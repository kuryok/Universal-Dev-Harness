"""Regras puras da transição única; contexto não é credencial de acesso."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from .domain import CoreError, Run, require_run_id, require_text


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
