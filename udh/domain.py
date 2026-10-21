"""Dados e invariantes do estado inicial; sem acesso a persistência."""
from dataclasses import dataclass
from datetime import datetime
import re
from uuid import UUID


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
class Run:
    run_id: str
    request_id: str
    spec_id: str
    spec_version: str
    state: str
    created_at: str
    events: tuple[Event | BlockedEvent, ...]
    schema_version: int = 1
    revision: int | None = None

    def __post_init__(self):
        require_run_id(self.run_id)
        require_text(self.request_id, 'Identificador da requisição')
        require_text(self.spec_id, 'Identificador da especificação')
        require_text(self.spec_version, 'Versão da especificação')
        require_timestamp(self.created_at)
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise CoreError('Versão do domínio inválida.')
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
        else:
            raise CoreError('Estado, revisão e journal inconsistentes.')

    def to_dict(self) -> dict:
        result = {'run_id': self.run_id, 'request_id': self.request_id,
                'spec_id': self.spec_id, 'spec_version': self.spec_version,
                'state': self.state, 'created_at': self.created_at,
                'events': [event.to_dict() for event in self.events]}

        if self.schema_version == 2:
            result.update(schema_version=2, revision=self.revision)
        return result
