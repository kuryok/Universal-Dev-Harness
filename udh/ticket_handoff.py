"""Handoff determinístico entre tickets gerenciados no plano persistido (Change 5 T4 / TICKET-02)."""
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import re

from .domain import (
    CoreError,
    Run,
    VALID_EVIDENCE_KINDS,
    VALID_EVIDENCE_VERDICTS,
    require_canonical_uuid,
    require_sha1,
    require_sha256,
    require_text,
    strict_json_loads,
)
from .handoff import (
    HandoffError,
    _assert_no_conversational_keys,
    _changed_files_between,
    _run_git_core,
    select_adapter,
    verify_worktree_clean,
)
from .service import CoreService
from .transitions import ConflictError
from .trust_store import TrustStore, TrustStoreError, _canonical_json_bytes, _read_regular_nofollow

MANAGED_HANDOFF_SCHEMA_VERSION = 1
MANAGED_HANDOFF_PACKET_KIND = 'managed_ticket_handoff'
MAX_CONTEXT_ITEM_BYTES = 4096
MAX_CONTEXT_TOTAL_BYTES = 32768
_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_AT_VERSION_RE = re.compile(r'^sha256:[0-9a-f]{64}$')

_TOP_LEVEL_KEYS = frozenset({
    'schema_version',
    'packet_kind',
    'packet_sha256',
    'plan_identity',
    'run_id',
    'run_revision',
    'ticket_id',
    'contract_sha256',
    'at_version',
    'from_adapter',
    'to_adapter',
    'task_contract',
    'authorized_context',
    'predecessors',
    'git',
})
_PLAN_IDENTITY_KEYS = frozenset({
    'change_id', 'spec_version', 'spec_sha256', 'approval_revision', 'plan_sha256',
})
_TASK_CONTRACT_KEYS = frozenset({'objective', 'coverage', 'checks', 'allowed_tools'})
_AUTHORIZED_CONTEXT_KEYS = frozenset({'allowed_context_ids', 'context_ids', 'items'})
_CONTEXT_ITEM_KEYS = frozenset({'id', 'kind', 'ref_sha256', 'summary'})
_PREDECESSOR_KEYS = frozenset({
    'ticket_id',
    'run_id',
    'run_revision',
    'checkpoint_id',
    'last_confirmed_step',
    'acceptance_snapshot_sha256',
    'at_evidence_id',
    'at_evidence_sha256',
    'review_evidence_id',
    'review_evidence_sha256',
    'commit_sha',
    'artifact_digest',
    'worktree_snapshot_sha256',
    'verified_evidence',
})
_VERIFIED_EVIDENCE_KEYS = frozenset({
    'evidence_id',
    'kind',
    'verdict',
    'evidence_sha256',
    'artifact_digest',
    'at_version',
    'summary_ref',
})
_GIT_KEYS = frozenset({'base_commit_sha', 'head_commit_sha', 'worktree_snapshot_sha256', 'changed_files'})


def _require_identifier(value, label: str, *, reason_code: str = 'HANDOFF_INTEGRITY_VIOLATION') -> str:
    if type(value) is not str or not _IDENTIFIER_RE.fullmatch(value):
        raise HandoffError(f'{reason_code}: {label} inválido.', reason_code=reason_code)
    return value


def _require_clean_text(value, label: str, *, reason_code: str = 'HANDOFF_INTEGRITY_VIOLATION') -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise HandoffError(f'{reason_code}: {label} inválido.', reason_code=reason_code)
    return value


