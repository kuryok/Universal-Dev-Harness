"""Esquema v2 e migração explícita, sem efeitos fora da transação SQLite."""
from .domain import CoreError

_NONEMPTY = "length(trim({field}, char(9)||char(10)||char(11)||char(12)||char(13)||' ')) > 0"
_UTC = "length(created_at) >= 20 AND substr(created_at, -1) = 'Z' AND julianday(created_at) IS NOT NULL"
RUNS_SQL = f'''CREATE TABLE "runs" (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK ({_NONEMPTY.format(field='request_id')}),
    spec_id TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_id')}),
    spec_version TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_version')}),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'BLOCKED')),
    created_at TEXT NOT NULL CHECK ({_UTC}),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND
        ((state = 'PENDING' AND revision = 0) OR (state = 'BLOCKED' AND revision = 1)))
)'''
EVENTS_SQL = f'''CREATE TABLE "events" (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    kind TEXT NOT NULL CHECK (kind IN ('RUN_CREATED', 'RUN_BLOCKED')),
    created_at TEXT NOT NULL CHECK ({_UTC}),
    UNIQUE (run_id, kind)
)'''
BLOCK_REQUESTS_SQL = f'''CREATE TABLE block_requests (
    request_id TEXT PRIMARY KEY NOT NULL CHECK ({_NONEMPTY.format(field='request_id')}),
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 0),
    reason TEXT NOT NULL CHECK ({_NONEMPTY.format(field='reason')}),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES events(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1)
)'''


def validate_schema(connection):
    """Compara SQL final exato; não normaliza conteúdo de literais."""
    objects = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
    expected = {'runs': RUNS_SQL, 'events': EVENTS_SQL, 'block_requests': BLOCK_REQUESTS_SQL}
    if (len(objects) != len(expected) or any(row['type'] != 'table' for row in objects)
            or {row['name']: (row['sql'] or '').strip() for row in objects} != expected):
        raise CoreError('Esquema SQLite v2 incompatível.')


def check_integrity(connection, store, version):
    if connection.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok':
        raise CoreError('Banco com dados ou restrições inválidos.')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise CoreError('Banco com referências órfãs.')
    for row in connection.execute('SELECT id FROM runs').fetchall():
        store._load(connection, row['id'], version=version)


def migrate(store):
    """Reconstrói v1 em v2, preservando valores e sem desligar FKs."""
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = store._validate_schema(connection)
            if version == 2:
                connection.commit()
                return {'schema_version': 2, 'migrated': False}
            extras = connection.execute("SELECT name FROM sqlite_master WHERE type != 'table' AND name NOT GLOB 'sqlite_*'").fetchall()
            if extras or connection.execute('PRAGMA legacy_alter_table').fetchone()[0] != 0:
                raise CoreError('Migração exige esquema v1 sem objetos adicionais e renomeação segura.')
            check_integrity(connection, store, 1)
            runs = [tuple(row) for row in connection.execute('SELECT * FROM runs ORDER BY id')]
            events = [tuple(row) for row in connection.execute('SELECT * FROM events ORDER BY sequence')]
            connection.execute(RUNS_SQL.replace('CREATE TABLE "runs"', 'CREATE TABLE runs_v2', 1))
            connection.execute('INSERT INTO runs_v2 SELECT id, request_id, spec_id, spec_version, state, created_at, 0 FROM runs')
            connection.execute(EVENTS_SQL.replace('CREATE TABLE "events"', 'CREATE TABLE events_v2', 1).replace('REFERENCES "runs"', 'REFERENCES runs_v2'))
            connection.execute('INSERT INTO events_v2 SELECT sequence, run_id, kind, created_at FROM events')
            connection.execute('DROP TABLE events')
            connection.execute('DROP TABLE runs')
            connection.execute('ALTER TABLE runs_v2 RENAME TO runs')
            connection.execute('ALTER TABLE events_v2 RENAME TO events')
            connection.execute(BLOCK_REQUESTS_SQL)
            validate_schema(connection)
            check_integrity(connection, store, 2)
            if (runs != [tuple(row)[:-1] for row in connection.execute('SELECT * FROM runs ORDER BY id')]
                    or events != [tuple(row) for row in connection.execute('SELECT * FROM events ORDER BY sequence')]):
                raise CoreError('Migração não preservou os dados originais.')
            connection.execute('PRAGMA user_version = 2')
            connection.commit()
            return {'schema_version': 2, 'migrated': True}
        except BaseException:
            connection.rollback()
            raise
