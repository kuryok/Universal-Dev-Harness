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


RUNS_V3_SQL = RUNS_SQL.replace("state = 'PENDING' AND revision = 0", "state = 'PENDING' AND revision IN (0, 2)")
EVENTS_V3_SQL = EVENTS_SQL.replace("'RUN_CREATED', 'RUN_BLOCKED'", "'RUN_CREATED', 'RUN_BLOCKED', 'RUN_UNBLOCKED'")
BLOCK_V3_SQL = (BLOCK_REQUESTS_SQL.replace('CREATE TABLE block_requests', 'CREATE TABLE "block_requests"')
    .replace('REFERENCES runs(', 'REFERENCES "runs"(').replace('REFERENCES events(', 'REFERENCES "events"(')
    .replace('authorization_rule_version = 1)\n)', "authorization_rule_version = 1),\n    result_schema_version INTEGER NOT NULL CHECK (typeof(result_schema_version) = 'integer' AND result_schema_version IN (2, 3))\n)"))
UNBLOCK_V3_SQL = f'''CREATE TABLE "unblock_requests" (
    request_id TEXT PRIMARY KEY NOT NULL CHECK ({_NONEMPTY.format(field='request_id')}),
    run_id TEXT NOT NULL UNIQUE REFERENCES "runs"(id),
    expected_revision INTEGER NOT NULL CHECK (typeof(expected_revision) = 'integer' AND expected_revision = 1),
    reason TEXT NOT NULL CHECK ({_NONEMPTY.format(field='reason')}),
    event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    blocked_event_sequence INTEGER NOT NULL UNIQUE REFERENCES "events"(sequence),
    authorization_id TEXT NOT NULL UNIQUE,
    controller_route TEXT NOT NULL CHECK (controller_route = 'OPERATOR_LOCAL'),
    core_manifest_sha256 TEXT NOT NULL CHECK (length(core_manifest_sha256) = 64 AND core_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    control_sha256 TEXT NOT NULL CHECK (length(control_sha256) = 64 AND control_sha256 NOT GLOB '*[^0-9a-f]*'),
    authorization_rule_version INTEGER NOT NULL CHECK (typeof(authorization_rule_version) = 'integer' AND authorization_rule_version = 1)
)'''


def validate_schema(connection, version=2):
    """Compara SQL final exato; não normaliza conteúdo de literais."""
    objects = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
    expected = {'runs': RUNS_SQL, 'events': EVENTS_SQL, 'block_requests': BLOCK_REQUESTS_SQL}
    if version == 3:
        expected = {'runs': RUNS_V3_SQL, 'events': EVENTS_V3_SQL, 'block_requests': BLOCK_V3_SQL, 'unblock_requests': UNBLOCK_V3_SQL}
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
            if version == 3:
                raise CoreError('Migração v2 não permite downgrade de v3.')
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


def migrate_v3(store):
    """Reconstrói v2 integralmente sob uma única transação."""
    with store._connection('rw') as connection:
        connection.execute('PRAGMA legacy_alter_table = OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = store._validate_schema(connection)
            if version == 3:
                connection.commit()
                return {'schema_version': 3, 'migrated': False}
            if version != 2:
                raise CoreError('Migração v3 exige banco v2 íntegro.')
            snapshots = {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY {order}')]
                         for name, order in (('runs', 'id'), ('events', 'sequence'), ('block_requests', 'request_id'))}
            connection.execute(RUNS_V3_SQL.replace('"runs"', '"runs_v3"'))
            connection.execute('INSERT INTO "runs_v3" SELECT * FROM "runs"')
            connection.execute(EVENTS_V3_SQL.replace('"events"', '"events_v3"').replace('"runs"', '"runs_v3"'))
            connection.execute('INSERT INTO "events_v3" SELECT * FROM "events"')
            connection.execute(BLOCK_V3_SQL.replace('"block_requests"', '"block_requests_v3"').replace('"runs"', '"runs_v3"').replace('"events"', '"events_v3"'))
            connection.execute('INSERT INTO "block_requests_v3" SELECT *, 2 FROM "block_requests"')
            for name in ('block_requests', 'events', 'runs'):
                connection.execute(f'DROP TABLE "{name}"')
            for name in ('runs', 'events', 'block_requests'):
                connection.execute(f'ALTER TABLE "{name}_v3" RENAME TO "{name}"')
            connection.execute(UNBLOCK_V3_SQL)
            validate_schema(connection, 3)
            check_integrity(connection, store, 3)
            for name, order in (('runs', 'id'), ('events', 'sequence'), ('block_requests', 'request_id')):
                rows = [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY {order}')]
                if name == 'block_requests':
                    if any(row[-1] != 2 for row in rows):
                        raise CoreError('Versão histórica de bloqueio inválida.')
                    rows = [row[:-1] for row in rows]
                if rows != snapshots[name]:
                    raise CoreError('Migração não preservou os dados originais.')
            connection.execute('PRAGMA user_version = 3')
            connection.commit()
            return {'schema_version': 3, 'migrated': True}
        except BaseException:
            connection.rollback()
            raise