def _require_non_negative_int(value, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise HandoffError(f'HANDOFF_INTEGRITY_VIOLATION: {label} inválido.')
    return value


def _verify_predecessor_checkpoint_evidence(
    trust_store: TrustStore,
    run: Run,
    evidence_hashes: Sequence[str],
) -> list[dict]:
    """Revalida fisicamente na TrustStore todas as evidências oficiais do checkpoint F-03 da predecessora."""
    by_hash = {
        ev.evidence_sha256: ev
        for ev in run.evidence_refs
        if ev.collector_route == 'TRUSTED_EXECUTOR'
        or (ev.kind == 'REVIEW_REPORT' and ev.collector_route == 'OPERATOR_LOCAL')
    }
    run_ev_root = (trust_store.root / 'evidence' / run.run_id).resolve()
    verified: list[dict] = []

    for ev_hash in evidence_hashes:
        ev = by_hash.get(ev_hash)
        if ev is None:
            raise HandoffError(
                f'HANDOFF_EVIDENCE_MISMATCH: hash de evidência {ev_hash} ausente ou sem rota autorizada.',
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


def _validate_context_refs(contract: dict, context_refs: dict | None) -> list[dict]:
    allowed_ids = list(contract['allowed_context_ids'])
    context_ids = list(contract['context_ids'])
    if not set(context_ids).issubset(set(allowed_ids)):
        raise HandoffError(
            'HANDOFF_CONTEXT_DENIED: context_ids do contrato extrapola allowed_context_ids.',
            reason_code='HANDOFF_CONTEXT_DENIED',
        )
    if not context_ids:
        if context_refs is not None:
            if type(context_refs) is not dict or context_refs:
                raise HandoffError(
                    'HANDOFF_CONTEXT_DENIED: contrato sem context_ids não admite context_refs.',
                    reason_code='HANDOFF_CONTEXT_DENIED',
                )
        return []

    if type(context_refs) is not dict:
        raise HandoffError(
            'HANDOFF_CONTEXT_DENIED: context_refs obrigatório para os context_ids do contrato.',
            reason_code='HANDOFF_CONTEXT_DENIED',
        )
    if set(context_refs.keys()) != set(context_ids):
        raise HandoffError(
            'HANDOFF_CONTEXT_DENIED: context_refs deve corresponder exatamente aos context_ids do contrato.',
            reason_code='HANDOFF_CONTEXT_DENIED',
        )

    items: list[dict] = []
    total_bytes = 0
    for cid in sorted(context_ids):
        _require_identifier(cid, 'ID de contexto', reason_code='HANDOFF_CONTEXT_DENIED')
        entry = context_refs[cid]
        if type(entry) is not dict:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: entrada de contexto {cid} deve ser objeto JSON.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        _assert_no_conversational_keys(entry, path=f'$.authorized_context.{cid}')
        if set(entry.keys()) != {'kind', 'ref_sha256', 'summary'}:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: entrada de contexto {cid} fora do esquema permitido.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        kind = _require_identifier(entry['kind'], f'kind de contexto {cid}', reason_code='HANDOFF_CONTEXT_DENIED')
        try:
            ref_sha = require_sha256(entry['ref_sha256'], f'ref_sha256 de contexto {cid}')
        except CoreError as error:
            raise HandoffError(f'HANDOFF_CONTEXT_DENIED: {error}', reason_code='HANDOFF_CONTEXT_DENIED') from error
        summary = entry['summary']
        if type(summary) is not str or not summary or summary.strip() != summary:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: summary inválido para contexto {cid}.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        try:
            summary_bytes = len(summary.encode('utf-8'))
        except UnicodeError as error:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: summary não UTF-8 para contexto {cid}.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            ) from error
        if summary_bytes > MAX_CONTEXT_ITEM_BYTES:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: summary de contexto {cid} excede {MAX_CONTEXT_ITEM_BYTES} bytes.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        item = {'id': cid, 'kind': kind, 'ref_sha256': ref_sha, 'summary': summary}
        total_bytes += len(_canonical_json_bytes(item))
        if total_bytes > MAX_CONTEXT_TOTAL_BYTES:
            raise HandoffError(
                f'HANDOFF_CONTEXT_DENIED: contexto autorizado excede {MAX_CONTEXT_TOTAL_BYTES} bytes.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        items.append(item)
    return items


def _verify_predecessors_worktree(git_dir: Path, workspace: Path, clean: dict, predecessors: list[dict]) -> dict:
    matched_head = [
        item for item in predecessors
        if item['checkpoint'].head_commit_sha == clean['head_commit_sha']
        and item['checkpoint'].worktree_snapshot_sha256 == clean['worktree_snapshot_sha256']
    ]
    if not matched_head:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: HEAD ou snapshot físico do worktree diverge do checkpoint F-03 aceito da predecessora.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    changed_union: set[str] = set()
    for item in predecessors:
        commit_sha = item['acceptance']['commit_sha']
        if commit_sha != clean['head_commit_sha']:
            anc = _run_git_core(
                git_dir, workspace, ['merge-base', '--is-ancestor', commit_sha, clean['head_commit_sha']], check=False,
            )
            if anc.returncode != 0:
                raise HandoffError(
                    f"HANDOFF_INTEGRITY_VIOLATION: commit aceito da predecessora {item['ticket_id']} não é ancestral de HEAD.",
                    reason_code='HANDOFF_INTEGRITY_VIOLATION',
                )
        changed_union.update(
            _changed_files_between(git_dir, workspace, item['checkpoint'].base_commit_sha, clean['head_commit_sha'])
        )
    base_sha = matched_head[0]['checkpoint'].base_commit_sha
    return {
        'base_commit_sha': base_sha,
        'head_commit_sha': clean['head_commit_sha'],
        'worktree_snapshot_sha256': clean['worktree_snapshot_sha256'],
        'changed_files': sorted(changed_union),
    }


def _map_context_error(error: Exception) -> HandoffError:
    explicit_code = getattr(error, 'handoff_reason_code', None)
    if isinstance(explicit_code, str) and explicit_code:
        return HandoffError(f'{explicit_code}: {error}', reason_code=explicit_code)
    if isinstance(error, TrustStoreError):
        return HandoffError(f'HANDOFF_EVIDENCE_MISMATCH: {error}', reason_code='HANDOFF_EVIDENCE_MISMATCH')
    if isinstance(error, ConflictError):
        return HandoffError(f'HANDOFF_PRECONDITION_FAILED: {error}', reason_code='HANDOFF_PRECONDITION_FAILED')
    return HandoffError(f'HANDOFF_INTEGRITY_VIOLATION: {error}', reason_code='HANDOFF_INTEGRITY_VIOLATION')


def _validate_verified_evidence_list(entries, *, expected_at_id: str, expected_at_sha: str) -> None:
    if type(entries) is not list or not entries:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: predecessor.verified_evidence obrigatório.')
    found_accepted_at = False
    for entry in entries:
        if type(entry) is not dict or set(entry.keys()) != _VERIFIED_EVIDENCE_KEYS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: item de verified_evidence fora do esquema.')
        ev_id = require_canonical_uuid(entry['evidence_id'], 'verified_evidence.evidence_id')
        kind = entry['kind']
        verdict = entry['verdict']
        if type(kind) is not str or kind not in VALID_EVIDENCE_KINDS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: verified_evidence.kind inválido.')
        if type(verdict) is not str or verdict not in VALID_EVIDENCE_VERDICTS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: verified_evidence.verdict inválido.')
        ev_sha = require_sha256(entry['evidence_sha256'], 'verified_evidence.evidence_sha256')
        require_sha256(entry['artifact_digest'], 'verified_evidence.artifact_digest')
        at_ver = entry['at_version']
        if at_ver is not None and (type(at_ver) is not str or not _AT_VERSION_RE.fullmatch(at_ver)):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: verified_evidence.at_version inválido.')
        require_text(entry['summary_ref'], 'verified_evidence.summary_ref')
        if ev_id == expected_at_id and ev_sha == expected_at_sha and kind == 'AT_RESULT' and verdict == 'PASS':
            found_accepted_at = True
    if not found_accepted_at:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: verified_evidence não contém o AT_RESULT PASS aceito.')


def verify_managed_handoff_packet(packet: dict) -> dict:
    """Valida integridade estrutural, ausência de chaves conversacionais e packet_sha256."""
    if type(packet) is not dict:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: pacote de handoff gerenciado deve ser objeto JSON.')
    _assert_no_conversational_keys(packet)
    if set(packet.keys()) != _TOP_LEVEL_KEYS:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: conjunto de chaves do pacote de handoff fora do contrato.')
    if type(packet['schema_version']) is not int or packet['schema_version'] != MANAGED_HANDOFF_SCHEMA_VERSION:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: schema_version do pacote de handoff inválido.')
    if packet['packet_kind'] != MANAGED_HANDOFF_PACKET_KIND:
        raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: packet_kind inválido.')

    try:
        plan_id = packet['plan_identity']
        if type(plan_id) is not dict or set(plan_id.keys()) != _PLAN_IDENTITY_KEYS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: plan_identity inválido.')
        _require_identifier(plan_id['change_id'], 'plan_identity.change_id')
        _require_identifier(plan_id['spec_version'], 'plan_identity.spec_version')
        require_sha256(plan_id['spec_sha256'], 'plan_identity.spec_sha256')
        _require_non_negative_int(plan_id['approval_revision'], 'plan_identity.approval_revision', minimum=1)
        require_sha256(plan_id['plan_sha256'], 'plan_identity.plan_sha256')

        require_canonical_uuid(packet['run_id'], 'run_id')
        _require_non_negative_int(packet['run_revision'], 'run_revision', minimum=1)
        _require_identifier(packet['ticket_id'], 'ticket_id')
        require_sha256(packet['contract_sha256'], 'contract_sha256')
        if type(packet['at_version']) is not str or not _AT_VERSION_RE.fullmatch(packet['at_version']):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: at_version inválido.')

        from_adapter = _require_clean_text(packet['from_adapter'], 'from_adapter', reason_code='HANDOFF_UNAUTHORIZED')
        to_adapter = _require_clean_text(packet['to_adapter'], 'to_adapter', reason_code='HANDOFF_UNAUTHORIZED')
        if from_adapter == to_adapter:
            raise HandoffError(
                'HANDOFF_UNAUTHORIZED: from_adapter e to_adapter devem ser distintos.',
                reason_code='HANDOFF_UNAUTHORIZED',
            )

        task_contract = packet['task_contract']
        if type(task_contract) is not dict or set(task_contract.keys()) != _TASK_CONTRACT_KEYS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract inválido.')
        require_text(task_contract['objective'], 'task_contract.objective')
        coverage = task_contract['coverage']
        if type(coverage) is not list or not coverage:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract.coverage obrigatório.')
        for pair in coverage:
            if type(pair) is not dict or set(pair.keys()) != {'task_id', 'criterion_id'}:
                raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: item de coverage inválido.')
            _require_identifier(pair['task_id'], 'coverage.task_id')
            _require_identifier(pair['criterion_id'], 'coverage.criterion_id')
        checks = task_contract['checks']
        if type(checks) is not list or not checks or any(type(c) is not str or not c.strip() for c in checks):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract.checks inválido.')
        allowed_tools = task_contract['allowed_tools']
        if type(allowed_tools) is not list:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract.allowed_tools inválido.')
        for tool in allowed_tools:
            _require_clean_text(tool, 'task_contract.allowed_tools')
        if len(set(allowed_tools)) != len(allowed_tools):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: task_contract.allowed_tools contém duplicatas.')

        auth_ctx = packet['authorized_context']
        if type(auth_ctx) is not dict or set(auth_ctx.keys()) != _AUTHORIZED_CONTEXT_KEYS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: authorized_context inválido.')
        allowed_ctx_ids = auth_ctx['allowed_context_ids']
        ctx_ids = auth_ctx['context_ids']
        ctx_items = auth_ctx['items']
        if type(allowed_ctx_ids) is not list or type(ctx_ids) is not list or type(ctx_items) is not list:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: listas de authorized_context inválidas.')
        for cid in allowed_ctx_ids:
            _require_identifier(cid, 'allowed_context_ids', reason_code='HANDOFF_CONTEXT_DENIED')
        for cid in ctx_ids:
            _require_identifier(cid, 'context_ids', reason_code='HANDOFF_CONTEXT_DENIED')
        if allowed_ctx_ids != sorted(set(allowed_ctx_ids)) or ctx_ids != sorted(set(ctx_ids)):
            raise HandoffError(
                'HANDOFF_CONTEXT_DENIED: IDs de contexto devem ser únicos e ordenados.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        if not set(ctx_ids).issubset(set(allowed_ctx_ids)):
            raise HandoffError(
                'HANDOFF_CONTEXT_DENIED: context_ids fora de allowed_context_ids.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        if len(ctx_items) != len(ctx_ids):
            raise HandoffError(
                'HANDOFF_CONTEXT_DENIED: itens de contexto divergem de context_ids.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )
        seen_item_ids: list[str] = []
        total_bytes = 0
        for item in ctx_items:
            if type(item) is not dict or set(item.keys()) != _CONTEXT_ITEM_KEYS:
                raise HandoffError('HANDOFF_CONTEXT_DENIED: item de contexto inválido.', reason_code='HANDOFF_CONTEXT_DENIED')
            seen_item_ids.append(_require_identifier(item['id'], 'context item id', reason_code='HANDOFF_CONTEXT_DENIED'))
            _require_identifier(item['kind'], 'context item kind', reason_code='HANDOFF_CONTEXT_DENIED')
            require_sha256(item['ref_sha256'], 'context item ref_sha256')
            if type(item['summary']) is not str or not item['summary'] or item['summary'].strip() != item['summary']:
                raise HandoffError('HANDOFF_CONTEXT_DENIED: summary de contexto inválido.', reason_code='HANDOFF_CONTEXT_DENIED')
            try:
                summary_bytes = len(item['summary'].encode('utf-8'))
            except UnicodeError as error:
                raise HandoffError(
                    'HANDOFF_CONTEXT_DENIED: summary de contexto não UTF-8.',
                    reason_code='HANDOFF_CONTEXT_DENIED',
                ) from error
            if summary_bytes > MAX_CONTEXT_ITEM_BYTES:
                raise HandoffError(
                    'HANDOFF_CONTEXT_DENIED: summary de contexto excede limite.',
                    reason_code='HANDOFF_CONTEXT_DENIED',
                )
            total_bytes += len(_canonical_json_bytes(item))
        if seen_item_ids != ctx_ids or total_bytes > MAX_CONTEXT_TOTAL_BYTES:
            raise HandoffError(
                'HANDOFF_CONTEXT_DENIED: conjunto ou tamanho de contexto inválido.',
                reason_code='HANDOFF_CONTEXT_DENIED',
            )

        predecessors = packet['predecessors']
        if type(predecessors) is not list or not predecessors:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: predecessors obrigatório no pacote de handoff gerenciado.')
        seen_preds: list[str] = []
        for pred in predecessors:
            if type(pred) is not dict or set(pred.keys()) != _PREDECESSOR_KEYS:
                raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: bloco de predecessora inválido.')
            seen_preds.append(_require_identifier(pred['ticket_id'], 'predecessor.ticket_id'))
            require_canonical_uuid(pred['run_id'], 'predecessor.run_id')
            _require_non_negative_int(pred['run_revision'], 'predecessor.run_revision', minimum=1)
            require_canonical_uuid(pred['checkpoint_id'], 'predecessor.checkpoint_id')
            require_text(pred['last_confirmed_step'], 'predecessor.last_confirmed_step')
            require_sha256(pred['acceptance_snapshot_sha256'], 'predecessor.acceptance_snapshot_sha256')
            at_ev_id = require_canonical_uuid(pred['at_evidence_id'], 'predecessor.at_evidence_id')
            at_ev_sha = require_sha256(pred['at_evidence_sha256'], 'predecessor.at_evidence_sha256')
            require_canonical_uuid(pred['review_evidence_id'], 'predecessor.review_evidence_id')
            require_sha256(pred['review_evidence_sha256'], 'predecessor.review_evidence_sha256')
            require_sha1(pred['commit_sha'], 'predecessor.commit_sha')
            require_sha256(pred['artifact_digest'], 'predecessor.artifact_digest')
            require_sha256(pred['worktree_snapshot_sha256'], 'predecessor.worktree_snapshot_sha256')
            _validate_verified_evidence_list(
                pred['verified_evidence'], expected_at_id=at_ev_id, expected_at_sha=at_ev_sha,
            )
        if seen_preds != sorted(set(seen_preds)):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: predecessors fora de ordem ou duplicados.')

        git_info = packet['git']
        if type(git_info) is not dict or set(git_info.keys()) != _GIT_KEYS:
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: bloco git inválido.')
        require_sha1(git_info['base_commit_sha'], 'git.base_commit_sha')
        head_sha = require_sha1(git_info['head_commit_sha'], 'git.head_commit_sha')
        snap_sha = require_sha256(git_info['worktree_snapshot_sha256'], 'git.worktree_snapshot_sha256')
        if type(git_info['changed_files']) is not list or any(type(p) is not str for p in git_info['changed_files']):
            raise HandoffError('HANDOFF_INTEGRITY_VIOLATION: git.changed_files inválido.')
        if not any(
            pred['commit_sha'] == head_sha and pred['worktree_snapshot_sha256'] == snap_sha
            for pred in predecessors
        ):
            raise HandoffError(
                'HANDOFF_INTEGRITY_VIOLATION: git.head_commit_sha/worktree_snapshot_sha256 não corresponde a nenhuma predecessora aceita.'
            )

        expected_sha = require_sha256(packet['packet_sha256'], 'packet_sha256')
    except HandoffError:
        raise
    except CoreError as error:
        raise HandoffError(f'HANDOFF_INTEGRITY_VIOLATION: {error}', reason_code='HANDOFF_INTEGRITY_VIOLATION') from error

    without_hash = {k: v for k, v in packet.items() if k != 'packet_sha256'}
    recomputed = hashlib.sha256(_canonical_json_bytes(without_hash)).hexdigest()
    if recomputed != expected_sha:
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: packet_sha256 não confere com o conteúdo canônico do pacote.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    return packet


def build_managed_handoff_packet(
    *,
    core_service: CoreService,
    trust_store: TrustStore,
    run_id: str,
    git_dir: Path,
    workspace: Path,
    from_adapter: str,
    to_adapter: str,
    allowed_tools: Sequence[str],
    context_refs: dict | None = None,
    conformant_adapters: Sequence[str] | None = None,
    env_probes=None,
    manifest_path: Path | None = None,
    opencode_manifest_path: Path | None = None,
) -> dict:
    """Monta e sela pacote de handoff entre tickets gerenciados a partir do estado persistido e verificado."""
    try:
        ctx = core_service.managed_ticket_handoff_context(run_id, trust_store=trust_store)
    except (CoreError, TrustStoreError) as error:
        raise _map_context_error(error) from error

    run = ctx['run']
    contract = ctx['contract']
    ticket = ctx['ticket']
    predecessors = ctx['predecessors']

    from_name = _require_clean_text(from_adapter, 'Adaptador de origem', reason_code='HANDOFF_UNAUTHORIZED')
    to_name = _require_clean_text(to_adapter, 'Adaptador de destino', reason_code='HANDOFF_UNAUTHORIZED')
    if from_name == to_name:
        raise HandoffError(
            'HANDOFF_UNAUTHORIZED: troca de agente exige adaptador de destino distinto do de origem.',
            reason_code='HANDOFF_UNAUTHORIZED',
        )

    clean = verify_worktree_clean(git_dir, workspace)
    git_block = _verify_predecessors_worktree(git_dir, workspace, clean, predecessors)

    verified_predecessors: list[dict] = []
    for item in predecessors:
        ckpt = item['checkpoint']
        acc = item['acceptance']
        verified_ev = _verify_predecessor_checkpoint_evidence(trust_store, item['run'], ckpt.evidence_hashes)
        verified_predecessors.append({
            'ticket_id': item['ticket_id'],
            'run_id': acc['run_id'],
            'run_revision': acc['run_revision'],
            'checkpoint_id': ckpt.checkpoint_id,
            'last_confirmed_step': ckpt.last_confirmed_step,
            'acceptance_snapshot_sha256': acc['snapshot_sha256'],
            'at_evidence_id': acc['at_evidence_id'],
            'at_evidence_sha256': acc['at_evidence_sha256'],
            'review_evidence_id': acc['review_evidence_id'],
            'review_evidence_sha256': acc['review_evidence_sha256'],
            'commit_sha': acc['commit_sha'],
            'artifact_digest': acc['artifact_digest'],
            'worktree_snapshot_sha256': ckpt.worktree_snapshot_sha256,
            'verified_evidence': verified_ev,
        })

    context_items = _validate_context_refs(contract, context_refs)

    if conformant_adapters is not None:
        select_adapter(conformant_adapters, requested=to_name, exclude=(from_name,))
        auth = core_service.policy(
            run.run_id,
            'EXECUTE_AGENT',
            preflight_status='READY',
            conformant_adapters=tuple(conformant_adapters),
            adapter=to_name,
        )
    else:
        report = core_service.doctor(
            store_dir=trust_store.root,
            env_probes=env_probes,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        if report['overall_status'] == 'NOT_READY':
            raise HandoffError(
                'Preflight retornou NOT_READY; seleção de adaptador bloqueada (fail-closed).',
                reason_code='NO_CONFORMANT_ADAPTER',
            )
        rt_check = next((c for c in report['checks'] if c['id'] == 'runtime_components'), {})
        conformant = tuple(rt_check.get('details', {}).get('conformant_adapters', ()))
        select_adapter(conformant, requested=to_name, exclude=(from_name,))
        auth = core_service.policy(
            run.run_id,
            'EXECUTE_AGENT',
            preflight_status=report['overall_status'],
            conformant_adapters=conformant,
            adapter=to_name,
        )
    if auth.get('decision') != 'ALLOW':
        raise HandoffError(
            f"HANDOFF_UNAUTHORIZED: adaptador de destino '{to_name}' não autorizado ({auth.get('reason_code')}).",
            reason_code='HANDOFF_UNAUTHORIZED',
        )

    if isinstance(allowed_tools, (str, bytes)) or not isinstance(allowed_tools, Sequence):
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: allowed_tools deve ser uma sequência de nomes de ferramentas.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )
    tools_list = [_require_clean_text(item, 'Ferramenta autorizada') for item in allowed_tools]
    if len(set(tools_list)) != len(tools_list):
        raise HandoffError(
            'HANDOFF_INTEGRITY_VIOLATION: allowed_tools não pode conter duplicatas.',
            reason_code='HANDOFF_INTEGRITY_VIOLATION',
        )

    body = {
        'schema_version': MANAGED_HANDOFF_SCHEMA_VERSION,
        'packet_kind': MANAGED_HANDOFF_PACKET_KIND,
        'plan_identity': {
            'change_id': contract['change_id'],
            'spec_version': contract['spec_version'],
            'spec_sha256': contract['spec_sha256'],
            'approval_revision': contract['approval_revision'],
            'plan_sha256': contract['plan_sha256'],
        },
        'run_id': run.run_id,
        'run_revision': run.revision,
        'ticket_id': contract['ticket_id'],
        'contract_sha256': ctx['contract_sha256'],
        'at_version': contract['at_version'],
        'from_adapter': from_name,
        'to_adapter': to_name,
        'task_contract': {
            'objective': ticket['objective'],
            'coverage': ticket['coverage'],
            'checks': ticket['checks'],
            'allowed_tools': tools_list,
        },
        'authorized_context': {
            'allowed_context_ids': contract['allowed_context_ids'],
            'context_ids': contract['context_ids'],
            'items': context_items,
        },
        'predecessors': verified_predecessors,
        'git': git_block,
    }
    packet_sha256 = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
    return verify_managed_handoff_packet({**body, 'packet_sha256': packet_sha256})


def render_managed_handoff_prompt(packet: dict) -> str:
    """Gera prompt determinístico e mínimo para o agente seguinte a partir do pacote verificado."""
    verified = verify_managed_handoff_packet(packet)
    serialized = json.dumps(verified, sort_keys=True, indent=2, ensure_ascii=False)
    return (
        'Pacote de handoff gerenciado UDH entre tickets dependentes (ADR-011/ADR-027; sem transcrição conversacional anterior).\n'
        'Continue no worktree verificado para concluir o ticket descrito em task_contract usando apenas o contexto autorizado:\n\n'
        f'{serialized}\n'
    )
