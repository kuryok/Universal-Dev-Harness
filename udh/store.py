"""Persistência transacional do estado mínimo do Core em SQLite."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .domain import CoreError, Event, Run


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
    def _validate_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
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
                    self._validate_schema(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return 1

    @staticmethod
    def _load(connection: sqlite3.Connection, run_id: str) -> Run:
        row = connection.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
        if row is None:
            raise CoreError('Execução não encontrada.')
        events = connection.execute(
            'SELECT sequence, kind, created_at FROM events WHERE run_id = ? ORDER BY sequence',
            (run_id,),
        ).fetchall()
        if len(events) != 1 or events[0]['kind'] != 'RUN_CREATED':
            raise CoreError('Execução com evento inicial ausente ou inválido.')
        return Run(
            run_id=row['id'], request_id=row['request_id'], spec_id=row['spec_id'],
            spec_version=row['spec_version'], state=row['state'], created_at=row['created_at'],
            events=tuple(Event(sequence=e['sequence'], kind=e['kind'], created_at=e['created_at'])
                         for e in events),
        )

    def create(self, *, run_id: str, request_id: str, spec_id: str,
               spec_version: str, created_at: str) -> Run:
        with self._connection('rw') as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                self._validate_schema(connection)
                existing = connection.execute(
                    'SELECT id, spec_id, spec_version FROM runs WHERE request_id = ?',
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (existing['spec_id'], existing['spec_version']) != (spec_id, spec_version):
                        raise CoreError('Requisição já utilizada com outra referência de especificação.')
                    result = self._load(connection, existing['id'])
                else:
                    connection.execute(
                        'INSERT INTO runs (id, request_id, spec_id, spec_version, state, created_at) '
                        "VALUES (?, ?, ?, ?, 'PENDING', ?)",
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
