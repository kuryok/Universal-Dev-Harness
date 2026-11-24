"""Smoke test oficial (B3.4) no perfil artifact (imagem artifact-only, UDS endurecido e reinício real)."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import shutil
from pathlib import Path

from .domain import CoreError, require_sha256, require_text
from .policy import policy_v2_sha256
from .preview import (
    ALLOWED_PROXY_METHODS,
    PreviewError,
    allocate_demo_data_dir,
    allocate_preview_ipc_dir,
    load_app_descriptor,
    uds_http_request,
    validate_http_path,
)
from .runtime import DEFAULT_ARTIFACT_LIMITS, ResourceLimits, RuntimeIsolationError, RuntimeManager
from .service import CoreService
from .transitions import ConflictError
from .trust_store import TrustStore, TrustStoreError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


@dataclass(frozen=True)
class SmokeCheck:
    """Passo declarativo de smoke test oriundo exclusivamente do domínio confiável (spec/Core)."""

    name: str
    method: str
    path: str
    expected_status: int
    body: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    expected_substring: str | None = None
    after_restart: bool = False

    def __post_init__(self):
        require_text(self.name, 'Nome do passo de smoke test')
        if not isinstance(self.method, str) or self.method.upper() not in ALLOWED_PROXY_METHODS:
            raise CoreError(f'Método HTTP inválido no smoke test: {self.method!r}.')
        validate_http_path(self.path, 'Caminho do smoke test')
        if type(self.expected_status) is not int or self.expected_status < 100 or self.expected_status > 599:
            raise CoreError('expected_status inválido no smoke test.')
        if self.body is not None and not isinstance(self.body, str):
            raise CoreError('body do smoke test deve ser texto ou None.')
        if self.expected_substring is not None and not isinstance(self.expected_substring, str):
            raise CoreError('expected_substring do smoke test deve ser texto ou None.')
        if not isinstance(self.after_restart, bool):
            raise CoreError('after_restart deve ser booleano.')
        for item in self.headers:
            if not isinstance(item, tuple) or len(item) != 2 or not all(isinstance(x, str) for x in item):
                raise CoreError('headers do smoke test devem ser tuplas (nome, valor).')

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'method': self.method.upper(),
            'path': self.path,
            'expected_status': self.expected_status,
            'body': self.body,
            'headers': [[k, v] for k, v in self.headers],
            'expected_substring': self.expected_substring,
            'after_restart': self.after_restart,
        }


def parse_trusted_smoke_checks(raw_checks: Sequence[SmokeCheck | Mapping]) -> tuple[SmokeCheck, ...]:
    """Valida fail-closed o contrato oficial de smoke_checks (exige len >= 1)."""
    if not isinstance(raw_checks, (list, tuple)) or not raw_checks:
        raise CoreError('SMOKE_SPEC_EMPTY: contrato oficial de smoke_checks deve conter ao menos 1 passo.')
    parsed: list[SmokeCheck] = []
    for item in raw_checks:
        if isinstance(item, SmokeCheck):
            parsed.append(item)
        elif isinstance(item, Mapping):
            raw_headers = item.get('headers', ())
            if isinstance(raw_headers, Mapping):
                headers_tuple = tuple((str(k), str(v)) for k, v in sorted(raw_headers.items()))
            elif isinstance(raw_headers, (list, tuple)):
                headers_tuple = tuple((str(pair[0]), str(pair[1])) for pair in raw_headers)
            else:
                raise CoreError('headers inválidos em smoke_checks.')
            parsed.append(SmokeCheck(
                name=item.get('name', ''),
                method=item.get('method', ''),
                path=item.get('path', ''),
                expected_status=item.get('expected_status'),
                body=item.get('body'),
                headers=headers_tuple,
                expected_substring=item.get('expected_substring'),
                after_restart=bool(item.get('after_restart', False)),
            ))
        else:
            raise CoreError('Entrada inválida em smoke_checks.')
    return tuple(parsed)


def smoke_spec_sha256(checks: Sequence[SmokeCheck]) -> str:
    canonical = json.dumps(
        [c.to_dict() for c in checks],
        sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


class SmokeRunner:
    """Executa smoke test oficial B3.4 no perfil artifact (imagem artifact-only, sem confiar em /artifact)."""

    def __init__(
        self,
        *,
        core_service: CoreService,
        trust_store: TrustStore,
        runtime_manager: RuntimeManager | None = None,
        require_isolated_netns: bool = True,
    ):
        self.core_service = core_service
        self.trust_store = trust_store
        self.runtime_manager = runtime_manager or RuntimeManager()
        self.require_isolated_netns = require_isolated_netns

    def run_smoke(
        self,
        run_id: str,
        *,
        request_id: str,
        artifact_digest: str,
        smoke_checks: Sequence[SmokeCheck | Mapping],
        commit_sha: str | None = None,
        limits: ResourceLimits | None = None,
        ready_timeout: float = 12.0,
    ) -> dict:
        run = self.core_service.show(run_id)
        if run.schema_version != 4:
            raise CoreError('SmokeRunner B3.4 exige banco SQLite v4.')
        if run.state != 'IN_PROGRESS':
            raise ConflictError(f'Execução deve estar IN_PROGRESS para smoke test oficial (atual: {run.state}).')
        if run.has_unknown_pendencies:
            raise ConflictError('Pendência UNKNOWN ativa impede smoke test oficial.')
        require_sha256(artifact_digest, 'Digest do artefato')

        checks = parse_trusted_smoke_checks(smoke_checks)
        spec_hash = smoke_spec_sha256(checks)
        manifest = self.runtime_manager.load_artifact_manifest()
        pol_sha = policy_v2_sha256()

        try:
            artifact_dir, _art_manifest = self.trust_store.verify_artifact(artifact_digest)
        except TrustStoreError as exc:
            return self._persist_report({
                'schema_version': 1,
                'run_id': run.run_id,
                'request_id': request_id,
                'kind': 'SMOKE_RESULT',
                'verdict': 'ERROR',
                'reason_code': 'ARTIFACT_INTEGRITY_VIOLATION',
                'detail': str(exc),
                'artifact_digest': artifact_digest,
                'commit_sha': commit_sha,
                'image_digest': manifest.image_digest,
                'preview_bridge_sha256': manifest.preview_bridge_sha256,
                'smoke_spec_sha256': spec_hash,
                'policy_sha256': pol_sha,
                'restart_verified': False,
                'steps': [],
                'collected_at': _utc_now(),
            })

        try:
            descriptor = load_app_descriptor(artifact_dir)
        except PreviewError as exc:
            return self._persist_report({
                'schema_version': 1,
                'run_id': run.run_id,
                'request_id': request_id,
                'kind': 'SMOKE_RESULT',
                'verdict': 'FAIL',
                'reason_code': exc.reason_code,
                'detail': str(exc),
                'artifact_digest': artifact_digest,
                'commit_sha': commit_sha,
                'image_digest': manifest.image_digest,
                'preview_bridge_sha256': manifest.preview_bridge_sha256,
                'smoke_spec_sha256': spec_hash,
                'policy_sha256': pol_sha,
                'restart_verified': False,
                'steps': [],
                'collected_at': _utc_now(),
            })

        pre_restart = [c for c in checks if not c.after_restart]
        post_restart = [c for c in checks if c.after_restart]

        data_dir = allocate_demo_data_dir(prefix='udh-smoke-data-')
        ipc_dirs: list[Path] = []
        step_results: list[dict] = []
        restart_verified = False
        verdict = 'PASS'
        reason_code = 'SMOKE_ALL_PASSED'
        detail = ''

        def _execute_phase(phase_name: str, phase_checks: list[SmokeCheck]) -> bool:
            nonlocal verdict, reason_code, detail
            ipc_dir = allocate_preview_ipc_dir(prefix='udh-smoke-ipc-')
            ipc_dirs.append(ipc_dir)
            proc = None
            try:
                proc = self.runtime_manager.start_artifact_preview(
                    artifact_dir=artifact_dir,
                    data_dir=data_dir,
                    ipc_dir=ipc_dir,
                    entrypoint=descriptor.entrypoint,
                    preview_mode='smoke',
                    limits=limits or DEFAULT_ARTIFACT_LIMITS,
                    image_ref=manifest.image_ref,
                    bridge_path=manifest.preview_bridge_path,
                    ready_timeout=ready_timeout,
                    forbidden_rw_roots=(self.trust_store.root,),
                )
                ready_resp = uds_http_request(
                    ipc_dir,
                    method='GET',
                    path=descriptor.ready_path,
                    timeout=min(ready_timeout, 10.0),
                    require_isolated_netns=self.require_isolated_netns,
                )
                ready_ok = 200 <= ready_resp.status < 400
                step_results.append({
                    'phase': phase_name,
                    'name': '__ready__',
                    'method': 'GET',
                    'path': descriptor.ready_path,
                    'status': ready_resp.status,
                    'ok': ready_ok,
                })
                if not ready_ok:
                    verdict = 'FAIL'
                    reason_code = 'SMOKE_READY_FAILED'
                    detail = f'Prontidão falhou em {descriptor.ready_path} (HTTP {ready_resp.status}).'
                    return False

                for check in phase_checks:
                    body_bytes = check.body.encode('utf-8') if check.body is not None else b''
                    resp = uds_http_request(
                        ipc_dir,
                        method=check.method,
                        path=check.path,
                        headers=check.headers,
                        body=body_bytes,
                        timeout=min(ready_timeout, 10.0),
                        require_isolated_netns=self.require_isolated_netns,
                    )
                    text_body = resp.body.decode('utf-8', errors='replace')
                    status_ok = resp.status == check.expected_status
                    sub_ok = (check.expected_substring is None) or (check.expected_substring in text_body)
                    ok = status_ok and sub_ok
                    step_results.append({
                        'phase': phase_name,
                        'name': check.name,
                        'method': check.method.upper(),
                        'path': check.path,
                        'expected_status': check.expected_status,
                        'actual_status': resp.status,
                        'substring_matched': sub_ok,
                        'ok': ok,
                    })
                    if not ok:
                        verdict = 'FAIL'
                        reason_code = 'SMOKE_CHECK_FAILED'
                        detail = f'Passo "{check.name}" falhou (status={resp.status}, esperado={check.expected_status}, sub={sub_ok}).'
                        return False
                return True
            except (RuntimeIsolationError, PreviewError, OSError) as exc:
                verdict = 'ERROR'
                reason_code = getattr(exc, 'reason_code', 'SMOKE_EXECUTION_ERROR')
                detail = str(exc)
                return False
            finally:
                if proc is not None:
                    proc.stop()

        try:
            phase1_ok = _execute_phase('initial', pre_restart)
            if phase1_ok and post_restart:
                phase2_ok = _execute_phase('after_restart', post_restart)
                if phase2_ok:
                    restart_verified = True
        finally:
            for d in ipc_dirs:
                shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(data_dir, ignore_errors=True)

        report = {
            'schema_version': 1,
            'run_id': run.run_id,
            'request_id': request_id,
            'kind': 'SMOKE_RESULT',
            'verdict': verdict,
            'reason_code': reason_code,
            'detail': detail,
            'artifact_digest': artifact_digest,
            'commit_sha': commit_sha,
            'image_digest': manifest.image_digest,
            'preview_bridge_sha256': manifest.preview_bridge_sha256,
            'smoke_spec_sha256': spec_hash,
            'policy_sha256': pol_sha,
            'docs_files_verified': list(descriptor.docs_files),
            'restart_verified': restart_verified,
            'steps': step_results,
            'collected_at': _utc_now(),
        }
        return self._persist_report(report)

    def _persist_report(self, report: dict) -> dict:
        evidence_path, evidence_sha256 = self.trust_store.save_evidence(
            report['run_id'], report['request_id'], report,
        )
        rel_ref = str(evidence_path.relative_to(self.trust_store.root))
        recorded = self.core_service.record_evidence(
            report['run_id'],
            report['request_id'],
            kind='SMOKE_RESULT',
            verdict=report['verdict'],
            artifact_digest=report['artifact_digest'],
            evidence_sha256=evidence_sha256,
            collector_route='TRUSTED_EXECUTOR',
            summary_ref=rel_ref,
            commit_sha=report.get('commit_sha'),
        )
        return {
            'evidence': recorded,
            'evidence_path': str(evidence_path),
            'report': report,
        }
