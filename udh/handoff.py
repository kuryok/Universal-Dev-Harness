"""Seleção determinística de adaptadores (ADR-026) e Handoff F-03 fail-closed com git_dir externo."""
from collections.abc import Sequence
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from .domain import (
    CoreError,
    Run,
    require_canonical_uuid,
    require_sha1,
    require_sha256,
    require_text,
    strict_json_loads,
)
from .service import CoreService
from .trust_store import (
    FORBIDDEN_SHADOW_NAMES,
    TrustStore,
    _canonical_json_bytes,
    _read_regular_nofollow,
)

CANONICAL_ADAPTER_ORDER = ('pi', 'opencode')
HANDOFF_SCHEMA_VERSION = 1
FORBIDDEN_GIT_WORKTREE_NAMES = frozenset({'.git', '.gitmodules', '.gitattributes'})
FORBIDDEN_CONVERSATIONAL_KEYS = frozenset({
    'messages',
    'transcript',
    'chat_history',
    'conversation',
    'assistant_text',
    'raw_stdout',
    'raw_stderr',
    'stdout',
    'stderr',
    'agent_output',
})


class HandoffError(CoreError):
    """Violação de integridade, autorização ou protocolo de handoff F-03."""

    def __init__(self, message: str, *, reason_code: str = 'HANDOFF_INTEGRITY_VIOLATION'):
        super().__init__(message)
        self.reason_code = reason_code


def select_adapter(
    conformant_adapters: Sequence[str],
    *,
    preference_order: Sequence[str] = CANONICAL_ADAPTER_ORDER,
    requested: str | None = None,
    exclude: Sequence[str] = (),
) -> dict:
    """Seleciona deterministicamente um adaptador conforme (ADR-026), sem relaxar conformidade."""
    conformant_set = {str(a) for a in conformant_adapters if isinstance(a, str) and a.strip()}
    exclude_set = {str(a) for a in exclude if isinstance(a, str) and a.strip()}
    ordered_pref = [str(a) for a in preference_order if isinstance(a, str) and a.strip()]

    candidates: list[str] = []
    for name in ordered_pref:
        if name in conformant_set and name not in exclude_set and name not in candidates:
            candidates.append(name)
    for name in sorted(conformant_set):
        if name not in exclude_set and name not in candidates:
            candidates.append(name)

    if requested is not None:
        require_text(requested, 'Adaptador solicitado')
        if requested in conformant_set and requested not in exclude_set:
            return {
                'selected_adapter': requested,
                'reason_code': 'ADAPTER_REQUESTED_SELECTED',
                'candidates': candidates,
                'fallback_used': False,
            }
        raise HandoffError(
            f'Adaptador solicitado "{requested}" não está conforme ou foi excluído.',
            reason_code='REQUESTED_ADAPTER_NOT_CONFORMANT',
        )

    if not candidates:
        raise HandoffError(
            'Nenhum adaptador conforme elegível disponível para seleção.',
            reason_code='NO_CONFORMANT_ADAPTER',
        )

    chosen = candidates[0]
    fallback_used = bool(ordered_pref) and (chosen != ordered_pref[0])
    return {
        'selected_adapter': chosen,
        'reason_code': 'ADAPTER_FALLBACK_SELECTED' if fallback_used else 'ADAPTER_PRIMARY_SELECTED',
        'candidates': candidates,
        'fallback_used': fallback_used,
    }


def _sanitized_git_env() -> dict[str, str]:
    return {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_AUTHOR_NAME': 'UDH Core',
        'GIT_AUTHOR_EMAIL': 'core@udh.local',
        'GIT_COMMITTER_NAME': 'UDH Core',
        'GIT_COMMITTER_EMAIL': 'core@udh.local',
        'GIT_AUTHOR_DATE': '2026-01-01T00:00:00Z',
        'GIT_COMMITTER_DATE': '2026-01-01T00:00:00Z',
    }


