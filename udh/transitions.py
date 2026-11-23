"""Regras puras da transição única; contexto não é credencial de acesso."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from .domain import (
    COMPLETION_EVIDENCE_KINDS,
    CoreError,
    Run,
    VALID_ACTOR_ROUTES,
    VALID_BLOCK_CATEGORIES,
    VALID_COLLECTOR_ROUTES,
    VALID_EVIDENCE_KINDS,
    VALID_EVIDENCE_VERDICTS,
    require_canonical_uuid,
    require_run_id,
    require_sha1,
    require_sha256,
    require_text,
)


class ConflictError(CoreError):
    """Estado, revisão ou chave de requisição conflitam com o pedido."""


class AuthorizationError(CoreError):
    """A entrada não recebeu o contexto administrativo esperado."""


def operator_context_valid(context):
    # A autoridade real é o domínio do SO que entrega o banco ao controlador.
    # Validar estes metadados evita configuração ambígua; não autentica um JSON.
    return (isinstance(context, Mapping)
            and type(context.get('schema')) is int and context['schema'] in (1, 2)
            and context.get('db_path') == '/state/udh.sqlite'
            and context.get('controller_route') == 'OPERATOR_LOCAL'
            and type(context.get('authorization_rule_version')) is int
            and context['authorization_rule_version'] == 1
            and context.get('allowed_operations') == (('MIGRATE_V2', 'BLOCK_PENDING') if context['schema'] == 1 else ('MIGRATE_V2', 'BLOCK_PENDING', 'MIGRATE_V3', 'UNBLOCK_RUN'))
            and all(isinstance(context.get(key), str) and re.fullmatch('[0-9a-f]{64}', context[key])
                    for key in ('core_manifest_sha256', 'control_sha256')))


@dataclass(frozen=True)
class BlockRequest:
    run_id: str
    expected_revision: int
    request_id: str
    reason: str

    def __post_init__(self):
        require_run_id(self.run_id)
        require_text(self.request_id, 'Identificador do bloqueio')
        require_text(self.reason, 'Motivo do bloqueio')
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise CoreError('Revisão esperada deve ser um inteiro não negativo.')

    def check_replay(self, *, run_id, expected_revision, reason):
        if (self.run_id, self.expected_revision, self.reason) != (run_id, expected_revision, reason):
            raise ConflictError('Requisição de bloqueio já utilizada com outro conteúdo.')

    def check_pending(self, run: Run):
        if run.state != 'PENDING' or run.revision != self.expected_revision or self.expected_revision != 0:
            raise ConflictError('Estado ou revisão não permite este bloqueio.')


@dataclass(frozen=True)
class UnblockRequest:
    run_id: str
    expected_revision: int
    request_id: str
    reason: str

    def __post_init__(self):
        require_run_id(self.run_id)
        require_text(self.request_id, 'Identificador do desbloqueio')
        require_text(self.reason, 'Motivo do desbloqueio')
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise CoreError('Revisão esperada deve ser um inteiro não negativo.')

    def check_replay(self, *, run_id, expected_revision, reason):
        if (self.run_id, self.expected_revision, self.reason) != (run_id, expected_revision, reason):
            raise ConflictError('Requisição de desbloqueio já utilizada com outro conteúdo.')

    def check_blocked(self, run: Run):
        if (run.state != 'BLOCKED' or run.revision != self.expected_revision
                or self.expected_revision != 1 or len(run.events) != 2):
            raise ConflictError('Estado ou revisão não permite este desbloqueio.')


def _require_expected_revision(value: int) -> int:
    if type(value) is not int or value < 0:
        raise CoreError('Revisão esperada deve ser um inteiro não negativo.')
    return value


@dataclass(frozen=True)
class StartRequest:
    run_id: str
    expected_revision: int
    request_id: str
    actor_route: str = 'CORE_LOCAL'

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        _require_expected_revision(self.expected_revision)
        require_text(self.request_id, 'Identificador da requisição de início')
        if self.actor_route not in VALID_ACTOR_ROUTES:
            raise CoreError('Rota de autoria inválida.')

    def check_replay(self, *, run_id: str, revision: int, actor_route: str):
        if (self.run_id, self.expected_revision, self.actor_route) != (run_id, revision - 1, actor_route):
            raise ConflictError('Requisição de início já utilizada com outro conteúdo.')

    def check_state(self, run: Run):
        if run.state != 'PENDING' or run.revision != self.expected_revision:
            raise ConflictError('Estado ou revisão não permite iniciar a execução.')
        if run.has_unknown_pendencies:
            raise ConflictError('Pendência UNKNOWN ativa exige reconciliação antes de iniciar.')


@dataclass(frozen=True)
class BlockV4Request:
    run_id: str
    expected_revision: int
    request_id: str
    category: str
    reason_code: str
    reason: str
    next_action: str
    actor_route: str = 'OPERATOR_LOCAL'

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        _require_expected_revision(self.expected_revision)
        require_text(self.request_id, 'Identificador da requisição de bloqueio')
        if self.category not in VALID_BLOCK_CATEGORIES:
            raise CoreError('Categoria de bloqueio inválida.')
        require_text(self.reason_code, 'Código do motivo do bloqueio')
        require_text(self.reason, 'Motivo do bloqueio')
        require_text(self.next_action, 'Próxima ação segura')
        if self.actor_route not in VALID_ACTOR_ROUTES:
            raise CoreError('Rota de autoria inválida.')

    def check_replay(self, *, run_id: str, revision: int, category: str, reason_code: str,
                     reason: str, next_action: str, actor_route: str):
        if ((self.run_id, self.expected_revision, self.category, self.reason_code,
             self.reason, self.next_action, self.actor_route)
                != (run_id, revision - 1, category, reason_code, reason, next_action, actor_route)):
            raise ConflictError('Requisição de bloqueio já utilizada com outro conteúdo.')

    def check_state(self, run: Run):
        if run.state not in ('PENDING', 'IN_PROGRESS') or run.revision != self.expected_revision:
            raise ConflictError('Estado ou revisão não permite bloquear a execução.')


@dataclass(frozen=True)
class UnblockV4Request:
    run_id: str
    expected_revision: int
    request_id: str
    reason: str
    actor_route: str = 'OPERATOR_LOCAL'

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        _require_expected_revision(self.expected_revision)
        require_text(self.request_id, 'Identificador da requisição de desbloqueio')
        require_text(self.reason, 'Motivo do desbloqueio')
        if self.actor_route not in VALID_ACTOR_ROUTES:
            raise CoreError('Rota de autoria inválida.')

    def check_replay(self, *, run_id: str, revision: int, reason: str, actor_route: str):
        if (self.run_id, self.expected_revision, self.reason, self.actor_route) != (run_id, revision - 1, reason, actor_route):
            raise ConflictError('Requisição de desbloqueio já utilizada com outro conteúdo.')

    def check_state(self, run: Run):
        if run.state != 'BLOCKED' or run.revision != self.expected_revision:
            raise ConflictError('Estado ou revisão não permite desbloquear a execução.')


@dataclass(frozen=True)
class CompleteRequest:
    run_id: str
    expected_revision: int
    request_id: str
    evidence_id: str
    actor_route: str = 'TRUSTED_EXECUTOR'

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        _require_expected_revision(self.expected_revision)
        require_text(self.request_id, 'Identificador da requisição de conclusão')
        require_canonical_uuid(self.evidence_id, 'Identificador da evidência')
        if self.actor_route not in VALID_ACTOR_ROUTES:
            raise CoreError('Rota de autoria inválida.')

    def check_replay(self, *, run_id: str, revision: int, evidence_id: str, actor_route: str):
        if (self.run_id, self.expected_revision, self.evidence_id, self.actor_route) != (run_id, revision - 1, evidence_id, actor_route):
            raise ConflictError('Requisição de conclusão já utilizada com outro conteúdo.')

    def check_state(self, run: Run):
        if run.state != 'IN_PROGRESS' or run.revision != self.expected_revision:
            raise ConflictError('Estado ou revisão não permite concluir a execução.')
        if run.has_unknown_pendencies:
            raise ConflictError('Pendência UNKNOWN ativa impede concluir a execução.')
        evidence = next((ev for ev in run.evidence_refs if ev.evidence_id == self.evidence_id), None)
        if evidence is None:
            raise ConflictError('Evidência oficial não encontrada para esta execução.')
        if evidence.verdict != 'PASS' or evidence.kind not in COMPLETION_EVIDENCE_KINDS:
            raise ConflictError('Conclusão exige evidência oficial com veredito PASS e tipo verificador.')


@dataclass(frozen=True)
class EvidenceRequest:
    run_id: str
    request_id: str
    kind: str
    verdict: str
    artifact_digest: str
    evidence_sha256: str
    collector_route: str
    summary_ref: str
    at_version: str | None = None
    commit_sha: str | None = None

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        require_text(self.request_id, 'Identificador da requisição de evidência')
        if self.kind not in VALID_EVIDENCE_KINDS:
            raise CoreError('Tipo de evidência inválido.')
        if self.verdict not in VALID_EVIDENCE_VERDICTS:
            raise CoreError('Veredito de evidência inválido.')
        require_sha256(self.artifact_digest, 'Digest do artefato')
        require_sha256(self.evidence_sha256, 'Hash SHA-256 da evidência')
        if self.collector_route not in VALID_COLLECTOR_ROUTES:
            raise CoreError('Rota de coleta de evidência inválida.')
        require_text(self.summary_ref, 'Referência do resumo da evidência')
        if self.at_version is not None:
            require_text(self.at_version, 'Versão dos testes de aceite')
        if self.commit_sha is not None:
            require_sha1(self.commit_sha, 'Commit SHA')

    def check_replay(self, row):
        expected = (
            self.run_id, self.kind, self.verdict, self.artifact_digest,
            self.evidence_sha256, self.at_version, self.commit_sha,
            self.collector_route, self.summary_ref,
        )
        stored = (
            row['run_id'], row['kind'], row['verdict'], row['artifact_digest'],
            row['evidence_sha256'], row['at_version'], row['commit_sha'],
            row['collector_route'], row['summary_ref'],
        )
        if expected != stored:
            raise ConflictError('Requisição de evidência já utilizada com outro conteúdo.')


@dataclass(frozen=True)
class CheckpointRequest:
    run_id: str
    expected_revision: int
    request_id: str
    last_confirmed_step: str
    base_commit_sha: str | None = None
    head_commit_sha: str | None = None
    worktree_snapshot_sha256: str | None = None
    evidence_hashes: tuple = ()
    external_idempotency_keys: tuple = ()
    unknown_pendencies: tuple = ()

    def __post_init__(self):
        require_canonical_uuid(self.run_id, 'Identificador da execução')
        _require_expected_revision(self.expected_revision)
        require_text(self.request_id, 'Identificador da requisição do checkpoint')
        require_text(self.last_confirmed_step, 'Último passo confirmado')
        if self.base_commit_sha is not None:
            require_sha1(self.base_commit_sha, 'Commit base')
        if self.head_commit_sha is not None:
            require_sha1(self.head_commit_sha, 'Commit HEAD')
        if self.worktree_snapshot_sha256 is not None:
            require_sha256(self.worktree_snapshot_sha256, 'Hash do snapshot do worktree')
        if not isinstance(self.evidence_hashes, tuple):
            raise CoreError('Lista de hashes de evidência inválida.')
        if not isinstance(self.external_idempotency_keys, tuple):
            raise CoreError('Lista de chaves idempotentes externas inválida.')
        if not isinstance(self.unknown_pendencies, tuple):
            raise CoreError('Lista de pendências UNKNOWN inválida.')

    def check_replay(self, row, *, evidence_hashes_json: str, external_keys_json: str, unknown_json: str):
        expected = (
            self.run_id, self.expected_revision, self.last_confirmed_step,
            self.base_commit_sha, self.head_commit_sha, self.worktree_snapshot_sha256,
            evidence_hashes_json, external_keys_json, unknown_json,
        )
        stored = (
            row['run_id'], row['run_revision'], row['last_confirmed_step'],
            row['base_commit_sha'], row['head_commit_sha'], row['worktree_snapshot_sha256'],
            row['evidence_hashes_json'], row['external_idempotency_keys_json'], row['unknown_pendencies_json'],
        )
        if expected != stored:
            raise ConflictError('Requisição de checkpoint já utilizada com outro conteúdo.')

    def check_state(self, run: Run):
        if run.revision != self.expected_revision:
            raise ConflictError('Revisão atual diverge da revisão esperada para o checkpoint.')
