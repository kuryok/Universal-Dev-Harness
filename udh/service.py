"""Casos de uso do Core; identidade e validação antes da persistência."""
from datetime import datetime, timezone
from uuid import uuid4

from .domain import Run, require_run_id, require_text
from .store import SQLiteStore
from .policy import evaluate
from .transitions import BlockRequest, AuthorizationError, operator_context_valid


class CoreService:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def initialize(self) -> int:
        return self.store.initialize()

    def create(self, spec_id: str, spec_version: str, request_id: str) -> Run:
        require_text(spec_id, 'Identificador da especificação')
        require_text(spec_version, 'Versão da especificação')
        require_text(request_id, 'Identificador da requisição')
        return self.store.create(
            run_id=str(uuid4()), request_id=request_id,
            spec_id=spec_id, spec_version=spec_version,
            created_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'),
        )

    def show(self, run_id: str) -> Run:
        return self.store.get(require_run_id(run_id))

    def policy(self, run_id: str, action: str) -> dict:
        require_text(action, 'Ação da política')
        return evaluate(self.show(run_id), action)

    def migrate(self) -> dict:
        return self.store.migrate()

    def block(self, run_id, expected_revision, request_id, reason, *, context):
        if not operator_context_valid(context):
            raise AuthorizationError('Operação exige a entrada administrativa protegida.')
        request = BlockRequest(require_run_id(run_id), expected_revision, request_id, reason)
        return self.store.block(request, created_at=datetime.now(timezone.utc).isoformat(
            timespec='microseconds').replace('+00:00', 'Z'),
            authorization_id=str(uuid4()), context=context)
