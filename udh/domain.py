"""Dados e invariantes do Core (v1–v4); sem acesso a persistência."""
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID


VALID_MODES = ('FAST', 'STANDARD', 'DEEP')
VALID_V4_STATES = ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE')
VALID_V4_OPERATIONS = ('CREATE_RUN', 'START_RUN', 'BLOCK_RUN', 'UNBLOCK_RUN', 'COMPLETE_RUN')
VALID_V4_KINDS = ('RUN_CREATED', 'RUN_STARTED', 'RUN_BLOCKED', 'RUN_UNBLOCKED', 'RUN_COMPLETED')
VALID_BLOCK_CATEGORIES = ('POLICY_VIOLATION', 'FUNCTIONAL_FAILURE', 'ADMIN_PAUSE')
VALID_EVIDENCE_KINDS = ('AT_RESULT', 'SMOKE_RESULT', 'POC_RESULT', 'REVIEW_REPORT', 'ARTIFACT_PROMOTION')
COMPLETION_EVIDENCE_KINDS = ('AT_RESULT', 'SMOKE_RESULT', 'POC_RESULT')
VALID_EVIDENCE_VERDICTS = ('PASS', 'FAIL', 'ERROR', 'STALE')
VALID_COLLECTOR_ROUTES = ('TRUSTED_EXECUTOR', 'OPERATOR_LOCAL')
VALID_ACTOR_ROUTES = ('CORE_LOCAL', 'OPERATOR_LOCAL', 'TRUSTED_EXECUTOR')


class CoreError(ValueError):
    """Uma operação do Core não pôde cumprir seu contrato."""


def require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoreError(f'{field} deve conter texto não vazio.')
    return value


def require_run_id(value: str) -> str:
    require_text(value, 'Identificador da execução')
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CoreError('Identificador da execução deve ser um UUID válido.') from error


def require_canonical_uuid(value: str, field: str) -> str:
    require_text(value, field)
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise CoreError(f'{field} deve ser um UUID válido.') from error
    if canonical != value:
        raise CoreError(f'{field} deve estar em formato UUID canônico.')
    return value


def require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise CoreError(f'{field} deve ser um hash SHA-256 hexadecimal minúsculo.')
    return value


