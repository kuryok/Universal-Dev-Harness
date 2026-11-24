"""Casos de uso do Core; identidade e validação antes da persistência."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

from .doctor import run_doctor
from .discovery_flow import DiscoveryFlow
from .domain import (
    COMPLETION_EVIDENCE_KINDS,
    CoreError,
    EvidenceRef,
    Run,
    VALID_MODES,
    require_canonical_uuid,
    require_run_id,
    require_text,
    strict_json_loads,
)
from .policy import evaluate
from .store import SQLiteStore
from .ticket_store import TicketStore
from .transitions import (
    AuthorizationError,
    BlockRequest,
    BlockV4Request,
    CheckpointRequest,
    CompleteRequest,
    ConflictError,
    EvidenceRequest,
    StartRequest,
    UnblockRequest,
    UnblockV4Request,
    operator_context_valid,
)
from .trust_store import TrustStore, TrustStoreError, _read_regular_nofollow


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class CoreService:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self._tickets = TicketStore(store)

    def initialize(self, *, schema_version: int = 1) -> int:
        return self.store.initialize(schema_version=schema_version)

    def create(self, spec_id: str, spec_version: str, request_id: str,
               *, ticket_id: str | None = None, mode: str | None = None) -> Run:
        require_text(spec_id, 'Identificador da especificação')
        require_text(spec_version, 'Versão da especificação')
        require_text(request_id, 'Identificador da requisição')
        if ticket_id is not None:
            require_text(ticket_id, 'Identificador do ticket')
        if mode is not None and mode not in VALID_MODES:
            raise CoreError('Modo adaptativo inválido; use FAST, STANDARD ou DEEP.')
        return self.store.create(
            run_id=str(uuid4()), request_id=request_id,
            spec_id=spec_id, spec_version=spec_version,
            created_at=_utc_now(),
            ticket_id=ticket_id, mode=mode,
        )

    def show(self, run_id: str) -> Run:
        return self.store.get(require_run_id(run_id))

    def list_runs(self, limit: int = 100) -> list[Run]:
        return self.store.list_runs(limit=limit)

    def count_runs_by_state(self) -> dict[str, int]:
        return self.store.count_runs_by_state()

    def promote_run_artifact(
        self,
        run_id: str,
        request_id: str,
        source_dir: Path | str,
        *,
        trust_store: TrustStore,
        stack_profile: str = 'python3-stdlib-web-v1',
        commit_sha: str | None = None,
    ) -> dict:
        """Promove artefato imutável na TrustStore e registra ARTIFACT_PROMOTION oficial no SQLite v4."""
        run = self.show(run_id)
        if run.schema_version != 4:
            raise CoreError('Promoção oficial de artefato exige banco SQLite v4.')
        if run.state != 'IN_PROGRESS':
            raise ConflictError(f'Execução deve estar IN_PROGRESS para promover artefato (atual: {run.state}).')
        if run.has_unknown_pendencies:
            raise ConflictError('Pendência UNKNOWN ativa impede promoção oficial de artefato.')

        art_manifest = trust_store.promote_artifact(
            Path(source_dir),
            stack_profile=stack_profile,
            commit_sha=commit_sha,
        )
        report = {
            'schema_version': 1,
            'run_id': run.run_id,
            'request_id': request_id,
            'kind': 'ARTIFACT_PROMOTION',
            'verdict': 'PASS',
            'artifact_digest': art_manifest['artifact_digest'],
            'stack_profile': art_manifest['stack_profile'],
            'commit_sha': art_manifest.get('commit_sha'),
            'files_count': len(art_manifest.get('files', {})),
            'collected_at': _utc_now(),
        }
        evidence_path, evidence_sha256 = trust_store.save_evidence(run.run_id, request_id, report)
        rel_ref = str(evidence_path.relative_to(trust_store.root))
        recorded = self.record_evidence(
            run.run_id,
            request_id,
            kind='ARTIFACT_PROMOTION',
            verdict='PASS',
            artifact_digest=art_manifest['artifact_digest'],
            evidence_sha256=evidence_sha256,
            collector_route='TRUSTED_EXECUTOR',
            summary_ref=rel_ref,
            commit_sha=commit_sha,
        )
        return {
            'artifact_manifest': art_manifest,
            'evidence': recorded,
            'evidence_path': str(evidence_path),
            'report': report,
        }

    @staticmethod
    def _verify_trusted_evidence_file(trust_store: TrustStore, ev: EvidenceRef) -> dict | None:
        if ev.collector_route != 'TRUSTED_EXECUTOR':
            return None
        ref = Path(ev.summary_ref)
        if ref.is_absolute() or '..' in ref.parts:
            return None
        expected_dir = (trust_store.root / 'evidence' / ev.run_id).resolve()
        candidate = trust_store.root / ref
        try:
            if candidate.is_symlink() or candidate.parent.resolve(strict=True) != expected_dir:
                return None
            raw = _read_regular_nofollow(candidate)
        except (OSError, TrustStoreError):
            return None
        if hashlib.sha256(raw).hexdigest() != ev.evidence_sha256:
            return None
        try:
            parsed = strict_json_loads(raw.decode('utf-8'), 'Relatório de evidência')
        except (CoreError, UnicodeDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        if (parsed.get('run_id') != ev.run_id
                or parsed.get('kind') != ev.kind
                or parsed.get('verdict') != ev.verdict
                or parsed.get('artifact_digest') != ev.artifact_digest):
            return None
        return parsed

    def delivery_summary(self, run_id: str, *, trust_store: TrustStore) -> dict:
        """Deriva estágio ADR-028 exclusivamente do SQLite v4 + verificação física na TrustStore."""
        run = self.show(run_id)
        ordered = sorted(run.evidence_refs, key=lambda e: (e.created_at, e.evidence_id))
        trusted_promos = [
            ev for ev in ordered
            if ev.kind == 'ARTIFACT_PROMOTION' and ev.collector_route == 'TRUSTED_EXECUTOR'
        ]

        current_promo: EvidenceRef | None = None
        current_digest: str | None = None
        artifact_intact = False
        if trusted_promos:
            candidate_promo = trusted_promos[-1]
            promo_report = self._verify_trusted_evidence_file(trust_store, candidate_promo)
            if promo_report is not None and candidate_promo.verdict == 'PASS':
                try:
                    trust_store.verify_artifact(candidate_promo.artifact_digest)
                    artifact_intact = True
                    current_promo = candidate_promo
                    current_digest = candidate_promo.artifact_digest
                except TrustStoreError:
                    artifact_intact = False

        stage = 'PENDING'
        latest_at_info: dict | None = None
        latest_smoke_info: dict | None = None
        at_passed = False
        smoke_passed = False
        has_stale_evidences = False

        if current_digest is not None and artifact_intact:
            stage = 'IMPLEMENTED'
            at_for_digest = [
                ev for ev in ordered
                if ev.kind == 'AT_RESULT'
                and ev.collector_route == 'TRUSTED_EXECUTOR'
                and ev.artifact_digest == current_digest
            ]
            smoke_for_digest = [
                ev for ev in ordered
                if ev.kind == 'SMOKE_RESULT'
                and ev.collector_route == 'TRUSTED_EXECUTOR'
                and ev.artifact_digest == current_digest
            ]
            older_evidences = [
                ev for ev in ordered
                if ev.kind in ('AT_RESULT', 'SMOKE_RESULT')
                and ev.artifact_digest != current_digest
            ]
            has_stale_evidences = bool(older_evidences)

            if at_for_digest:
                latest_at = at_for_digest[-1]
                at_report = self._verify_trusted_evidence_file(trust_store, latest_at)
                at_verified = at_report is not None and latest_at.verdict == 'PASS'
                latest_at_info = {
                    'evidence_id': latest_at.evidence_id,
                    'verdict': latest_at.verdict,
                    'verified': at_report is not None,
                    'at_version': latest_at.at_version,
                    'artifact_digest': latest_at.artifact_digest,
                    'created_at': latest_at.created_at,
                }
                if at_verified:
                    at_passed = True
                    stage = 'TESTED'

            if smoke_for_digest:
                latest_smoke = smoke_for_digest[-1]
                smoke_report = self._verify_trusted_evidence_file(trust_store, latest_smoke)
                smoke_verified = smoke_report is not None and latest_smoke.verdict == 'PASS'
                latest_smoke_info = {
                    'evidence_id': latest_smoke.evidence_id,
                    'verdict': latest_smoke.verdict,
                    'verified': smoke_report is not None,
                    'restart_verified': bool(smoke_report.get('restart_verified')) if smoke_report else False,
                    'artifact_digest': latest_smoke.artifact_digest,
                    'created_at': latest_smoke.created_at,
                }
                if at_passed and smoke_verified:
                    smoke_passed = True
                    stage = 'ACCEPTED'

            if stage == 'ACCEPTED' and run.state == 'DONE' and not run.has_unknown_pendencies:
                complete_events = [
                    ev for ev in run.events
                    if getattr(ev, 'operation', None) == 'COMPLETE_RUN'
                ]
                if complete_events:
                    comp_ev_id = getattr(complete_events[-1], 'evidence_id', None)
                    ev_by_id = {ev.evidence_id: ev for ev in ordered}
                    linked = ev_by_id.get(comp_ev_id) if comp_ev_id else None
                    if (linked is not None
                            and linked.kind in COMPLETION_EVIDENCE_KINDS
                            and linked.artifact_digest == current_digest
                            and linked.verdict == 'PASS'
                            and self._verify_trusted_evidence_file(trust_store, linked) is not None):
                        stage = 'DELIVERED'

        can_deliver = (
            stage == 'ACCEPTED'
            and run.state == 'IN_PROGRESS'
            and not run.has_unknown_pendencies
            and latest_smoke_info is not None
            and latest_smoke_info['verdict'] == 'PASS'
            and latest_smoke_info['verified']
        )

        return {
            'run_id': run.run_id,
            'run_state': run.state,
            'run_revision': run.revision,
            'delivery_stage': stage,
            'current_artifact_digest': current_digest,
            'artifact_intact': artifact_intact,
            'latest_promotion_evidence_id': current_promo.evidence_id if current_promo else None,
            'latest_at': latest_at_info,
            'latest_smoke': latest_smoke_info,
            'at_passed': at_passed,
            'smoke_passed': smoke_passed,
            'has_stale_evidences': has_stale_evidences,
            'has_unknown_pendencies': run.has_unknown_pendencies,
            'can_deliver': can_deliver,
            'environment_verified': 'linux-x86_64-podman-rootless-offline',
            'delivery_mode': 'preview-protected-demo',
            'unverified_targets': ['wsl2', 'external_services_b3_5'],
        }

    def deliver_run(
        self,
        run_id: str,
        expected_revision: int,
        request_id: str,
        *,
        trust_store: TrustStore,
    ) -> dict:
        """Conclui IN_PROGRESS -> DONE (DELIVERED) exigindo AT_RESULT=PASS + SMOKE_RESULT=PASS no mesmo digest."""
        summary = self.delivery_summary(run_id, trust_store=trust_store)
        if summary['run_state'] != 'IN_PROGRESS':
            raise ConflictError(
                f'Entrega exige execução IN_PROGRESS (estado atual: {summary["run_state"]}).'
            )
        if summary['has_unknown_pendencies']:
            raise ConflictError('Pendência UNKNOWN ativa impede conclusão da entrega.')
        if not summary['can_deliver']:
            raise ConflictError(
                f'DELIVERY_PRECONDITION_FAILED: entrega exige AT_RESULT=PASS e SMOKE_RESULT=PASS mais recentes '
                f'no mesmo artifact_digest íntegro (estágio atual: {summary["delivery_stage"]}).'
            )
        smoke_ev_id = summary['latest_smoke']['evidence_id']
        completion = self.complete(
            run_id,
            expected_revision,
            request_id,
            smoke_ev_id,
            actor_route='TRUSTED_EXECUTOR',
        )
        updated = self.delivery_summary(run_id, trust_store=trust_store)
        return {'completion': completion, 'delivery': updated}

    def policy(
        self,
        run_id: str,
        action: str,
        *,
        preflight_status: str | None = None,
        conformant_adapters: tuple[str, ...] | list[str] = (),
        adapter: str = 'pi',
    ) -> dict:
        require_text(action, 'Ação da política')
        return evaluate(
            self.show(run_id),
            action,
            preflight_status=preflight_status,
            conformant_adapters=conformant_adapters,
            adapter=adapter,
        )

    def authorize_agent_execution(
        self,
        run_id: str,
        *,
        store_dir=None,
        adapter: str = 'pi',
        env_probes=None,
        manifest_path=None,
        opencode_manifest_path=None,
        adapters=None,
    ) -> dict:
        report = self.doctor(
            store_dir=store_dir,
            env_probes=env_probes,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
            adapters=adapters,
        )
        rt_check = next((c for c in report['checks'] if c['id'] == 'runtime_components'), {})
        conformant = tuple(rt_check.get('details', {}).get('conformant_adapters', ()))
        return self.policy(
            run_id,
            'EXECUTE_AGENT',
            preflight_status=report['overall_status'],
            conformant_adapters=conformant,
            adapter=adapter,
        )

    def select_conformant_adapter(
        self,
        *,
        store_dir=None,
        preference_order=('pi', 'opencode'),
        requested: str | None = None,
        exclude=(),
        env_probes=None,
        manifest_path=None,
        opencode_manifest_path=None,
        adapters=None,
    ) -> dict:
        from .handoff import HandoffError, select_adapter

        report = self.doctor(
            store_dir=store_dir,
            env_probes=env_probes,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
            adapters=adapters,
        )
        if report['overall_status'] == 'NOT_READY':
            raise HandoffError(
                'Preflight retornou NOT_READY; seleção de adaptador bloqueada (fail-closed).',
                reason_code='NO_CONFORMANT_ADAPTER',
            )
        rt_check = next((c for c in report['checks'] if c['id'] == 'runtime_components'), {})
        conformant = tuple(rt_check.get('details', {}).get('conformant_adapters', ()))
        return select_adapter(
            conformant,
            preference_order=preference_order,
            requested=requested,
            exclude=exclude,
        )

    def migrate(self, target=2, *, context=None) -> dict:
        if type(target) is int and target == 9:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v9.')
            return self.store.migrate_v9()
        if type(target) is int and target == 8:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v8.')
            return self.store.migrate_v8()
        if type(target) is int and target == 7:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v7.')
            return self.store.migrate_v7()
        if type(target) is int and target == 6:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v6.')
            return self.store.migrate_v6()
        if type(target) is int and target == 5:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v5.')
            return self.store.migrate_v5()
        if type(target) is int and target == 4:
            if context is not None:
                raise AuthorizationError('Contexto administrativo legado não autoriza migração para v4.')
            return self.store.migrate_v4()
        if type(target) is int and target == 3:
            if not operator_context_valid(context) or 'MIGRATE_V3' not in context['allowed_operations']:
                raise AuthorizationError('Operação exige a entrada administrativa protegida.')
            return self.store.migrate_v3()
        if type(target) is int and target == 2:
            if context is not None and (not operator_context_valid(context)
                                        or 'MIGRATE_V2' not in context['allowed_operations']):
                raise AuthorizationError('Operação exige a entrada administrativa protegida.')
            return self.store.migrate()
        raise CoreError('Versão de migração inválida.')

    def discovery_flow(self, config) -> DiscoveryFlow:
        """Acesso ao fluxo de descoberta pela rota local confiável."""
        return DiscoveryFlow(self.store, config)

    def approve_ticket_input(self, spec, *, expected=None):
        """Fixa a entrada aprovada fornecida pelo chamador confiável."""
        return self._tickets.approve(spec, expected=expected)

    def materialize_ticket_plan(self, ref, *, expected_revision, proposal=None):
        return self._tickets.materialize(ref, expected_revision=expected_revision, proposal=proposal)

    def reconcile_ticket_plan(self, ref, *, expected_revision, plan_sha256):
        return self._tickets.reconcile(ref, expected_revision=expected_revision, plan_sha256=plan_sha256)

    def get_ticket_plan(self, ref):
        return self._tickets.get_plan(ref)

    def get_ticket_plan_head(self, change_id):
        return self._tickets.get_head(change_id)

    def create_managed_ticket_run(self, ref, *, approval_revision, plan_sha256, ticket_id, request_id, mode) -> Run:
        """Cria a execução vinculada por solicitação do chamador confiável do Core."""
        return self._tickets.create_managed_run(
            ref, approval_revision=approval_revision, plan_sha256=plan_sha256, ticket_id=ticket_id,
            request_id=request_id, mode=mode, run_id=str(uuid4()), created_at=_utc_now(),
        )

    def register_managed_ticket_contract(self, run_id, *, expected_revision, contract, trust_store: TrustStore) -> dict:
        """Registra a cobertura fornecida pelo chamador confiável após aprovação humana."""
        return self._tickets.register_contract(
            run_id, expected_revision=expected_revision, contract=contract, trust_store=trust_store,
        )

    def get_managed_ticket_contract(self, run_id) -> dict | None:
        """Consulta o registro histórico sem autorizar a execução."""
        return self._tickets.get_contract(run_id)

    def managed_verification_contract(self, run_id, *, expected_revision) -> dict | None:
        return self._tickets.verification_contract(run_id, expected_revision=expected_revision)

    def record_managed_at_report(self, report, *, expected_revision, trust_store: TrustStore) -> tuple[dict, str]:
        """Entrada local do executor; o escritor normal não registra AT gerenciado."""
        return self.store.record_managed_at_report(
            report, expected_revision=expected_revision, trust_store=trust_store,
            evidence_id=str(uuid4()), created_at=_utc_now(),
        )

    def record_managed_ticket_review(self, run_id, *, expected_revision, request_id, at_evidence_id,
                                     independent_context, review_context_ref, confirmation_ref, findings,
                                     trust_store: TrustStore) -> dict:
        """Registra a confirmação de revisão recebida por um chamador local confiável."""
        return self._tickets.record_review(
            run_id, expected_revision=expected_revision, request_id=request_id, at_evidence_id=at_evidence_id,
            independent_context=independent_context, review_context_ref=review_context_ref,
            confirmation_ref=confirmation_ref, findings=findings, trust_store=trust_store,
            evidence_id=str(uuid4()), created_at=_utc_now(),
        )

    def accept_managed_ticket(self, run_id, *, expected_revision, request_id, at_evidence_id, review_evidence_id,
                              trust_store: TrustStore) -> dict:
        """Registra o aceite oficial sem alterar o estado da execução."""
        return self._tickets.accept(
            run_id, expected_revision=expected_revision, request_id=request_id, at_evidence_id=at_evidence_id,
            review_evidence_id=review_evidence_id, trust_store=trust_store, created_at=_utc_now(),
        )

    def block(self, run_id, expected_revision, request_id, reason, *, context):
        if not operator_context_valid(context) or 'BLOCK_PENDING' not in context['allowed_operations']:
            raise AuthorizationError('Operação exige a entrada administrativa protegida.')
        request = BlockRequest(require_run_id(run_id), expected_revision, request_id, reason)
        return self.store.block(request, created_at=_utc_now(),
                                authorization_id=str(uuid4()), context=context)

    def unblock(self, run_id, expected_revision, request_id, reason, *, context):
        if not operator_context_valid(context) or 'UNBLOCK_RUN' not in context['allowed_operations']:
            raise AuthorizationError('Operação exige a entrada administrativa protegida.')
        request = UnblockRequest(require_run_id(run_id), expected_revision, request_id, reason)
        return self.store.unblock(request, created_at=_utc_now(),
                                  authorization_id=str(uuid4()), context=context)

    def start(self, run_id: str, expected_revision: int, request_id: str,
              *, actor_route: str = 'CORE_LOCAL', trust_store: TrustStore | None = None) -> dict:
        """A TrustStore para início gerenciado vem apenas do chamador local confiável."""
        request = StartRequest(require_canonical_uuid(run_id, 'Identificador da execução'),
                               expected_revision, request_id, actor_route=actor_route)
        return self.store.start_v4(request, created_at=_utc_now(), trust_store=trust_store)

    def block_v4(self, run_id: str, expected_revision: int, request_id: str,
                 *, category: str, reason_code: str, reason: str, next_action: str,
                 actor_route: str = 'OPERATOR_LOCAL') -> dict:
        request = BlockV4Request(
            require_canonical_uuid(run_id, 'Identificador da execução'),
            expected_revision, request_id,
            category=category, reason_code=reason_code,
            reason=reason, next_action=next_action, actor_route=actor_route,
        )
        return self.store.block_v4(request, created_at=_utc_now(), authorization_id=str(uuid4()))

    def unblock_v4(self, run_id: str, expected_revision: int, request_id: str, reason: str,
                   *, actor_route: str = 'OPERATOR_LOCAL') -> dict:
        request = UnblockV4Request(
            require_canonical_uuid(run_id, 'Identificador da execução'),
            expected_revision, request_id, reason=reason, actor_route=actor_route,
        )
        return self.store.unblock_v4(request, created_at=_utc_now(), authorization_id=str(uuid4()))

    def record_evidence(self, run_id: str, request_id: str, *, kind: str, verdict: str,
                        artifact_digest: str, evidence_sha256: str, collector_route: str,
                        summary_ref: str, at_version: str | None = None,
                        commit_sha: str | None = None) -> dict:
        request = EvidenceRequest(
            run_id=require_canonical_uuid(run_id, 'Identificador da execução'),
            request_id=request_id, kind=kind, verdict=verdict,
            artifact_digest=artifact_digest, evidence_sha256=evidence_sha256,
            collector_route=collector_route, summary_ref=summary_ref,
            at_version=at_version, commit_sha=commit_sha,
        )
        return self.store.record_evidence_v4(request, evidence_id=str(uuid4()), created_at=_utc_now())

    def complete(self, run_id: str, expected_revision: int, request_id: str, evidence_id: str,
                 *, actor_route: str = 'TRUSTED_EXECUTOR') -> dict:
        request = CompleteRequest(
            require_canonical_uuid(run_id, 'Identificador da execução'),
            expected_revision, request_id,
            require_canonical_uuid(evidence_id, 'Identificador da evidência'),
            actor_route=actor_route,
        )
        return self.store.complete_v4(request, created_at=_utc_now(), authorization_id=str(uuid4()))

    def record_checkpoint(self, run_id: str, expected_revision: int, request_id: str,
                          last_confirmed_step: str, *, base_commit_sha: str | None = None,
                          head_commit_sha: str | None = None,
                          worktree_snapshot_sha256: str | None = None,
                          evidence_hashes: tuple | list = (),
                          external_idempotency_keys: tuple | list = (),
                          unknown_pendencies: tuple | list = ()) -> dict:
        for value, label in (
            (evidence_hashes, 'evidence_hashes'),
            (external_idempotency_keys, 'external_idempotency_keys'),
            (unknown_pendencies, 'unknown_pendencies'),
        ):
            if not isinstance(value, (list, tuple)):
                raise CoreError(f'{label} deve ser uma lista ou tupla.')
        request = CheckpointRequest(
            run_id=require_canonical_uuid(run_id, 'Identificador da execução'),
            expected_revision=expected_revision, request_id=request_id,
            last_confirmed_step=last_confirmed_step,
            base_commit_sha=base_commit_sha, head_commit_sha=head_commit_sha,
            worktree_snapshot_sha256=worktree_snapshot_sha256,
            evidence_hashes=tuple(evidence_hashes),
            external_idempotency_keys=tuple(external_idempotency_keys),
            unknown_pendencies=tuple(unknown_pendencies),
        )
        return self.store.record_checkpoint_v4(request, checkpoint_id=str(uuid4()), created_at=_utc_now())

    @staticmethod
    def doctor(
        *,
        db_path=None,
        env_probes=None,
        store_dir=None,
        run_conformance: bool = False,
        manifest_path=None,
        opencode_manifest_path=None,
        adapters=None,
    ) -> dict:
        return run_doctor(
            db_path=db_path,
            env_probes=env_probes,
            store_dir=store_dir,
            run_conformance=run_conformance,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
            adapters=adapters,
        )
