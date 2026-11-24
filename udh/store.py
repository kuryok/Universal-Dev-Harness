"""Persistência transacional do Core em SQLite (v1–v8)."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .domain import (
    BlockedEvent,
    Checkpoint,
    CoreError,
    Event,
    EvidenceRef,
    Run,
    Transition,
    UnblockedEvent,
    require_run_id,
    strict_json_loads,
)
from . import migrations
from .ticket_store import TicketStore, check_contract_integrity, check_managed_integrity
from .transitions import ConflictError, EvidenceRequest


_NONEMPTY = "length(trim({field}, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0"
_LEDGER_VERSIONS = (4, 5, 6, 7, 8)
_UTC = "length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL"
_RUNS_SQL = f"""CREATE TABLE runs (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK ({_NONEMPTY.format(field='request_id')}),
    spec_id TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_id')}),
    spec_version TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_version')}),
    state TEXT NOT NULL CHECK (state = 'PENDING'),
    created_at TEXT NOT NULL CHECK ({_UTC})
)"""
_EVENTS_SQL = f"""CREATE TABLE events (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL CHECK (kind = 'RUN_CREATED'),
    created_at TEXT NOT NULL CHECK ({_UTC}),
    UNIQUE (run_id, kind)
)"""


def _valid_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def _canonical(sql: str) -> str:
    # Espaços dentro de literais SQL fazem parte das regras de integridade.
    return sql.strip()


class SQLiteStore:
    """Mantém a versão do esquema e operações atômicas de execução."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connection(self, mode: str):
        connection = None
        try:
            uri = self.path.absolute().as_uri() + '?mode=' + mode
            connection = sqlite3.connect(uri, uri=True, timeout=2, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            yield connection
        except sqlite3.Error as error:
            if isinstance(error, sqlite3.IntegrityError):
                message = 'Gravação recusada por uma regra de integridade do banco.'
            elif isinstance(error, sqlite3.OperationalError):
                message = 'Banco indisponível para a operação; confira caminho, permissões e operações em andamento.'
            else:
                message = 'Não foi possível ler ou gravar o banco SQLite.'
            raise CoreError(message) from error
        except (OSError, ValueError) as error:
            if isinstance(error, CoreError):
                raise
            raise CoreError('Caminho do banco inválido.') from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection, *, v1_only=False) -> int:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version == 4 and not v1_only:
            migrations.validate_schema_v4(connection)
            return 4
        if version == 5 and not v1_only:
            migrations.validate_schema_v5(connection)
            return 5
        if version == 6 and not v1_only:
            migrations.validate_schema_v6(connection)
            return 6
        if version == 7 and not v1_only:
            migrations.validate_schema_v7(connection)
            return 7
        if version == 8 and not v1_only:
            migrations.validate_schema_v8(connection)
            return 8
        if version in (2, 3) and not v1_only:
            migrations.validate_schema(connection, version)
            migrations.check_integrity(connection, SQLiteStore, version)
            return version
        if version != 1:
            raise CoreError('Banco não inicializado ou versão do esquema incompatível.')
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()
        tables = {row['name']: row['sql'] for row in objects if row['type'] == 'table'}
        expected = {'runs': _RUNS_SQL, 'events': _EVENTS_SQL}
        if set(tables) != set(expected) or any(
            _canonical(tables[name] or '') != _canonical(sql)
            for name, sql in expected.items()
        ):
            raise CoreError('Esquema SQLite incompatível ou incompleto.')
        if any(row['type'] != 'table' and not (
            row['type'] == 'trigger' and row['tbl_name'] in expected
        ) for row in objects):
            raise CoreError('Banco contém objetos incompatíveis com o esquema UDH.')

        return 1

    def migrate(self) -> dict:
        return migrations.migrate(self)

    def migrate_v3(self) -> dict:
        return migrations.migrate_v3(self)

    def migrate_v4(self) -> dict:
        return migrations.migrate_v4(self)

    def migrate_v5(self) -> dict:
        return migrations.migrate_v5(self)

    def migrate_v6(self) -> dict:
        return migrations.migrate_v6(self)

    def migrate_v7(self) -> dict:
        return migrations.migrate_v7(self)

    def migrate_v8(self) -> dict:
        return migrations.migrate_v8(self)

    _check_managed_integrity = staticmethod(check_managed_integrity)
    _check_contract_integrity = staticmethod(check_contract_integrity)

    def _check_acceptance_integrity(self, connection):
        TicketStore(self).check_acceptance_integrity(connection)

    def initialize(self, *, schema_version: int = 1) -> int:
        if type(schema_version) is not int or schema_version not in (1, 4):
            raise CoreError('Versão de inicialização inválida; use 1 ou 4.')
        with self._connection('rwc') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                objects = connection.execute('SELECT name FROM sqlite_master').fetchall()
                if version == 0 and not objects:
                    if schema_version == 1:
                        connection.execute(_RUNS_SQL)
                        connection.execute(_EVENTS_SQL)
                        connection.execute('PRAGMA user_version = 1')
                    else:
                        migrations.init_v4(connection)
                elif schema_version == 1:
                    self._validate_schema(connection, v1_only=True)
                else:
                    if version not in _LEDGER_VERSIONS:
                        raise CoreError('Banco existente não tem um esquema de ledger compatível.')
                    self._validate_schema(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return schema_version

    @staticmethod
    def _parse_json_list(raw: str, field: str) -> tuple:
        parsed = strict_json_loads(raw, field)
        if not isinstance(parsed, list):
            raise CoreError(f'{field} deve ser uma lista JSON.')
        return tuple(parsed)

    @staticmethod
    def _parse_legacy_meta(raw: str | None) -> dict | None:
        if raw is None:
            return None
        parsed = strict_json_loads(raw, 'Metadados legados')
        if not isinstance(parsed, dict):
            raise CoreError('Metadados legados devem ser objeto JSON.')
        return parsed

    @classmethod
    def _row_to_transition(cls, row) -> Transition:
        return Transition(
            sequence=row['sequence'],
            run_id=row['run_id'],
            revision=row['revision'],
            operation=row['operation'],
            kind=row['kind'],
            from_state=row['from_state'],
            to_state=row['to_state'],
            request_id=row['request_id'],
            created_at=row['created_at'],
            actor_route=row['actor_route'],
            reason=row['reason'],
            reason_code=row['reason_code'],
            block_category=row['block_category'],
            next_action=row['next_action'],
            authorization_id=row['authorization_id'],
            evidence_id=row['evidence_id'],
            legacy_meta=cls._parse_legacy_meta(row['legacy_meta_json']),
        )

    @staticmethod
    def _row_to_evidence(row) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=row['evidence_id'],
            run_id=row['run_id'],
            request_id=row['request_id'],
            kind=row['kind'],
            verdict=row['verdict'],
            artifact_digest=row['artifact_digest'],
            evidence_sha256=row['evidence_sha256'],
            at_version=row['at_version'],
            commit_sha=row['commit_sha'],
            collector_route=row['collector_route'],
            summary_ref=row['summary_ref'],
            created_at=row['created_at'],
        )

    @classmethod
    def _row_to_checkpoint(cls, row) -> Checkpoint:
        return Checkpoint(
            checkpoint_id=row['checkpoint_id'],
            run_id=row['run_id'],
            run_revision=row['run_revision'],
            request_id=row['request_id'],
            last_confirmed_step=row['last_confirmed_step'],
            base_commit_sha=row['base_commit_sha'],
            head_commit_sha=row['head_commit_sha'],
            worktree_snapshot_sha256=row['worktree_snapshot_sha256'],
            evidence_hashes=cls._parse_json_list(row['evidence_hashes_json'], 'evidence_hashes_json'),
            external_idempotency_keys=cls._parse_json_list(row['external_idempotency_keys_json'], 'external_idempotency_keys_json'),
            unknown_pendencies=cls._parse_json_list(row['unknown_pendencies_json'], 'unknown_pendencies_json'),
            created_at=row['created_at'],
        )

    @classmethod
    def _load_v4(cls, connection: sqlite3.Connection, run_id: str) -> Run:
        row = connection.execute('SELECT * FROM "runs" WHERE id = ?', (run_id,)).fetchone()
        if row is None:
            raise CoreError('Execução não encontrada.')
        transitions = tuple(
            cls._row_to_transition(item)
            for item in connection.execute(
                'SELECT * FROM "transitions" WHERE run_id = ? ORDER BY revision',
                (run_id,),
            ).fetchall()
        )
        evidence_refs = tuple(
            cls._row_to_evidence(item)
            for item in connection.execute(
                'SELECT * FROM "evidence_refs" WHERE run_id = ? ORDER BY created_at, evidence_id',
                (run_id,),
            ).fetchall()
        )
        checkpoints = tuple(
            cls._row_to_checkpoint(item)
            for item in connection.execute(
                'SELECT * FROM "checkpoints" WHERE run_id = ? ORDER BY run_revision, created_at, checkpoint_id',
                (run_id,),
            ).fetchall()
        )
        return Run(
            run_id=row['id'],
            request_id=row['request_id'],
            spec_id=row['spec_id'],
            spec_version=row['spec_version'],
            state=row['state'],
            created_at=row['created_at'],
            events=transitions,
            schema_version=4,
            revision=row['revision'],
            ticket_id=row['ticket_id'],
            mode=row['mode'],
            updated_at=row['updated_at'],
            evidence_refs=evidence_refs,
            checkpoints=checkpoints,
        )

    @staticmethod
    def _load(connection: sqlite3.Connection, run_id: str, *, version=None) -> Run:
        if version is None:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version in _LEDGER_VERSIONS:
            return SQLiteStore._load_v4(connection, run_id)
        row = connection.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
        if row is None:
            raise CoreError('Execução não encontrada.')
        events = connection.execute(
            'SELECT sequence, kind, created_at FROM events WHERE run_id = ? ORDER BY sequence',
            (run_id,),
        ).fetchall()
        if not events or events[0]['kind'] != 'RUN_CREATED' or (version == 1 and len(events) != 1):
            raise CoreError('Execução com evento inicial ausente ou inválido.')
        parsed = [Event(sequence=events[0]['sequence'], kind=events[0]['kind'], created_at=events[0]['created_at'])]
        if version in (2, 3):
            requests = connection.execute('SELECT * FROM block_requests WHERE run_id = ?', (run_id,)).fetchall()
            if (len(events) == 2 or (version == 3 and len(events) == 3)) and len(requests) == 1:
                event, request = events[1], requests[0]
                if (event['kind'] != 'RUN_BLOCKED' or request['event_sequence'] != event['sequence']
                        or type(request['expected_revision']) is not int or request['expected_revision'] != 0
                        or request['controller_route'] != 'OPERATOR_LOCAL'
                        or type(request['authorization_rule_version']) is not int or request['authorization_rule_version'] != 1
                        or not _valid_sha256(request['core_manifest_sha256'])
                        or not _valid_sha256(request['control_sha256'])
                        or (version == 3 and (type(request['result_schema_version']) is not int
                                              or request['result_schema_version'] not in (2, 3)))):
                    raise CoreError('Vínculo de autorização inconsistente.')
                parsed.append(BlockedEvent(sequence=event['sequence'], created_at=event['created_at'],
                    reason=request['reason'], request_id=request['request_id'], authorization_id=request['authorization_id']))
            elif len(events) != 1 or requests:
                raise CoreError('Journal v2 ou pedido de bloqueio inconsistente.')
            if version == 3:
                unblocks = connection.execute('SELECT * FROM unblock_requests WHERE run_id = ?', (run_id,)).fetchall()
                if len(events) == 3 and len(unblocks) == 1 and len(requests) == 1:
                    event, request = events[2], unblocks[0]
                    if (event['kind'] != 'RUN_UNBLOCKED' or request['event_sequence'] != event['sequence']
                            or request['blocked_event_sequence'] != events[1]['sequence']
                            or type(request['expected_revision']) is not int or request['expected_revision'] != 1
                            or request['controller_route'] != 'OPERATOR_LOCAL'
                            or type(request['authorization_rule_version']) is not int or request['authorization_rule_version'] != 1
                            or not _valid_sha256(request['core_manifest_sha256'])
                            or not _valid_sha256(request['control_sha256'])):
                        raise CoreError('Vínculo de desbloqueio inconsistente.')
                    parsed.append(UnblockedEvent(sequence=event['sequence'], created_at=event['created_at'],
                        reason=request['reason'], request_id=request['request_id'], authorization_id=request['authorization_id'],
                        blocked_event_sequence=request['blocked_event_sequence']))
                elif len(events) == 3 or unblocks:
                    raise CoreError('Journal v3 ou pedido de desbloqueio inconsistente.')
            if require_run_id(row['id']) != row['id']:
                raise CoreError('Identificador de execução não canônico.')
            for request in requests + (unblocks if version == 3 else []):
                if require_run_id(request['authorization_id']) != request['authorization_id']:
                    raise CoreError('Identificador de autorização não canônico.')

        return Run(
            run_id=row['id'], request_id=row['request_id'], spec_id=row['spec_id'],
            spec_version=row['spec_version'], state=row['state'], created_at=row['created_at'],
            events=tuple(parsed), schema_version=version,
            revision=row['revision'] if version in (2, 3) else None,
        )

    @staticmethod
    def _insert_ledger_run(connection, *, run_id, request_id, spec_id, spec_version,
                           created_at, ticket_id, mode):
        connection.execute(
            'INSERT INTO "runs" (id, request_id, spec_id, spec_version, ticket_id, mode, state, created_at, updated_at, revision) '
            "VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, 0)",
            (run_id, request_id, spec_id, spec_version, ticket_id, mode, created_at, created_at),
        )
        connection.execute(
            'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, request_id, created_at, actor_route) '
            "VALUES (?, 0, 'CREATE_RUN', 'RUN_CREATED', NULL, 'PENDING', ?, ?, 'CORE_LOCAL')",
            (run_id, request_id, created_at),
        )

    def create(self, *, run_id: str, request_id: str, spec_id: str,
               spec_version: str, created_at: str,
               ticket_id: str | None = None, mode: str | None = None) -> Run:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version in (1, 2, 3):
                    if ticket_id is not None or mode is not None:
                        raise CoreError('ticket_id e mode exigem banco SQLite v4.')
                    existing = connection.execute(
                        'SELECT id, spec_id, spec_version FROM runs WHERE request_id = ?',
                        (request_id,),
                    ).fetchone()
                    if existing is not None:
                        if (existing['spec_id'], existing['spec_version']) != (spec_id, spec_version):
                            raise CoreError('Requisição já utilizada com outra referência de especificação.')
                        result = self._load(connection, existing['id'], version=version)
                    else:
                        extra_column, extra_value = (', revision', ', 0') if version in (2, 3) else ('', '')
                        connection.execute(
                            f'INSERT INTO runs (id, request_id, spec_id, spec_version, state, created_at{extra_column}) '
                            f"VALUES (?, ?, ?, ?, 'PENDING', ?{extra_value})",
                            (run_id, request_id, spec_id, spec_version, created_at),
                        )
                        connection.execute(
                            "INSERT INTO events (run_id, kind, created_at) VALUES (?, 'RUN_CREATED', ?)",
                            (run_id, created_at),
                        )
                        result = self._load(connection, run_id, version=version)
                else:
                    effective_mode = mode if mode is not None else 'STANDARD'
                    existing = connection.execute(
                        'SELECT id, spec_id, spec_version, ticket_id, mode FROM "runs" WHERE request_id = ?',
                        (request_id,),
                    ).fetchone()
                    if existing is not None:
                        if version >= 6 and connection.execute(
                            'SELECT 1 FROM managed_ticket_runs WHERE run_id = ?', (existing['id'],),
                        ).fetchone() is not None:
                            raise ConflictError('Requisição já utilizada para criar uma execução gerenciada.')
                        if (existing['spec_id'], existing['spec_version'], existing['ticket_id'], existing['mode']) != (
                            spec_id, spec_version, ticket_id, effective_mode,
                        ):
                            raise ConflictError('Requisição já utilizada com outra referência de especificação ou metadados.')
                        result = self._load_v4(connection, existing['id'])
                    else:
                        self._insert_ledger_run(
                            connection, run_id=run_id, request_id=request_id, spec_id=spec_id,
                            spec_version=spec_version, created_at=created_at, ticket_id=ticket_id, mode=effective_mode,
                        )
                        result = self._load_v4(connection, run_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return result

    def get(self, run_id: str) -> Run:
        with self._connection('ro') as connection:
            connection.execute('BEGIN')
            try:
                version = self._validate_schema(connection)
                result = self._load(connection, run_id, version=version)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return result

    def list_runs(self, limit: int = 100) -> list[Run]:
        if type(limit) is not int or limit < 1 or limit > 1000:
            raise CoreError('Limite de listagem inválido (esperado inteiro entre 1 e 1000).')
        with self._connection('ro') as connection:
            connection.execute('BEGIN')
            try:
                version = self._validate_schema(connection)
                rows = connection.execute(
                    'SELECT id FROM "runs" ORDER BY created_at DESC, id DESC LIMIT ?',
                    (limit,),
                ).fetchall()
                results = [self._load(connection, row['id'], version=version) for row in rows]
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return results

    def count_runs_by_state(self) -> dict[str, int]:
        counts = {'PENDING': 0, 'IN_PROGRESS': 0, 'BLOCKED': 0, 'DONE': 0}
        with self._connection('ro') as connection:
            connection.execute('BEGIN')
            try:
                self._validate_schema(connection)
                rows = connection.execute(
                    'SELECT state, COUNT(*) AS total FROM "runs" GROUP BY state'
                ).fetchall()
                for row in rows:
                    counts[row['state']] = int(row['total'])
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return counts

    def deep_check(self) -> dict:
        with self._connection('ro') as connection:
            connection.execute('BEGIN')
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version == 1:
                    self._validate_schema(connection, v1_only=True)
                    migrations.check_integrity(connection, SQLiteStore, 1)
                    runs_checked = connection.execute('SELECT count(*) FROM runs').fetchone()[0]
                elif version in (2, 3):
                    migrations.validate_schema(connection, version)
                    migrations.check_integrity(connection, SQLiteStore, version)
                    runs_checked = connection.execute('SELECT count(*) FROM "runs"').fetchone()[0]
                elif version in _LEDGER_VERSIONS:
                    self._validate_schema(connection)
                    runs_checked = migrations.check_integrity_v4(connection, SQLiteStore)
                    if version >= 6:
                        self._check_managed_integrity(connection, allow_started=version >= 8)
                    if version >= 7:
                        self._check_contract_integrity(connection)
                    if version >= 8:
                        self._check_acceptance_integrity(connection)
                else:
                    raise CoreError('Banco não inicializado ou versão do esquema incompatível.')
                connection.commit()
                return {
                    'schema_version': version,
                    'runs_checked': runs_checked,
                    'quick_check': 'ok',
                    'foreign_key_check': 'ok',
                }
            except BaseException:
                connection.rollback()
                raise

    def block(self, request, *, created_at, authorization_id, context):
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version not in (2, 3):
                    raise CoreError('Bloqueio exige migração explícita para SQLite v2.')
                existing = connection.execute(
                    'SELECT * FROM block_requests WHERE request_id = ?',
                    (request.request_id,),
                ).fetchone()
                if existing is not None:
                    request.check_replay(run_id=existing['run_id'],
                        expected_revision=existing['expected_revision'], reason=existing['reason'])
                else:
                    request.check_pending(self._load(connection, request.run_id))
                    event = connection.execute(
                        "INSERT INTO events (run_id, kind, created_at) VALUES (?, 'RUN_BLOCKED', ?)",
                        (request.run_id, created_at),
                    ).lastrowid
                    connection.execute(
                        'INSERT INTO block_requests (request_id, run_id, expected_revision, reason, '
                        'event_sequence, authorization_id, controller_route, core_manifest_sha256, '
                        'control_sha256, authorization_rule_version' + (', result_schema_version' if version == 3 else '') +
                        ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?' + (', 3' if version == 3 else '') + ')',
                        (request.request_id, request.run_id, request.expected_revision, request.reason,
                         event, authorization_id, context['controller_route'], context['core_manifest_sha256'],
                         context['control_sha256'], context['authorization_rule_version']),
                    )
                    changed = connection.execute(
                        "UPDATE runs SET state = 'BLOCKED', revision = 1 WHERE id = ? AND state = 'PENDING' AND revision = ?",
                        (request.run_id, request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação do bloqueio.')
                self._validate_schema(connection)
                run = self._load(connection, request.run_id)
                blocked = run.events[1]
                stored = connection.execute('SELECT * FROM block_requests WHERE request_id = ?', (request.request_id,)).fetchone()
                result = dict(schema_version=stored['result_schema_version'] if version == 3 else 2, request_id=blocked.request_id, run_id=run.run_id,
                    from_state='PENDING', to_state='BLOCKED', revision=1,
                    event_sequence=blocked.sequence, created_at=blocked.created_at,
                    authorization_id=blocked.authorization_id)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

    def unblock(self, request, *, created_at, authorization_id, context):
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version != 3:
                    raise CoreError('Desbloqueio exige migração explícita para SQLite v3.')
                existing = connection.execute(
                    'SELECT * FROM unblock_requests WHERE request_id = ?',
                    (request.request_id,),
                ).fetchone()
                if existing is not None:
                    request.check_replay(run_id=existing['run_id'],
                        expected_revision=existing['expected_revision'], reason=existing['reason'])
                else:
                    current = self._load(connection, request.run_id)
                    request.check_blocked(current)
                    blocked_event_sequence = current.events[1].sequence
                    event = connection.execute(
                        "INSERT INTO events (run_id, kind, created_at) VALUES (?, 'RUN_UNBLOCKED', ?)",
                        (request.run_id, created_at),
                    ).lastrowid
                    connection.execute(
                        'INSERT INTO unblock_requests (request_id, run_id, expected_revision, reason, '
                        'event_sequence, blocked_event_sequence, authorization_id, controller_route, '
                        'core_manifest_sha256, control_sha256, authorization_rule_version) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (request.request_id, request.run_id, request.expected_revision, request.reason,
                         event, blocked_event_sequence, authorization_id, context['controller_route'],
                         context['core_manifest_sha256'], context['control_sha256'],
                         context['authorization_rule_version']),
                    )
                    changed = connection.execute(
                        "UPDATE runs SET state = 'PENDING', revision = 2 WHERE id = ? AND state = 'BLOCKED' AND revision = ?",
                        (request.run_id, request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação do desbloqueio.')
                self._validate_schema(connection)
                run = self._load(connection, request.run_id)
                unblocked = run.events[2]
                result = dict(schema_version=3, request_id=unblocked.request_id, run_id=run.run_id,
                    from_state='BLOCKED', to_state='PENDING', revision=2,
                    event_sequence=unblocked.sequence, created_at=unblocked.created_at,
                    authorization_id=unblocked.authorization_id)
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

    def start_v4(self, request, *, created_at: str, trust_store=None) -> dict:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version not in _LEDGER_VERSIONS:
                    raise CoreError('Operação start exige banco SQLite v4.')
                existing = connection.execute(
                    'SELECT * FROM "transitions" WHERE operation = ? AND request_id = ?',
                    ('START_RUN', request.request_id),
                ).fetchone()
                if existing is not None:
                    request.check_replay(run_id=existing['run_id'], revision=existing['revision'],
                                         actor_route=existing['actor_route'])
                    self._load_v4(connection, existing['run_id'])
                    stored = self._row_to_transition(existing)
                else:
                    binding = TicketStore._binding(connection, request.run_id) if version >= 6 else None
                    if binding is not None:
                        TicketStore.require_start_context(version, trust_store)
                    current = self._load_v4(connection, request.run_id)
                    request.check_state(current)
                    if binding is not None:
                        TicketStore(self).authorize_start(connection, current, trust_store)
                    next_rev = current.revision + 1
                    connection.execute(
                        'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, actor_route) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (request.run_id, next_rev, 'START_RUN', 'RUN_STARTED', 'PENDING', 'IN_PROGRESS',
                         request.request_id, created_at, request.actor_route),
                    )
                    changed = connection.execute(
                        'UPDATE "runs" SET state = ?, revision = ?, updated_at = ? WHERE id = ? AND state = ? AND revision = ?',
                        ('IN_PROGRESS', next_rev, created_at, request.run_id, 'PENDING', request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação do início.')
                    run = self._load_v4(connection, request.run_id)
                    stored = run.events[-1]
                connection.commit()
                return {**stored.to_dict(), 'schema_version': 4}
            except BaseException:
                connection.rollback()
                raise

    def block_v4(self, request, *, created_at: str, authorization_id: str) -> dict:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                if self._validate_schema(connection) not in _LEDGER_VERSIONS:
                    raise CoreError('Operação block v4 exige banco SQLite v4.')
                existing = connection.execute(
                    'SELECT * FROM "transitions" WHERE operation = ? AND request_id = ?',
                    ('BLOCK_RUN', request.request_id),
                ).fetchone()
                if existing is not None:
                    request.check_replay(
                        run_id=existing['run_id'], revision=existing['revision'],
                        category=existing['block_category'], reason_code=existing['reason_code'],
                        reason=existing['reason'], next_action=existing['next_action'],
                        actor_route=existing['actor_route'],
                    )
                    self._load_v4(connection, existing['run_id'])
                    stored = self._row_to_transition(existing)
                else:
                    current = self._load_v4(connection, request.run_id)
                    request.check_state(current)
                    next_rev = current.revision + 1
                    connection.execute(
                        'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, reason, reason_code, block_category, next_action, authorization_id, actor_route) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (request.run_id, next_rev, 'BLOCK_RUN', 'RUN_BLOCKED', current.state, 'BLOCKED',
                         request.request_id, created_at, request.reason, request.reason_code,
                         request.category, request.next_action, authorization_id, request.actor_route),
                    )
                    changed = connection.execute(
                        'UPDATE "runs" SET state = ?, revision = ?, updated_at = ? WHERE id = ? AND state = ? AND revision = ?',
                        ('BLOCKED', next_rev, created_at, request.run_id, current.state, request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação do bloqueio.')
                    run = self._load_v4(connection, request.run_id)
                    stored = run.events[-1]
                connection.commit()
                return {**stored.to_dict(), 'schema_version': 4}
            except BaseException:
                connection.rollback()
                raise

    def unblock_v4(self, request, *, created_at: str, authorization_id: str) -> dict:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                if self._validate_schema(connection) not in _LEDGER_VERSIONS:
                    raise CoreError('Operação unblock v4 exige banco SQLite v4.')
                existing = connection.execute(
                    'SELECT * FROM "transitions" WHERE operation = ? AND request_id = ?',
                    ('UNBLOCK_RUN', request.request_id),
                ).fetchone()
                if existing is not None:
                    request.check_replay(
                        run_id=existing['run_id'], revision=existing['revision'],
                        reason=existing['reason'], actor_route=existing['actor_route'],
                    )
                    self._load_v4(connection, existing['run_id'])
                    stored = self._row_to_transition(existing)
                else:
                    current = self._load_v4(connection, request.run_id)
                    request.check_state(current)
                    next_rev = current.revision + 1
                    connection.execute(
                        'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, reason, authorization_id, actor_route) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (request.run_id, next_rev, 'UNBLOCK_RUN', 'RUN_UNBLOCKED', 'BLOCKED', 'PENDING',
                         request.request_id, created_at, request.reason, authorization_id, request.actor_route),
                    )
                    changed = connection.execute(
                        'UPDATE "runs" SET state = ?, revision = ?, updated_at = ? WHERE id = ? AND state = ? AND revision = ?',
                        ('PENDING', next_rev, created_at, request.run_id, 'BLOCKED', request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação do desbloqueio.')
                    run = self._load_v4(connection, request.run_id)
                    stored = run.events[-1]
                connection.commit()
                return {**stored.to_dict(), 'schema_version': 4}
            except BaseException:
                connection.rollback()
                raise

    def record_evidence_v4(self, request, *, evidence_id: str, created_at: str) -> dict:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version not in _LEDGER_VERSIONS:
                    raise CoreError('Registro de evidência exige banco SQLite v4.')
                if (version >= 6 and (request.kind == 'AT_RESULT' or request.verdict == 'PASS'
                                      or (version >= 8 and request.kind == 'REVIEW_REPORT'))
                        and TicketStore._binding(connection, request.run_id) is not None):
                    raise ConflictError('Evidência gerenciada exige relatório protegido do executor.')
                recorded = self._write_evidence(connection, request, evidence_id, created_at)
                connection.commit()
                return recorded
            except BaseException:
                connection.rollback()
                raise

    def _write_evidence(self, connection, request, evidence_id, created_at) -> dict:
        row = connection.execute('SELECT * FROM "evidence_refs" WHERE request_id = ?',
                                 (request.request_id,)).fetchone()
        if row is not None:
            request.check_replay(row)
        else:
            self._load_v4(connection, request.run_id)
            connection.execute(
                'INSERT INTO "evidence_refs" (evidence_id, run_id, request_id, kind, verdict, '
                'artifact_digest, evidence_sha256, at_version, commit_sha, collector_route, summary_ref, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (evidence_id, request.run_id, request.request_id, request.kind, request.verdict,
                 request.artifact_digest, request.evidence_sha256, request.at_version,
                 request.commit_sha, request.collector_route, request.summary_ref, created_at),
            )
            row = connection.execute('SELECT * FROM "evidence_refs" WHERE evidence_id = ?',
                                     (evidence_id,)).fetchone()
        self._load_v4(connection, request.run_id)
        return {**self._row_to_evidence(row).to_dict(), 'schema_version': 4}

    def record_managed_at_report(self, report, *, expected_revision, trust_store, evidence_id, created_at):
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                if version < 7:
                    raise CoreError('Relatório gerenciado exige banco SQLite v7.')
                run = self._load_v4(connection, report['run_id'])
                passed = report['verdict'] == 'PASS'
                if passed and (run.state != 'IN_PROGRESS' or run.revision != expected_revision
                               or run.has_unknown_pendencies):
                    raise ConflictError('Execução mudou antes do registro de PASS.')
                bound = TicketStore.bound_contract(connection, version, run, require_current=passed)
                if (bound is None or report['kind'] != 'AT_RESULT'
                        or report['contract_sha256'] != bound['contract_sha256']
                        or report['at_version'] != bound['contract']['at_version']):
                    raise ConflictError('Relatório não corresponde ao contrato protegido.')
                path, digest = trust_store.save_evidence(run.run_id, report['request_id'], report)
                request = EvidenceRequest(
                    run_id=run.run_id, request_id=report['request_id'], kind='AT_RESULT', verdict=report['verdict'],
                    artifact_digest=report['artifact_digest'], evidence_sha256=digest, collector_route='TRUSTED_EXECUTOR',
                    summary_ref=str(path.relative_to(trust_store.root)), at_version=report['at_version'],
                    commit_sha=report['commit_sha'],
                )
                recorded = self._write_evidence(connection, request, evidence_id, created_at)
                connection.commit()
                return recorded, str(path)
            except BaseException:
                connection.rollback()
                raise

    def complete_v4(self, request, *, created_at: str, authorization_id: str) -> dict:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                if self._validate_schema(connection) not in _LEDGER_VERSIONS:
                    raise CoreError('Operação complete exige banco SQLite v4.')
                existing = connection.execute(
                    'SELECT * FROM "transitions" WHERE operation = ? AND request_id = ?',
                    ('COMPLETE_RUN', request.request_id),
                ).fetchone()
                if existing is not None:
                    request.check_replay(
                        run_id=existing['run_id'], revision=existing['revision'],
                        evidence_id=existing['evidence_id'], actor_route=existing['actor_route'],
                    )
                    self._load_v4(connection, existing['run_id'])
                    stored = self._row_to_transition(existing)
                else:
                    current = self._load_v4(connection, request.run_id)
                    request.check_state(current)
                    next_rev = current.revision + 1
                    connection.execute(
                        'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, authorization_id, actor_route, evidence_id) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (request.run_id, next_rev, 'COMPLETE_RUN', 'RUN_COMPLETED', 'IN_PROGRESS', 'DONE',
                         request.request_id, created_at, authorization_id, request.actor_route, request.evidence_id),
                    )
                    changed = connection.execute(
                        'UPDATE "runs" SET state = ?, revision = ?, updated_at = ? WHERE id = ? AND state = ? AND revision = ?',
                        ('DONE', next_rev, created_at, request.run_id, 'IN_PROGRESS', request.expected_revision),
                    ).rowcount
                    if changed != 1:
                        raise ConflictError('Execução mudou antes da confirmação da conclusão.')
                    run = self._load_v4(connection, request.run_id)
                    stored = run.events[-1]
                connection.commit()
                return {**stored.to_dict(), 'schema_version': 4}
            except BaseException:
                connection.rollback()
                raise

    def record_checkpoint_v4(self, request, *, checkpoint_id: str, created_at: str) -> dict:
        try:
            ev_json = json.dumps(list(request.evidence_hashes), sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=False, allow_nan=False)
            ext_json = json.dumps(list(request.external_idempotency_keys), sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False)
            unk_json = json.dumps(list(request.unknown_pendencies), sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise CoreError('Dados do checkpoint não podem ser serializados em JSON estrito.') from error
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                if self._validate_schema(connection) not in _LEDGER_VERSIONS:
                    raise CoreError('Registro de checkpoint exige banco SQLite v4.')
                existing = connection.execute(
                    'SELECT * FROM "checkpoints" WHERE request_id = ?',
                    (request.request_id,),
                ).fetchone()
                if existing is not None:
                    request.check_replay(existing, evidence_hashes_json=ev_json,
                                         external_keys_json=ext_json, unknown_json=unk_json)
                    self._load_v4(connection, existing['run_id'])
                    stored = self._row_to_checkpoint(existing)
                else:
                    current = self._load_v4(connection, request.run_id)
                    request.check_state(current)
                    connection.execute(
                        'INSERT INTO "checkpoints" (checkpoint_id, run_id, run_revision, request_id, '
                        'last_confirmed_step, base_commit_sha, head_commit_sha, worktree_snapshot_sha256, '
                        'evidence_hashes_json, external_idempotency_keys_json, unknown_pendencies_json, created_at) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (checkpoint_id, request.run_id, request.expected_revision, request.request_id,
                         request.last_confirmed_step, request.base_commit_sha, request.head_commit_sha,
                         request.worktree_snapshot_sha256, ev_json, ext_json, unk_json, created_at),
                    )
                    self._load_v4(connection, request.run_id)
                    row = connection.execute(
                        'SELECT * FROM "checkpoints" WHERE checkpoint_id = ?',
                        (checkpoint_id,),
                    ).fetchone()
                    stored = self._row_to_checkpoint(row)
                connection.commit()
                return {**stored.to_dict(), 'schema_version': 4}
            except BaseException:
                connection.rollback()
                raise
