"""Casos de uso do Core (v1–v4); identidade e validação antes da persistência."""
from datetime import datetime, timezone
from uuid import uuid4

from .doctor import run_doctor
from .domain import CoreError, Run, VALID_MODES, require_canonical_uuid, require_run_id, require_text
from .policy import evaluate
from .store import SQLiteStore
from .transitions import (
    AuthorizationError,
    BlockRequest,
    BlockV4Request,
    CheckpointRequest,
    CompleteRequest,
    EvidenceRequest,
    StartRequest,
    UnblockRequest,
    UnblockV4Request,
    operator_context_valid,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class CoreService:
    def __init__(self, store: SQLiteStore):
        self.store = store

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

    def policy(self, run_id: str, action: str, *, preflight_status: str | None = None) -> dict:
        require_text(action, 'Ação da política')
        return evaluate(self.show(run_id), action, preflight_status=preflight_status)

    def migrate(self, target=2, *, context=None) -> dict:
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
              *, actor_route: str = 'CORE_LOCAL') -> dict:
        request = StartRequest(require_canonical_uuid(run_id, 'Identificador da execução'),
                               expected_revision, request_id, actor_route=actor_route)
        return self.store.start_v4(request, created_at=_utc_now())

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
    def doctor(*, db_path=None, env_probes=None) -> dict:
        return run_doctor(db_path=db_path, env_probes=env_probes)