def _ensure_external_git_dir(git_dir: Path, workspace: Path) -> tuple[Path, Path]:
    ws_resolved = Path(workspace).resolve(strict=True)
    if not ws_resolved.is_dir():
        raise HandoffError('Workspace de handoff deve ser um diretório existente.')
    git_resolved = Path(git_dir).resolve()
    if git_resolved == ws_resolved or git_resolved.is_relative_to(ws_resolved) or ws_resolved.is_relative_to(git_resolved):
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: git_dir do Core deve residir estritamente fora do workspace do agente.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return git_resolved, ws_resolved


def _run_git_core(
    git_dir: Path,
    workspace: Path,
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    git_resolved, ws_resolved = _ensure_external_git_dir(git_dir, workspace)
    cmd = [
        'git',
        '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false',
        '-c', 'protocol.file.allow=never',
        '-c', 'core.symlinks=false',
        '-c', 'core.quotepath=false',
        '--git-dir', str(git_resolved),
        '--work-tree', str(ws_resolved),
        *args,
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(ws_resolved),
        env=_sanitized_git_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if check and completed.returncode != 0:
        raise HandoffError(
            f'HANDOFF_INTEGRITY_VIOLATION: falha em comando git do Core (rc={completed.returncode}): '
            f'{(completed.stderr or completed.stdout).strip()[:300]}',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return completed


def _is_forbidden_shadow_relpath(rel_posix: str) -> bool:
    parts = Path(rel_posix).parts
    if not parts:
        return True
    for part in parts:
        lowered = part.lower()
        if lowered in FORBIDDEN_GIT_WORKTREE_NAMES:
            return True
        if lowered.endswith('.pth'):
            return True
        if lowered in FORBIDDEN_SHADOW_NAMES:
            return True
        if lowered.endswith('.py') and lowered[:-3] in FORBIDDEN_SHADOW_NAMES:
            return True
    return False


def scan_worktree_physical(workspace: Path) -> dict[str, str]:
    """Varre fisicamente o worktree com O_NOFOLLOW rejeitando .git, symlinks, especiais e sombreamentos."""
    ws_resolved = Path(workspace).resolve(strict=True)
    if not ws_resolved.is_dir():
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: workspace inexistente.')
    file_hashes: dict[str, str] = {}

    for current, dirnames, filenames in os.walk(ws_resolved, topdown=True, followlinks=False):
        current_path = Path(current)
        for dirname in list(dirnames):
            dir_path = current_path / dirname
            rel_dir = dir_path.relative_to(ws_resolved).as_posix()
            if _is_forbidden_shadow_relpath(rel_dir):
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: diretório proibido no worktree: {rel_dir}.',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
            st = os.lstat(dir_path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: symlink ou entrada especial proibida no worktree: {rel_dir}.',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
        dirnames.sort()
        for filename in sorted(filenames):
            file_path = current_path / filename
            rel_posix = file_path.relative_to(ws_resolved).as_posix()
            if _is_forbidden_shadow_relpath(rel_posix):
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: arquivo proibido no worktree: {rel_posix}.',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
            st = os.lstat(file_path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: symlink ou arquivo especial no worktree: {rel_posix}.',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
            try:
                raw = _read_regular_nofollow(file_path)
            except CoreError as error:
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: leitura segura falhou para {rel_posix}: {error}',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                ) from error
            file_hashes[rel_posix] = hashlib.sha256(raw).hexdigest()

    return dict(sorted(file_hashes.items()))


def _compute_snapshot_sha256(file_hashes: dict[str, str]) -> str:
    return hashlib.sha256(_canonical_json_bytes(file_hashes)).hexdigest()


def _verify_git_index_and_tree_modes(git_dir: Path, workspace: Path) -> set[str]:
    stage = _run_git_core(git_dir, workspace, ['ls-files', '--stage', '--full-name'])
    tracked: set[str] = set()
    for line in stage.stdout.splitlines():
        if not line.strip():
            continue
        meta, rel = line.split('\t', 1)
        mode = meta.split(' ', 1)[0]
        if mode not in ('100644', '100755'):
            raise HandoffError(
                f'HANDOFF_INTEGRITY_VIOLATION: modo Git proibido ({mode}) no índice para {rel}.',
                reason_code='HANDOFF_INTEGRITY_VIOLATION',
            )
        tracked.add(rel)

    tree = _run_git_core(git_dir, workspace, ['ls-tree', '-r', '--full-name', 'HEAD'])
    tree_paths: set[str] = set()
    for line in tree.stdout.splitlines():
        if not line.strip():
            continue
        meta, rel = line.split('\t', 1)
        mode = meta.split(' ', 1)[0]
        if mode not in ('100644', '100755'):
            raise HandoffError(
                f'HANDOFF_INTEGRITY_VIOLATION: modo Git proibido ({mode}) em HEAD para {rel}.',
                reason_code='HANDOFF_INTEGRITY_VIOLATION',
            )
        tree_paths.add(rel)

    if tracked != tree_paths:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: índice Git diverge da árvore HEAD.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return tracked


def init_core_worktree(git_dir: Path, workspace: Path, *, initial_message: str = 'udh: base snapshot') -> dict:
    """Inicializa git_dir externo ao workspace e grava commit base determinístico."""
    git_resolved, ws_resolved = _ensure_external_git_dir(git_dir, workspace)
    git_resolved.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ['git', 'init', '--bare', str(git_resolved)],
        env=_sanitized_git_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise HandoffError(
            f'HANDOFF_INTEGRITY_VIOLATION: falha ao inicializar git_dir externo: {completed.stderr}',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    _run_git_core(git_resolved, ws_resolved, ['config', 'core.bare', 'false'])
    _run_git_core(git_resolved, ws_resolved, ['config', 'core.hooksPath', '/dev/null'])
    _run_git_core(git_resolved, ws_resolved, ['config', 'core.fsmonitor', 'false'])

    file_hashes = scan_worktree_physical(ws_resolved)
    _run_git_core(git_resolved, ws_resolved, ['add', '-A', '-f'])
    commit_args = ['commit', '--no-verify', '-m', initial_message]
    if not file_hashes:
        commit_args.append('--allow-empty')
    _run_git_core(git_resolved, ws_resolved, commit_args)
    head_sha = _run_git_core(git_resolved, ws_resolved, ['rev-parse', 'HEAD']).stdout.strip()
    tracked = _verify_git_index_and_tree_modes(git_resolved, ws_resolved)
    if tracked != set(file_hashes.keys()):
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: divergência 1:1 entre worktree físico e git ls-files no commit base.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return {
        'base_commit_sha': require_sha1(head_sha, 'Commit base'),
        'worktree_snapshot_sha256': _compute_snapshot_sha256(file_hashes),
        'files': file_hashes,
    }


def commit_handoff_snapshot(
    git_dir: Path,
    workspace: Path,
    *,
    message: str = 'udh: handoff snapshot',
) -> dict:
    """Valida fisicamente o worktree, cria commit de handoff determinístico no Core e calcula snapshot 1:1."""
    git_resolved, ws_resolved = _ensure_external_git_dir(git_dir, workspace)
    file_hashes = scan_worktree_physical(ws_resolved)
    _run_git_core(git_resolved, ws_resolved, ['add', '-A', '-f'])
    diff_cached = _run_git_core(git_resolved, ws_resolved, ['diff', '--cached', '--quiet'], check=False)
    if diff_cached.returncode == 1:
        _run_git_core(git_resolved, ws_resolved, ['commit', '--no-verify', '-m', message])
    elif diff_cached.returncode != 0:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: erro ao inspecionar diff staged de handoff.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )

    return verify_worktree_clean(git_resolved, ws_resolved)


def verify_worktree_clean(git_dir: Path, workspace: Path) -> dict:
    """Confere árvore 100% limpa, correspondência física 1:1 com git ls-files e ausência de entradas proibidas."""
    git_resolved, ws_resolved = _ensure_external_git_dir(git_dir, workspace)
    file_hashes = scan_worktree_physical(ws_resolved)
    status = _run_git_core(git_resolved, ws_resolved, ['status', '--porcelain', '--ignored'])
    if status.stdout.strip():
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: worktree sujo ou contendo arquivos não rastreados/ignorados.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    tracked = _verify_git_index_and_tree_modes(git_resolved, ws_resolved)
    if tracked != set(file_hashes.keys()):
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: worktree físico diverge de git ls-files.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    head_sha = require_sha1(
        _run_git_core(git_resolved, ws_resolved, ['rev-parse', 'HEAD']).stdout.strip(),
        'Commit HEAD',
    )
    return {
        'head_commit_sha': head_sha,
        'worktree_snapshot_sha256': _compute_snapshot_sha256(file_hashes),
        'files': file_hashes,
    }


def _changed_files_between(git_dir: Path, workspace: Path, base_sha: str, head_sha: str) -> list[str]:
    if base_sha == head_sha:
        return []
    diff = _run_git_core(git_dir, workspace, ['diff', '--name-only', f'{base_sha}..{head_sha}'])
    return sorted(line.strip() for line in diff.stdout.splitlines() if line.strip())


def _verify_evidence_in_trust_store(
    trust_store: TrustStore,
    run: Run,
    evidence_hashes: Sequence[str],
) -> list[dict]:
    by_hash = {
        ev.evidence_sha256: ev
        for ev in run.evidence_refs
        if ev.collector_route == 'TRUSTED_EXECUTOR'
    }
    run_ev_root = (trust_store.root / 'evidence' / run.run_id).resolve()
    verified: list[dict] = []

    for ev_hash in evidence_hashes:
        ev = by_hash.get(ev_hash)
        if ev is None:
            raise HandoffError(
                f'HANDOFF_EVIDENCE_MISMATCH: hash de evidência {ev_hash} ausente ou sem rota TRUSTED_EXECUTOR.',
                reason_code='HANDOFF_EVIDENCE_MISMATCH',
            )
        raw_ref = Path(ev.summary_ref)
        ref_path = raw_ref if raw_ref.is_absolute() else (trust_store.root / raw_ref)
        try:
            resolved_ref = ref_path.resolve(strict=True)
        except OSError as error:
            raise HandoffError(
                f'HANDOFF_EVIDENCE_MISMATCH: arquivo oficial de evidência ausente em {ev.summary_ref}.',
                reason_code='HANDOFF_EVIDENCE_MISMATCH',
            ) from error
        if not resolved_ref.is_relative_to(run_ev_root):
            raise HandoffError(
                'HANDOFF_EVIDENCE_MISMATCH: referência de evidência fora do domínio oficial da TrustStore.',
                reason_code='HANDOFF_EVIDENCE_MISMATCH',
            )
        try:
            raw = _read_regular_nofollow(ref_path)
            actual_hash = hashlib.sha256(raw).hexdigest()
            parsed = strict_json_loads(raw.decode('utf-8'), 'Relatório oficial de evidência')
        except (CoreError, OSError, UnicodeDecodeError) as error:
            raise HandoffError(
                f'HANDOFF_EVIDENCE_MISMATCH: falha ao verificar evidência oficial na TrustStore: {error}',
                reason_code='HANDOFF_EVIDENCE_MISMATCH',
            ) from error
        if actual_hash != ev.evidence_sha256 or not isinstance(parsed, dict):
            raise HandoffError(
                'HANDOFF_EVIDENCE_MISMATCH: SHA-256 do arquivo na TrustStore diverge da evidência registrada.',
                reason_code='HANDOFF_EVIDENCE_MISMATCH',
            )
        verified.append({
            'evidence_id': ev.evidence_id,
            'kind': ev.kind,
            'verdict': ev.verdict,
            'evidence_sha256': ev.evidence_sha256,
            'artifact_digest': ev.artifact_digest,
            'at_version': ev.at_version,
            'summary_ref': ev.summary_ref,
        })
    return verified


def _assert_no_conversational_keys(obj, *, path: str = '$') -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_CONVERSATIONAL_KEYS:
                raise HandoffError(
                    f'HANDOFF_INTEGRITY_VIOLATION: chave conversacional proibida no HandoffPacket: {path}.{key}.',
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
            _assert_no_conversational_keys(value, path=f'{path}.{key}')
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            _assert_no_conversational_keys(item, path=f'{path}[{idx}]')


def verify_handoff_packet(packet: dict) -> dict:
    """Valida integridade estrutural, packet_sha256 e ausência programática de chaves conversacionais."""
    if not isinstance(packet, dict):
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: HandoffPacket deve ser objeto JSON.')
    if type(packet.get('schema_version')) is not int or packet.get('schema_version') != HANDOFF_SCHEMA_VERSION:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: schema_version do HandoffPacket inválido.')
    _assert_no_conversational_keys(packet)

    required_keys = {
        'schema_version', 'packet_sha256', 'run_id', 'run_revision', 'checkpoint_id',
        'spec_id', 'spec_version', 'ticket_id', 'last_confirmed_step',
        'from_adapter', 'to_adapter', 'git', 'evidence', 'task_contract',
    }
    if set(packet.keys()) != required_keys:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: conjunto de chaves do HandoffPacket fora do contrato.')

    try:
        require_canonical_uuid(packet['run_id'], 'run_id no HandoffPacket')
        require_canonical_uuid(packet['checkpoint_id'], 'checkpoint_id no HandoffPacket')
        if type(packet['run_revision']) is not int or packet['run_revision'] < 0:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: run_revision inválido no HandoffPacket.')
        require_text(packet['spec_id'], 'spec_id no HandoffPacket')
        require_text(packet['spec_version'], 'spec_version no HandoffPacket')
        if packet['ticket_id'] is not None:
            require_text(packet['ticket_id'], 'ticket_id no HandoffPacket')
        require_text(packet['last_confirmed_step'], 'last_confirmed_step no HandoffPacket')
        require_text(packet['from_adapter'], 'from_adapter no HandoffPacket')
        require_text(packet['to_adapter'], 'to_adapter no HandoffPacket')

        git_info = packet['git']
        if not isinstance(git_info, dict):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: bloco git inválido no HandoffPacket.')
        require_sha1(git_info.get('base_commit_sha'), 'base_commit_sha')
        require_sha1(git_info.get('head_commit_sha'), 'head_commit_sha')
        require_sha256(git_info.get('worktree_snapshot_sha256'), 'worktree_snapshot_sha256')
        if not isinstance(git_info.get('changed_files'), list):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: changed_files inválido no HandoffPacket.')

        if not isinstance(packet['evidence'], list):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: evidence inválido no HandoffPacket.')
        task_contract = packet['task_contract']
        if not isinstance(task_contract, dict):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract inválido no HandoffPacket.')
        require_text(task_contract.get('objective'), 'task_contract.objective')
        if not isinstance(task_contract.get('acceptance_criteria'), list) or not task_contract['acceptance_criteria']:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: acceptance_criteria obrigatório no HandoffPacket.')
        if not isinstance(task_contract.get('allowed_tools'), list):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: allowed_tools inválido no HandoffPacket.')

        expected_sha = require_sha256(packet['packet_sha256'], 'packet_sha256')
    except HandoffError:
        raise
    except CoreError as error:
        raise HandoffError(f'HANDOFF_INTEGRITY_VIOLATION: {error}', reason_code='HANDOFF_INTEGRITY_VIOLATION') from error
    without_hash = {k: v for k, v in packet.items() if k != 'packet_sha256'}
    recomputed = hashlib.sha256(_canonical_json_bytes(without_hash)).hexdigest()
    if recomputed != expected_sha:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: packet_sha256 não confere com o conteúdo canônico do HandoffPacket.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return packet


def build_handoff_packet(
    *,
    core_service: CoreService,
    trust_store: TrustStore,
    run_id: str,
    git_dir: Path,
    workspace: Path,
    from_adapter: str,
    to_adapter: str,
    objective: str,
    acceptance_criteria: Sequence[str],
    allowed_tools: Sequence[str],
    conformant_adapters: Sequence[str] | None = None,
    env_probes=None,
    manifest_path: Path | None = None,
    opencode_manifest_path: Path | None = None,
) -> dict:
    """Valida estado autoritativo (SQLite v4 + Git externo + TrustStore + Política v2) e monta HandoffPacket."""
    run = core_service.show(run_id)
    if run.schema_version != 4 or run.state != 'IN_PROGRESS':
        raise HandoffError(
            f'HANDOFF_INTEGRITY_VIOLATION: handoff exige banco v4 em IN_PROGRESS (atual: v{run.schema_version}, {run.state}).',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    if not run.checkpoints:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: nenhum checkpoint F-03 registrado para a execução.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    latest = run.checkpoints[-1]
    if latest.run_revision != run.revision:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: último checkpoint F-03 está obsoleto em relação à revisão da execução.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    if latest.has_unknown_pendencies:
        raise HandoffError(
            'UNKNOWN_PENDENCY_ACTIVE: existe pendência UNKNOWN ativa no último checkpoint F-03.',
            reason_code='UNKNOWN_PENDENCY_ACTIVE',
        )
    if not latest.base_commit_sha or not latest.head_commit_sha or not latest.worktree_snapshot_sha256:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: checkpoint F-03 incompleto (base/head/snapshot ausente).',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )

    clean = verify_worktree_clean(git_dir, workspace)
    if clean['head_commit_sha'] != latest.head_commit_sha:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: HEAD do worktree diverge de head_commit_sha do checkpoint F-03.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    if clean['worktree_snapshot_sha256'] != latest.worktree_snapshot_sha256:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: snapshot físico do worktree diverge do checkpoint F-03.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )

    verified_evidence = _verify_evidence_in_trust_store(trust_store, run, latest.evidence_hashes)

    # Verifica autorização de execução do adaptador de destino sob Política v2 e ADR-026
    if conformant_adapters is not None:
        select_adapter(conformant_adapters, requested=to_adapter)
        auth = core_service.policy(
            run.run_id,
            'EXECUTE_AGENT',
            preflight_status='READY',
            conformant_adapters=tuple(conformant_adapters),
            adapter=to_adapter,
        )
    else:
        auth = core_service.authorize_agent_execution(
            run.run_id,
            store_dir=trust_store.root,
            adapter=to_adapter,
            env_probes=env_probes,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
    if auth.get('decision') != 'ALLOW':
        raise HandoffError(
            f"HANDOFF_UNAUTHORIZED: adaptador de destino '{to_adapter}' não autorizado ({auth.get('reason_code')}).",
            reason_code='HANDOFF_UNAUTHORIZED',
        )

    require_text(from_adapter, 'Adaptador de origem')
    require_text(to_adapter, 'Adaptador de destino')
    require_text(objective, 'Objetivo da tarefa')
    criteria_list = [require_text(item, 'Critério de aceite') for item in acceptance_criteria]
    if not criteria_list:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: acceptance_criteria não pode ser vazio.')
    tools_list = [require_text(item, 'Ferramenta autorizada') for item in allowed_tools]

    changed_files = _changed_files_between(git_dir, workspace, latest.base_commit_sha, latest.head_commit_sha)
    body = {
        'schema_version': HANDOFF_SCHEMA_VERSION,
        'run_id': run.run_id,
        'run_revision': run.revision,
        'checkpoint_id': latest.checkpoint_id,
        'spec_id': run.spec_id,
        'spec_version': run.spec_version,
        'ticket_id': run.ticket_id,
        'last_confirmed_step': latest.last_confirmed_step,
        'from_adapter': from_adapter,
        'to_adapter': to_adapter,
        'git': {
            'base_commit_sha': latest.base_commit_sha,
            'head_commit_sha': latest.head_commit_sha,
            'worktree_snapshot_sha256': latest.worktree_snapshot_sha256,
            'changed_files': changed_files,
        },
        'evidence': verified_evidence,
        'task_contract': {
            'objective': objective,
            'acceptance_criteria': criteria_list,
            'allowed_tools': tools_list,
        },
    }
    packet_sha256 = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
    packet = {**body, 'packet_sha256': packet_sha256}
    return verify_handoff_packet(packet)


def render_handoff_prompt(packet: dict) -> str:
    """Gera prompt determinístico e mínimo para o 2º agente apenas com estado autoritativo verificado."""
    verified = verify_handoff_packet(packet)
    serialized = json.dumps(verified, sort_keys=True, indent=2, ensure_ascii=False)
    return (
        'Pacote de handoff autoritativo UDH (ADR-011/ADR-027; sem histórico conversacional anterior).\n'
        'Continue o trabalho a partir do worktree verificado abaixo para satisfazer o task_contract:\n\n'
        f'{serialized}\n'
    )
