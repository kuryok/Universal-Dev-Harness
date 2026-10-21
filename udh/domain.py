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
class Run:
    run_id: str
    request_id: str
    spec_id: str
    spec_version: str
    state: str
    created_at: str
    events: tuple[Event, ...]

    def __post_init__(self):
        require_run_id(self.run_id)
        require_text(self.request_id, 'Identificador da requisição')
        require_text(self.spec_id, 'Identificador da especificação')
        require_text(self.spec_version, 'Versão da especificação')
        require_timestamp(self.created_at)
        if self.state != 'PENDING' or len(self.events) != 1:
            raise CoreError('Execução deve estar PENDING com um único evento inicial.')

    def to_dict(self) -> dict:
        return {'run_id': self.run_id, 'request_id': self.request_id,
                'spec_id': self.spec_id, 'spec_version': self.spec_version,
                'state': self.state, 'created_at': self.created_at,
                'events': [event.to_dict() for event in self.events]}
