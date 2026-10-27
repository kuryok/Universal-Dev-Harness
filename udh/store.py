"""Persistência transacional do estado mínimo do Core em SQLite."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .domain import CoreError, Event, BlockedEvent, UnblockedEvent, Run, require_run_id
from . import migrations
from .transitions import ConflictError


_NONEMPTY = "length(trim({field}, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0"
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

    def initialize(self) -> int:
        with self._connection('rwc') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                objects = connection.execute('SELECT name FROM sqlite_master').fetchall()
                if version == 0 and not objects:
                    connection.execute(_RUNS_SQL)
                    connection.execute(_EVENTS_SQL)
                    connection.execute('PRAGMA user_version = 1')
                else:
                    self._validate_schema(connection, v1_only=True)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return 1

    @staticmethod
    def _load(connection: sqlite3.Connection, run_id: str, *, version=None) -> Run:
        if version is None:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
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
            if len(events) in (2, 3) and len(requests) == 1:
                event, request = events[1], requests[0]
                if (event['kind'] != 'RUN_BLOCKED' or request['event_sequence'] != event['sequence']
                        or request['expected_revision'] != 0 or request['controller_route'] != 'OPERATOR_LOCAL'
                        or request['authorization_rule_version'] != 1):
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
                            or request['expected_revision'] != 1 or request['controller_route'] != 'OPERATOR_LOCAL'
                            or request['authorization_rule_version'] != 1):
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

    def create(self, *, run_id: str, request_id: str, spec_id: str,
               spec_version: str, created_at: str) -> Run:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = self._validate_schema(connection)
                existing = connection.execute(
                    'SELECT id, spec_id, spec_version FROM runs WHERE request_id = ?',
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (existing['spec_id'], existing['spec_version']) != (spec_id, spec_version):
                        raise CoreError('Requisição já utilizada com outra referência de especificação.')
                    result = self._load(connection, existing['id'])
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
                    result = self._load(connection, run_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return result

    def get(self, run_id: str) -> Run:
        with self._connection('ro') as connection:
            connection.execute('BEGIN')
            try:
                self._validate_schema(connection)
                result = self._load(connection, run_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return result

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