def require_sha1(value: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise CoreError(f'{field} deve ser um hash SHA-1 hexadecimal minúsculo.')
    return value


def require_timestamp(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z', value,
    ):
        raise CoreError('Data deve estar em UTC no formato RFC 3339.')
    try:
        datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as error:
        raise CoreError('Data UTC inválida.') from error
    return value


@dataclass(frozen=True)
class Event:
    sequence: int
    kind: str
    created_at: str

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence <= 0 or self.kind != 'RUN_CREATED':
            raise CoreError('Evento inicial inválido.')
        require_timestamp(self.created_at)

    def to_dict(self) -> dict:
        return {'sequence': self.sequence, 'kind': self.kind, 'created_at': self.created_at}


@dataclass(frozen=True)
class BlockedEvent:
    sequence: int
    created_at: str
    reason: str
    request_id: str
    authorization_id: str
    kind: str = 'RUN_BLOCKED'

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence <= 0 or self.kind != 'RUN_BLOCKED':
            raise CoreError('Evento de bloqueio inválido.')
        require_timestamp(self.created_at)
        require_text(self.reason, 'Motivo do bloqueio')
        require_text(self.request_id, 'Identificador do bloqueio')
        require_run_id(self.authorization_id)

    def to_dict(self):
        return dict(sequence=self.sequence, kind=self.kind, created_at=self.created_at,
                    from_state='PENDING', to_state='BLOCKED', revision=1,
                    reason=self.reason, request_id=self.request_id,
                    authorization_id=self.authorization_id)


@dataclass(frozen=True)
class UnblockedEvent:
    sequence: int
    created_at: str
    reason: str
    request_id: str
    authorization_id: str
    blocked_event_sequence: int
    kind: str = 'RUN_UNBLOCKED'

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence <= 0 or type(self.blocked_event_sequence) is not int or self.blocked_event_sequence <= 0 or self.kind != 'RUN_UNBLOCKED':
            raise CoreError('Evento de desbloqueio inválido.')
        require_timestamp(self.created_at)
        require_text(self.reason, 'Motivo do desbloqueio')
        require_text(self.request_id, 'Identificador do desbloqueio')
        if require_run_id(self.authorization_id) != self.authorization_id:
            raise CoreError('Identificador de autorização inválido.')

    def to_dict(self):
        return dict(sequence=self.sequence, kind=self.kind, created_at=self.created_at,
                    from_state='BLOCKED', to_state='PENDING', revision=2, reason=self.reason,
                    request_id=self.request_id, authorization_id=self.authorization_id,
                    blocked_event_sequence=self.blocked_event_sequence)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    run_id: str
    request_id: str
    kind: str
    verdict: str
    artifact_digest: str
    evidence_sha256: str
    collector_route: str
    summary_ref: str
    created_at: str
    at_version: str | None = None
    commit_sha: str | None = None

    def __post_init__(self):
        require_canonical_uuid(self.evidence_id, 'Identificador da evidência')
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        require_text(self.request_id, 'Identificador da requisição de evidência')
        if self.kind not in VALID_EVIDENCE_KINDS:
            raise CoreError('Tipo de evidência inválido.')
        if self.verdict not in VALID_EVIDENCE_VERDICTS:
            raise CoreError('Veredito de evidência inválido.')
        require_sha256(self.artifact_digest, 'Digest do artefato')
        require_sha256(self.evidence_sha256, 'Hash SHA-256 da evidência')
        if self.at_version is not None:
            require_text(self.at_version, 'Versão dos testes de aceite')
        if self.commit_sha is not None:
            require_sha1(self.commit_sha, 'Commit SHA')
        if self.collector_route not in VALID_COLLECTOR_ROUTES:
            raise CoreError('Rota de coleta de evidência inválida.')
        require_text(self.summary_ref, 'Referência do resumo da evidência')
        require_timestamp(self.created_at)

    def to_dict(self) -> dict:
        return {
            'evidence_id': self.evidence_id,
            'run_id': self.run_id,
            'request_id': self.request_id,
            'kind': self.kind,
            'verdict': self.verdict,
            'artifact_digest': self.artifact_digest,
            'evidence_sha256': self.evidence_sha256,
            'at_version': self.at_version,
            'commit_sha': self.commit_sha,
            'collector_route': self.collector_route,
            'summary_ref': self.summary_ref,
            'created_at': self.created_at,
        }


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    run_revision: int
    request_id: str
    last_confirmed_step: str
    created_at: str
    base_commit_sha: str | None = None
    head_commit_sha: str | None = None
    worktree_snapshot_sha256: str | None = None
    evidence_hashes: tuple = ()
    external_idempotency_keys: tuple = ()
    unknown_pendencies: tuple = ()

    def __post_init__(self):
        require_canonical_uuid(self.checkpoint_id, 'Identificador do checkpoint')
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        if type(self.run_revision) is not int or self.run_revision < 0:
            raise CoreError('Revisão do checkpoint deve ser inteiro não negativo.')
        require_text(self.request_id, 'Identificador da requisição do checkpoint')
        require_text(self.last_confirmed_step, 'Último passo confirmado')
        if self.base_commit_sha is not None:
            require_sha1(self.base_commit_sha, 'Commit base')
        if self.head_commit_sha is not None:
            require_sha1(self.head_commit_sha, 'Commit HEAD')
        if self.worktree_snapshot_sha256 is not None:
            require_sha256(self.worktree_snapshot_sha256, 'Hash do snapshot do worktree')
        if not isinstance(self.evidence_hashes, tuple):
            raise CoreError('Lista de hashes de evidência deve ser imutável.')
        if not isinstance(self.external_idempotency_keys, tuple):
            raise CoreError('Lista de chaves idempotentes externas deve ser imutável.')
        if not isinstance(self.unknown_pendencies, tuple):
            raise CoreError('Lista de pendências UNKNOWN deve ser imutável.')
        require_timestamp(self.created_at)

    @property
    def has_unknown_pendencies(self) -> bool:
        return bool(self.unknown_pendencies)

    def to_dict(self) -> dict:
        return {
            'checkpoint_id': self.checkpoint_id,
            'run_id': self.run_id,
            'run_revision': self.run_revision,
            'request_id': self.request_id,
            'last_confirmed_step': self.last_confirmed_step,
            'base_commit_sha': self.base_commit_sha,
            'head_commit_sha': self.head_commit_sha,
            'worktree_snapshot_sha256': self.worktree_snapshot_sha256,
            'evidence_hashes': list(self.evidence_hashes),
            'external_idempotency_keys': list(self.external_idempotency_keys),
            'unknown_pendencies': list(self.unknown_pendencies),
            'created_at': self.created_at,
        }


@dataclass(frozen=True)
class Transition:
    sequence: int
    run_id: str
    revision: int
    operation: str
    kind: str
    from_state: str | None
    to_state: str
    request_id: str
    created_at: str
    actor_route: str
    reason: str | None = None
    reason_code: str | None = None
    block_category: str | None = None
    next_action: str | None = None
    authorization_id: str | None = None
    evidence_id: str | None = None
    legacy_meta: dict | None = None

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence <= 0:
            raise CoreError('Sequência da transição inválida.')
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        if type(self.revision) is not int or self.revision < 0:
            raise CoreError('Revisão da transição inválida.')
        if self.operation not in VALID_V4_OPERATIONS or self.kind not in VALID_V4_KINDS:
            raise CoreError('Operação ou tipo de transição inválido.')
        if self.to_state not in VALID_V4_STATES:
            raise CoreError('Estado de destino da transição inválido.')
        require_text(self.request_id, 'Identificador da requisição da transição')
        require_timestamp(self.created_at)
        if self.actor_route not in VALID_ACTOR_ROUTES:
            raise CoreError('Rota de autoria da transição inválida.')
        if self.authorization_id is not None:
            require_canonical_uuid(self.authorization_id, 'Identificador de autorização')
        if self.evidence_id is not None:
            require_canonical_uuid(self.evidence_id, 'Identificador da evidência')
        if self.legacy_meta is not None and not isinstance(self.legacy_meta, dict):
            raise CoreError('Metadados legados inválidos.')
        if self.operation == 'CREATE_RUN':
            if (self.kind != 'RUN_CREATED' or self.from_state is not None
                    or self.to_state != 'PENDING' or self.revision != 0
                    or self.block_category is not None or self.evidence_id is not None):
                raise CoreError('Transição CREATE_RUN inconsistente.')
        elif self.operation == 'START_RUN':
            if (self.kind != 'RUN_STARTED' or self.from_state != 'PENDING'
                    or self.to_state != 'IN_PROGRESS' or self.revision <= 0
                    or self.block_category is not None or self.evidence_id is not None):
                raise CoreError('Transição START_RUN inconsistente.')
        elif self.operation == 'BLOCK_RUN':
            if (self.kind != 'RUN_BLOCKED' or self.from_state not in ('PENDING', 'IN_PROGRESS')
                    or self.to_state != 'BLOCKED' or self.revision <= 0
                    or self.block_category not in VALID_BLOCK_CATEGORIES
                    or self.evidence_id is not None):
                raise CoreError('Transição BLOCK_RUN inconsistente.')
            require_text(self.reason, 'Motivo do bloqueio')
            require_text(self.reason_code, 'Código do motivo do bloqueio')
            require_text(self.next_action, 'Próxima ação segura')
        elif self.operation == 'UNBLOCK_RUN':
            if (self.kind != 'RUN_UNBLOCKED' or self.from_state != 'BLOCKED'
                    or self.to_state != 'PENDING' or self.revision <= 0
                    or self.block_category is not None or self.evidence_id is not None):
                raise CoreError('Transição UNBLOCK_RUN inconsistente.')
            require_text(self.reason, 'Motivo do desbloqueio')
        elif self.operation == 'COMPLETE_RUN':
            if (self.kind != 'RUN_COMPLETED' or self.from_state != 'IN_PROGRESS'
                    or self.to_state != 'DONE' or self.revision <= 0
                    or self.block_category is not None or self.evidence_id is None):
                raise CoreError('Transição COMPLETE_RUN inconsistente.')
        for optional_field, label in (
            (self.reason, 'Motivo'),
            (self.reason_code, 'Código do motivo'),
            (self.next_action, 'Próxima ação'),
        ):
            if optional_field is not None:
                require_text(optional_field, label)

    def to_dict(self) -> dict:
        result = {
            'sequence': self.sequence,
            'run_id': self.run_id,
            'revision': self.revision,
            'operation': self.operation,
            'kind': self.kind,
            'from_state': self.from_state,
            'to_state': self.to_state,
            'request_id': self.request_id,
            'created_at': self.created_at,
            'actor_route': self.actor_route,
        }
        if self.reason is not None:
            result['reason'] = self.reason
        if self.reason_code is not None:
            result['reason_code'] = self.reason_code
        if self.block_category is not None:
            result['block_category'] = self.block_category
        if self.next_action is not None:
            result['next_action'] = self.next_action
        if self.authorization_id is not None:
            result['authorization_id'] = self.authorization_id
        if self.evidence_id is not None:
            result['evidence_id'] = self.evidence_id
        if self.legacy_meta is not None:
            result['legacy_meta'] = dict(self.legacy_meta)
        return result


@dataclass(frozen=True)
class Run:
    run_id: str
    request_id: str
    spec_id: str
    spec_version: str
    state: str
    created_at: str
    events: tuple[Event | BlockedEvent | UnblockedEvent | Transition, ...]
    schema_version: int = 1
    revision: int | None = None
    ticket_id: str | None = None
    mode: str | None = None
    updated_at: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()

    def __post_init__(self):
        require_run_id(self.run_id)
        require_text(self.request_id, 'Identificador da requisição')
        require_text(self.spec_id, 'Identificador da especificação')
        require_text(self.spec_version, 'Versão da especificação')
        require_timestamp(self.created_at)
        if type(self.schema_version) is not int or self.schema_version not in (1, 2, 3, 4):
            raise CoreError('Versão do domínio inválida.')
        if self.schema_version in (1, 2, 3):
            if (self.ticket_id is not None or self.mode is not None or self.updated_at is not None
                    or self.evidence_refs or self.checkpoints):
                raise CoreError('Campos exclusivos de v4 não são permitidos em v1–v3.')
            if not self.events or not isinstance(self.events[0], Event):
                raise CoreError('Evento inicial ausente.')
            if self.schema_version == 1:
                if self.state != 'PENDING' or len(self.events) != 1 or self.revision is not None:
                    raise CoreError('Execução deve estar PENDING com um único evento inicial.')
            elif type(self.revision) is not int:
                raise CoreError('Revisão deve ser inteira.')
            elif self.state == 'PENDING' and self.revision == 0 and len(self.events) == 1:
                pass
            elif (self.state == 'BLOCKED' and self.revision == 1 and len(self.events) == 2
                    and isinstance(self.events[1], BlockedEvent)
                    and self.events[1].sequence > self.events[0].sequence):
                pass
            elif (self.schema_version == 3 and self.state == 'PENDING' and self.revision == 2
                    and len(self.events) == 3 and isinstance(self.events[1], BlockedEvent)
                    and isinstance(self.events[2], UnblockedEvent)
                    and self.events[0].sequence < self.events[1].sequence < self.events[2].sequence
                    and self.events[2].blocked_event_sequence == self.events[1].sequence):
                pass
            else:
                raise CoreError('Estado, revisão e journal inconsistentes.')
            return

        require_canonical_uuid(self.run_id, 'Identificador da execução')
        if type(self.revision) is not int or self.revision < 0:
            raise CoreError('Revisão v4 deve ser inteira não negativa.')
        if self.state not in VALID_V4_STATES:
            raise CoreError('Estado v4 inválido.')
        if self.mode not in VALID_MODES:
            raise CoreError('Modo adaptativo inválido.')
        if self.ticket_id is not None:
            require_text(self.ticket_id, 'Identificador do ticket')
        if self.updated_at is None:
            raise CoreError('Data de atualização obrigatória em v4.')
        require_timestamp(self.updated_at)
        if not self.events or len(self.events) != self.revision + 1:
            raise CoreError('Ledger de transições v4 inconsistente com a revisão.')
        evidence_by_id: dict[str, EvidenceRef] = {}
        for ev in self.evidence_refs:
            if not isinstance(ev, EvidenceRef) or ev.run_id != self.run_id:
                raise CoreError('Referência de evidência incompatível com a execução.')
            if ev.evidence_id in evidence_by_id:
                raise CoreError('Identificador de evidência duplicado.')
            evidence_by_id[ev.evidence_id] = ev
        for idx, transition in enumerate(self.events):
            if not isinstance(transition, Transition) or transition.run_id != self.run_id:
                raise CoreError('Transição incompatível com a execução v4.')
            if transition.revision != idx:
                raise CoreError('Revisões do ledger v4 devem ser contíguas a partir de 0.')
            if idx == 0:
                if transition.operation != 'CREATE_RUN' or transition.to_state != 'PENDING':
                    raise CoreError('Transição inicial v4 deve ser CREATE_RUN.')
            else:
                previous = self.events[idx - 1]
                if transition.sequence <= previous.sequence:
                    raise CoreError('Sequências do ledger v4 devem ser estritamente crescentes.')
                if transition.from_state != previous.to_state:
                    raise CoreError('Cadeia de estados do ledger v4 descontínua.')
            if transition.operation == 'COMPLETE_RUN':
                ev = evidence_by_id.get(transition.evidence_id or '')
                if ev is None or ev.verdict != 'PASS' or ev.kind not in COMPLETION_EVIDENCE_KINDS:
                    raise CoreError('Conclusão v4 exige evidência oficial PASS da mesma execução.')
        if self.events[-1].to_state != self.state or self.events[-1].created_at != self.updated_at:
            raise CoreError('Estado ou data de atualização divergente da última transição v4.')
        for cp in self.checkpoints:
            if not isinstance(cp, Checkpoint) or cp.run_id != self.run_id or cp.run_revision > self.revision:
                raise CoreError('Checkpoint incompatível com a execução ou revisão atual.')

    @property
    def has_passing_evidence(self) -> bool:
        return any(
            ev.verdict == 'PASS' and ev.kind in COMPLETION_EVIDENCE_KINDS
            for ev in self.evidence_refs
        )

    @property
    def has_unknown_pendencies(self) -> bool:
        return bool(self.checkpoints and self.checkpoints[-1].has_unknown_pendencies)

    def to_dict(self) -> dict:
        result = {'run_id': self.run_id, 'request_id': self.request_id,
                  'spec_id': self.spec_id, 'spec_version': self.spec_version,
                  'state': self.state, 'created_at': self.created_at,
                  'events': [event.to_dict() for event in self.events]}

        if self.schema_version in (2, 3):
            result.update(schema_version=self.schema_version, revision=self.revision)
        elif self.schema_version == 4:
            result.update(
                schema_version=4,
                revision=self.revision,
                ticket_id=self.ticket_id,
                mode=self.mode,
                updated_at=self.updated_at,
                evidence_refs=[ev.to_dict() for ev in self.evidence_refs],
                checkpoints=[cp.to_dict() for cp in self.checkpoints],
            )
        return result
