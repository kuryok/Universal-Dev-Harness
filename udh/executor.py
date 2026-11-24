"""Executor Confiável F-04: orquestra runner isolado no perfil artifact sem importar código do projeto."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import tempfile

from .domain import COMPLETION_EVIDENCE_KINDS, CoreError, require_sha1, require_sha256, strict_json_loads
from .policy import policy_v2_sha256
from .runtime import DEFAULT_ARTIFACT_LIMITS, ResourceLimits, RuntimeIsolationError, RuntimeManager
from .service import CoreService
from .ticket_contract import discover_at_tests as _discover_at_tests_core, verify_contract_seal
from .transitions import ConflictError
from .trust_store import TamperingError, TrustStore, TrustStoreError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class TrustedExecutor:
    """Executa ATs selados em perfil artifact isolado e registra evidências oficiais no SQLite v4."""

    def __init__(
        self,
        *,
        core_service: CoreService,
        trust_store: TrustStore,
        runtime_manager: RuntimeManager | None = None,
    ):
        self.core_service = core_service
        self.trust_store = trust_store
        self.runtime_manager = runtime_manager or RuntimeManager()

    def verify_run(
        self,
        run_id: str,
        *,
        request_id: str,
        artifact_digest: str,
        at_version: str,
        commit_sha: str | None = None,
        kind: str = 'AT_RESULT',
        limits: ResourceLimits | None = None,
        use_artifact_image: bool = False,
    ) -> dict:
        run = self.core_service.show(run_id)
        if run.schema_version != 4:
            raise CoreError('Executor Confiável F-04 exige banco SQLite v4.')
        if run.state != 'IN_PROGRESS':
            raise ConflictError(f'Execução deve estar IN_PROGRESS para verificação oficial (atual: {run.state}).')
        if run.has_unknown_pendencies:
            raise ConflictError('Pendência UNKNOWN ativa impede verificação oficial.')

        binding = self.core_service.managed_verification_contract(run_id, expected_revision=run.revision)
        report_binding = {}
        if binding is not None:
            if kind != 'AT_RESULT' or at_version != binding['contract']['at_version']:
                raise ConflictError('Verificação gerenciada exige AT_RESULT com a versão fixada no contrato.')
            require_sha1(commit_sha, 'Commit SHA do candidato')
            require_sha256(artifact_digest, 'Digest do artefato candidato')
            report_binding = {'contract_sha256': binding['contract_sha256'], 'expected_revision': run.revision}

        manifest = (
            self.runtime_manager.load_artifact_manifest()
            if use_artifact_image
            else self.runtime_manager.load_manifest()
        )
        pol_sha = policy_v2_sha256()

        # 1. Validação fail-closed do selo de AT na TrustStore + inventário AST independente no Core
        try:
            if binding is None:
                at_dir, _ = self.trust_store.verify_sealed_at(at_version)
                core_expected_tests = _discover_at_tests_core(at_dir)
            else:
                at_dir, core_expected_tests = verify_contract_seal(binding['contract'], self.trust_store)
            if not core_expected_tests:
                raise TrustStoreError('AT_INTEGRITY_VIOLATION: nenhum caso TestCase.test_* encontrado no selo.')
        except (TrustStoreError, SyntaxError, UnicodeDecodeError, OSError) as error:
            return self._record_failure(
                run_id=run_id, request_id=request_id, kind=kind, verdict='ERROR',
                reason_code='AT_INTEGRITY_VIOLATION', detail=str(error),
                artifact_digest=artifact_digest, at_version=at_version, commit_sha=commit_sha,
                runner_sha256=manifest.at_runner_sha256, image_digest=manifest.image_digest,
                policy_sha256=pol_sha, **report_binding,
            )

        # 2. Validação fail-closed do artefato promovido na TrustStore
        try:
            artifact_dir, art_manifest = self.trust_store.verify_artifact(artifact_digest)
        except TamperingError as error:
            return self._record_failure(
                run_id=run_id, request_id=request_id, kind=kind, verdict='FAIL',
                reason_code='RUNNER_TAMPERING_DETECTED', detail=str(error),
                artifact_digest=artifact_digest, at_version=at_version, commit_sha=commit_sha,
                runner_sha256=manifest.at_runner_sha256, image_digest=manifest.image_digest,
                policy_sha256=pol_sha, **report_binding,
            )
        except TrustStoreError as error:
            return self._record_failure(
                run_id=run_id, request_id=request_id, kind=kind, verdict='ERROR',
                reason_code='ARTIFACT_INTEGRITY_VIOLATION', detail=str(error),
                artifact_digest=artifact_digest, at_version=at_version, commit_sha=commit_sha,
                runner_sha256=manifest.at_runner_sha256, image_digest=manifest.image_digest,
                policy_sha256=pol_sha, **report_binding,
            )
        if binding is not None and art_manifest.get('commit_sha') != commit_sha:
            raise ConflictError('Commit do candidato diverge do artefato promovido.')

        # 3. Execução isolada no perfil artifact com nonce exclusivo via stdin
        nonce = secrets.token_hex(32)
        stdin_payload = json.dumps({'nonce': nonce}, separators=(',', ':')).encode('utf-8')
        with tempfile.TemporaryDirectory(prefix='udh-f04-work-') as work_tmp:
            work_dir = Path(work_tmp)
            try:
                container = self.runtime_manager.run_artifact(
                    command=['python3', '-I', '-B', manifest.at_runner_path, '--artifact', '/artifact', '--at-dir', '/sealed-at'],
                    artifact_dir=artifact_dir,
                    at_dir=at_dir,
                    work_dir=work_dir,
                    limits=limits or DEFAULT_ARTIFACT_LIMITS,
                    image_ref=manifest.image_ref,
                    stdin_bytes=stdin_payload,
                    forbidden_rw_roots=(self.trust_store.root,),
                )
            except RuntimeIsolationError as error:
                return self._record_failure(
                    run_id=run_id, request_id=request_id, kind=kind, verdict='ERROR',
                    reason_code='RUNTIME_EXECUTION_ERROR', detail=str(error),
                    artifact_digest=artifact_digest, at_version=at_version, commit_sha=commit_sha,
                    runner_sha256=manifest.at_runner_sha256, image_digest=manifest.image_digest,
                    policy_sha256=pol_sha, **report_binding,
                )

        # 4. Avaliação determinística no Core (nunca confia em saída livre nem arquivos do workspace)
        parsed = None
        stdout_lines = [line for line in container.stdout.splitlines() if line.strip()]
        if len(stdout_lines) == 1:
            try:
                candidate = strict_json_loads(stdout_lines[0], 'Envelope F-04')
                if isinstance(candidate, dict):
                    parsed = candidate
            except CoreError:
                parsed = None

        nonce_valid = (
            isinstance(parsed, dict)
            and parsed.get('udhf04') == 1
            and isinstance(parsed.get('nonce'), str)
            and hmac.compare_digest(parsed['nonce'], nonce)
        )

        verdict = 'FAIL'
        reason_code = 'AT_FAILED'
        tests_run = 0
        failures = 0
        errors = 0
        skipped = 0
        tampering_ok = False
        tampering_reason = None
        expected_tests: list[str] = []
        passed_tests: list[str] = []

        if container.outcome is not None:
            verdict = 'ERROR'
            reason_code = f'CONTAINER_{container.outcome}'
        elif not nonce_valid:
            verdict = 'FAIL'
            reason_code = 'INVALID_OR_FORGED_RUNNER_ENVELOPE'
        else:
            assert parsed is not None
            tampering_ok = parsed.get('tampering_ok') is True
            tampering_reason = parsed.get('tampering_reason')
            tests_run = parsed.get('tests_run') if type(parsed.get('tests_run')) is int else 0
            failures = parsed.get('failures') if type(parsed.get('failures')) is int else 1
            errors = parsed.get('errors') if type(parsed.get('errors')) is int else 1
            skipped = parsed.get('skipped') if type(parsed.get('skipped')) is int else 0
            raw_expected = parsed.get('expected_tests')
            raw_passed = parsed.get('passed_tests')
            expected_tests = (
                raw_expected
                if isinstance(raw_expected, list) and all(isinstance(x, str) for x in raw_expected)
                else []
            )
            passed_tests = (
                raw_passed
                if isinstance(raw_passed, list) and all(isinstance(x, str) for x in raw_passed)
                else []
            )

            coverage_ok = (
                bool(core_expected_tests)
                and set(expected_tests) == set(core_expected_tests)
                and set(passed_tests) == set(core_expected_tests)
                and len(passed_tests) == len(core_expected_tests)
                and tests_run == len(core_expected_tests)
            )
            if not tampering_ok:
                verdict = 'FAIL'
                reason_code = 'RUNNER_TAMPERING_DETECTED'
            elif (container.returncode == 0 and parsed.get('ok') is True and coverage_ok
                  and failures == 0 and errors == 0 and skipped == 0):
                verdict = 'PASS'
                reason_code = 'AT_ALL_PASSED'
            else:
                verdict = 'FAIL'
                reason_code = str(tampering_reason or 'AT_FAILED')

        report = {
            'schema_version': 1,
            'run_id': run_id,
            'request_id': request_id,
            'kind': kind,
            'verdict': verdict,
            'reason_code': reason_code,
            'artifact_digest': artifact_digest,
            'at_version': at_version,
            'commit_sha': commit_sha,
            'runner_sha256': manifest.at_runner_sha256,
            'image_digest': manifest.image_digest,
            'policy_sha256': pol_sha,
            'container_exit_code': container.returncode,
            'container_outcome': container.outcome,
            'nonce_verified': nonce_valid,
            'tampering_ok': tampering_ok,
            'tampering_reason': tampering_reason,
            'tests_run': tests_run,
            'failures': failures,
            'errors': errors,
            'skipped': skipped,
            'expected_tests': expected_tests,
            'passed_tests': passed_tests,
            'stdout_sha256': hashlib.sha256(container.stdout.encode('utf-8')).hexdigest(),
            'stderr_sha256': hashlib.sha256(container.stderr.encode('utf-8')).hexdigest(),
            'collected_at': _utc_now(),
        }
        return self._persist_report(report, **report_binding)

    def _record_failure(
        self,
        *,
        run_id: str,
        request_id: str,
        kind: str,
        verdict: str,
        reason_code: str,
        detail: str,
        artifact_digest: str,
        at_version: str,
        commit_sha: str | None,
        runner_sha256: str,
        image_digest: str,
        policy_sha256: str,
        contract_sha256: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        report = {
            'schema_version': 1,
            'run_id': run_id,
            'request_id': request_id,
            'kind': kind,
            'verdict': verdict,
            'reason_code': reason_code,
            'detail': detail,
            'artifact_digest': artifact_digest,
            'at_version': at_version,
            'commit_sha': commit_sha,
            'runner_sha256': runner_sha256,
            'image_digest': image_digest,
            'policy_sha256': policy_sha256,
            'container_exit_code': None,
            'container_outcome': None,
            'nonce_verified': False,
            'tampering_ok': False,
            'tests_run': 0,
            'failures': 1 if verdict == 'FAIL' else 0,
            'errors': 1 if verdict == 'ERROR' else 0,
            'skipped': 0,
            'expected_tests': [],
            'passed_tests': [],
            'collected_at': _utc_now(),
        }
        return self._persist_report(report, contract_sha256=contract_sha256, expected_revision=expected_revision)

    def _persist_report(self, report: dict, *, contract_sha256=None, expected_revision=None) -> dict:
        if contract_sha256 is not None:
            report['contract_sha256'] = contract_sha256
            recorded, evidence_path = self.core_service.record_managed_at_report(
                report, expected_revision=expected_revision, trust_store=self.trust_store,
            )
            return {'evidence': recorded, 'evidence_path': evidence_path, 'report': report}

        evidence_path, evidence_sha256 = self.trust_store.save_evidence(
            report['run_id'], report['request_id'], report,
        )
        rel_ref = str(evidence_path.relative_to(self.trust_store.root))
        recorded = self.core_service.record_evidence(
            report['run_id'],
            report['request_id'],
            kind=report['kind'],
            verdict=report['verdict'],
            artifact_digest=report['artifact_digest'],
            evidence_sha256=evidence_sha256,
            collector_route='TRUSTED_EXECUTOR',
            summary_ref=rel_ref,
            at_version=report['at_version'],
            commit_sha=report['commit_sha'],
        )
        return {
            'evidence': recorded,
            'evidence_path': str(evidence_path),
            'report': report,
        }

    def complete_verified_run(
        self,
        run_id: str,
        *,
        expected_revision: int,
        request_id: str,
        evidence_id: str,
        artifact_digest: str,
        at_version: str,
    ) -> dict:
        """Conclui a execução exigindo que evidence_id seja a evidência mais recente para (artifact_digest, at_version)."""
        run = self.core_service.show(run_id)
        matching = [
            ev for ev in run.evidence_refs
            if ev.kind in COMPLETION_EVIDENCE_KINDS
            and ev.artifact_digest == artifact_digest
            and ev.at_version == at_version
        ]
        if not matching:
            raise ConflictError('Nenhuma evidência verificadora registrada para este artefato e versão de AT.')
        latest = matching[-1]
        if (latest.evidence_id != evidence_id
                or latest.verdict != 'PASS'
                or latest.collector_route != 'TRUSTED_EXECUTOR'):
            raise ConflictError(
                'Conclusão exige a evidência oficial mais recente de TRUSTED_EXECUTOR com veredito PASS para (artifact_digest, at_version).'
            )
        return self.core_service.complete(
            run_id, expected_revision, request_id, evidence_id, actor_route='TRUSTED_EXECUTOR',
        )
