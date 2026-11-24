"""Esquemas v2–v4 e migrações explícitas, sem efeitos fora da transação SQLite."""
import json

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
        raise CoreError(f'Esquema SQLite v{version} incompatível.')


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
            if version in (3, 4):
                raise CoreError(f'Migração v2 não permite downgrade de v{version}.')
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


_UTC_FIELD = "length({field}) >= 20 AND substr({field}, -1) = 'Z' AND julianday({field}) IS NOT NULL"
_SHA256_FIELD = "length({field}) = 64 AND {field} NOT GLOB '*[^0-9a-f]*'"
_SHA1_FIELD = "length({field}) = 40 AND {field} NOT GLOB '*[^0-9a-f]*'"

RUNS_V4_SQL = f'''CREATE TABLE "runs" (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE CHECK ({_NONEMPTY.format(field='request_id')}),
    spec_id TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_id')}),
    spec_version TEXT NOT NULL CHECK ({_NONEMPTY.format(field='spec_version')}),
    ticket_id TEXT CHECK (ticket_id IS NULL OR {_NONEMPTY.format(field='ticket_id')}),
    mode TEXT NOT NULL CHECK (mode IN ('FAST', 'STANDARD', 'DEEP')),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
    created_at TEXT NOT NULL CHECK ({_UTC_FIELD.format(field='created_at')}),
    updated_at TEXT NOT NULL CHECK ({_UTC_FIELD.format(field='updated_at')}),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0)
)'''

EVIDENCE_V4_SQL = f'''CREATE TABLE "evidence_refs" (
    evidence_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    request_id TEXT NOT NULL UNIQUE CHECK ({_NONEMPTY.format(field='request_id')}),
    kind TEXT NOT NULL CHECK (kind IN ('AT_RESULT', 'SMOKE_RESULT', 'POC_RESULT', 'REVIEW_REPORT', 'ARTIFACT_PROMOTION')),
    verdict TEXT NOT NULL CHECK (verdict IN ('PASS', 'FAIL', 'ERROR', 'STALE')),
    artifact_digest TEXT NOT NULL CHECK ({_SHA256_FIELD.format(field='artifact_digest')}),
    evidence_sha256 TEXT NOT NULL CHECK ({_SHA256_FIELD.format(field='evidence_sha256')}),
    at_version TEXT CHECK (at_version IS NULL OR {_NONEMPTY.format(field='at_version')}),
    commit_sha TEXT CHECK (commit_sha IS NULL OR ({_SHA1_FIELD.format(field='commit_sha')})),
    collector_route TEXT NOT NULL CHECK (collector_route IN ('TRUSTED_EXECUTOR', 'OPERATOR_LOCAL')),
    summary_ref TEXT NOT NULL CHECK ({_NONEMPTY.format(field='summary_ref')}),
    created_at TEXT NOT NULL CHECK ({_UTC_FIELD.format(field='created_at')})
)'''

TRANSITIONS_V4_SQL = f'''CREATE TABLE "transitions" (
    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
    operation TEXT NOT NULL CHECK (operation IN ('CREATE_RUN', 'START_RUN', 'BLOCK_RUN', 'UNBLOCK_RUN', 'COMPLETE_RUN')),
    kind TEXT NOT NULL CHECK (kind IN ('RUN_CREATED', 'RUN_STARTED', 'RUN_BLOCKED', 'RUN_UNBLOCKED', 'RUN_COMPLETED')),
    from_state TEXT CHECK (from_state IS NULL OR from_state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED')),
    to_state TEXT NOT NULL CHECK (to_state IN ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'DONE')),
    request_id TEXT NOT NULL CHECK ({_NONEMPTY.format(field='request_id')}),
    created_at TEXT NOT NULL CHECK ({_UTC_FIELD.format(field='created_at')}),
    reason TEXT CHECK (reason IS NULL OR {_NONEMPTY.format(field='reason')}),
    reason_code TEXT CHECK (reason_code IS NULL OR {_NONEMPTY.format(field='reason_code')}),
    block_category TEXT CHECK (block_category IS NULL OR block_category IN ('POLICY_VIOLATION', 'FUNCTIONAL_FAILURE', 'ADMIN_PAUSE')),
    next_action TEXT CHECK (next_action IS NULL OR {_NONEMPTY.format(field='next_action')}),
    authorization_id TEXT UNIQUE,
    actor_route TEXT NOT NULL CHECK (actor_route IN ('CORE_LOCAL', 'OPERATOR_LOCAL', 'TRUSTED_EXECUTOR')),
    evidence_id TEXT REFERENCES "evidence_refs"(evidence_id),
    legacy_meta_json TEXT,
    CHECK ((operation = 'CREATE_RUN' AND kind = 'RUN_CREATED' AND from_state IS NULL AND to_state = 'PENDING' AND revision = 0 AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'START_RUN' AND kind = 'RUN_STARTED' AND from_state = 'PENDING' AND to_state = 'IN_PROGRESS' AND revision > 0 AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'BLOCK_RUN' AND kind = 'RUN_BLOCKED' AND from_state IN ('PENDING', 'IN_PROGRESS') AND to_state = 'BLOCKED' AND revision > 0 AND block_category IS NOT NULL AND reason IS NOT NULL AND reason_code IS NOT NULL AND next_action IS NOT NULL AND evidence_id IS NULL)
        OR (operation = 'UNBLOCK_RUN' AND kind = 'RUN_UNBLOCKED' AND from_state = 'BLOCKED' AND to_state = 'PENDING' AND revision > 0 AND reason IS NOT NULL AND block_category IS NULL AND evidence_id IS NULL)
        OR (operation = 'COMPLETE_RUN' AND kind = 'RUN_COMPLETED' AND from_state = 'IN_PROGRESS' AND to_state = 'DONE' AND revision > 0 AND block_category IS NULL AND evidence_id IS NOT NULL)),
    UNIQUE (run_id, revision),
    UNIQUE (operation, request_id)
)'''

CHECKPOINTS_V4_SQL = f'''CREATE TABLE "checkpoints" (
    checkpoint_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES "runs"(id),
    run_revision INTEGER NOT NULL CHECK (typeof(run_revision) = 'integer' AND run_revision >= 0),
    request_id TEXT NOT NULL UNIQUE CHECK ({_NONEMPTY.format(field='request_id')}),
    last_confirmed_step TEXT NOT NULL CHECK ({_NONEMPTY.format(field='last_confirmed_step')}),
    base_commit_sha TEXT CHECK (base_commit_sha IS NULL OR ({_SHA1_FIELD.format(field='base_commit_sha')})),
    head_commit_sha TEXT CHECK (head_commit_sha IS NULL OR ({_SHA1_FIELD.format(field='head_commit_sha')})),
    worktree_snapshot_sha256 TEXT CHECK (worktree_snapshot_sha256 IS NULL OR ({_SHA256_FIELD.format(field='worktree_snapshot_sha256')})),
    evidence_hashes_json TEXT NOT NULL,
    external_idempotency_keys_json TEXT NOT NULL,
    unknown_pendencies_json TEXT NOT NULL,
    created_at TEXT NOT NULL CHECK ({_UTC_FIELD.format(field='created_at')})
)'''

IDX_EVIDENCE_V4_SQL = 'CREATE INDEX "idx_evidence_refs_run_id" ON "evidence_refs" (run_id, created_at)'
IDX_CHECKPOINTS_V4_SQL = 'CREATE INDEX "idx_checkpoints_run_id" ON "checkpoints" (run_id, run_revision, created_at)'

_EXPECTED_V4 = {
    ('table', 'runs'): RUNS_V4_SQL.strip(),
    ('table', 'evidence_refs'): EVIDENCE_V4_SQL.strip(),
    ('table', 'transitions'): TRANSITIONS_V4_SQL.strip(),
    ('table', 'checkpoints'): CHECKPOINTS_V4_SQL.strip(),
    ('index', 'idx_evidence_refs_run_id'): IDX_EVIDENCE_V4_SQL.strip(),
    ('index', 'idx_checkpoints_run_id'): IDX_CHECKPOINTS_V4_SQL.strip(),
}

_V5_TABLES = {
    'ticket_plan_inputs': '''CREATE TABLE "ticket_plan_inputs" (
 change_id TEXT NOT NULL, spec_version TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
 input_json TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 PRIMARY KEY (change_id, spec_version, spec_sha256)
)''',
    'ticket_plans': '''CREATE TABLE "ticket_plans" (
 change_id TEXT NOT NULL, spec_version TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
 plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
 PRIMARY KEY (change_id, spec_version, spec_sha256),
 UNIQUE (change_id, spec_version, spec_sha256, plan_sha256),
 FOREIGN KEY (change_id, spec_version, spec_sha256)
 REFERENCES ticket_plan_inputs (change_id, spec_version, spec_sha256)
)''',
    'ticket_plan_heads': '''CREATE TABLE "ticket_plan_heads" (
 change_id TEXT PRIMARY KEY NOT NULL, spec_version TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 1),
 active_plan_sha256 TEXT,
 FOREIGN KEY (change_id, spec_version, spec_sha256)
 REFERENCES ticket_plan_inputs (change_id, spec_version, spec_sha256),
 FOREIGN KEY (change_id, spec_version, spec_sha256, active_plan_sha256)
 REFERENCES ticket_plans (change_id, spec_version, spec_sha256, plan_sha256)
)''',
}
_EXPECTED_V5 = {**_EXPECTED_V4, **{('table', name): sql for name, sql in _V5_TABLES.items()}}
_MANAGED_RUNS_SQL = '''CREATE TABLE "managed_ticket_runs" (
 change_id TEXT NOT NULL, spec_version TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
 approval_revision INTEGER NOT NULL CHECK (typeof(approval_revision) = 'integer' AND approval_revision >= 1),
 ticket_id TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
 run_id TEXT NOT NULL UNIQUE REFERENCES runs (id),
 PRIMARY KEY (change_id, spec_version, spec_sha256, approval_revision, ticket_id),
 FOREIGN KEY (change_id, spec_version, spec_sha256, plan_sha256)
 REFERENCES ticket_plans (change_id, spec_version, spec_sha256, plan_sha256)
)'''
_EXPECTED_V6 = {**_EXPECTED_V5, ('table', 'managed_ticket_runs'): _MANAGED_RUNS_SQL}
_MANAGED_CONTRACTS_SQL = '''CREATE TABLE "managed_ticket_contracts" (
 run_id TEXT PRIMARY KEY NOT NULL REFERENCES managed_ticket_runs (run_id),
 contract_json TEXT NOT NULL CHECK (length(CAST(contract_json AS BLOB)) BETWEEN 1 AND 65536),
 contract_sha256 TEXT NOT NULL CHECK (length(contract_sha256) = 64 AND contract_sha256 NOT GLOB '*[^0-9a-f]*')
)'''
_EXPECTED_V7 = {**_EXPECTED_V6, ('table', 'managed_ticket_contracts'): _MANAGED_CONTRACTS_SQL}
_MANAGED_ACCEPTANCES_SQL = '''CREATE TABLE "managed_ticket_acceptances" (
 run_id TEXT PRIMARY KEY NOT NULL REFERENCES managed_ticket_contracts (run_id),
 request_id TEXT NOT NULL UNIQUE,
 run_revision INTEGER NOT NULL CHECK (typeof(run_revision) = 'integer' AND run_revision >= 1),
 at_evidence_id TEXT NOT NULL REFERENCES evidence_refs (evidence_id),
 review_evidence_id TEXT NOT NULL REFERENCES evidence_refs (evidence_id),
 snapshot_json TEXT NOT NULL CHECK (length(CAST(snapshot_json AS BLOB)) BETWEEN 1 AND 65536),
 snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64 AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*'),
 created_at TEXT NOT NULL
)'''
_EXPECTED_V8 = {**_EXPECTED_V7, ('table', 'managed_ticket_acceptances'): _MANAGED_ACCEPTANCES_SQL}
_DISCOVERY_SESSIONS_SQL = '''CREATE TABLE "discovery_sessions" (
 session_id TEXT PRIMARY KEY NOT NULL,
 change_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
 payload_json TEXT NOT NULL CHECK (length(CAST(payload_json AS BLOB)) BETWEEN 1 AND 2097152)
)'''
_EXPECTED_V9 = {**_EXPECTED_V8, ('table', 'discovery_sessions'): _DISCOVERY_SESSIONS_SQL}


def init_v4(connection):
    for sql in (
        RUNS_V4_SQL,
        EVIDENCE_V4_SQL,
        TRANSITIONS_V4_SQL,
        CHECKPOINTS_V4_SQL,
        IDX_EVIDENCE_V4_SQL,
        IDX_CHECKPOINTS_V4_SQL,
    ):
        connection.execute(sql)
    connection.execute('PRAGMA user_version = 4')


def _validate_ledger_schema(connection, expected, version):
    objects = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
    ).fetchall()
    actual = {(row['type'], row['name']): (row['sql'] or '').strip() for row in objects}
    if len(objects) != len(expected) or actual != expected:
        raise CoreError(f'Esquema SQLite v{version} incompatível.')


def validate_schema_v4(connection):
    """Valida o esquema exato sem varrer dados do ledger."""
    _validate_ledger_schema(connection, _EXPECTED_V4, 4)


def validate_schema_v5(connection):
    _validate_ledger_schema(connection, _EXPECTED_V5, 5)


def validate_schema_v6(connection):
    _validate_ledger_schema(connection, _EXPECTED_V6, 6)


def validate_schema_v7(connection):
    _validate_ledger_schema(connection, _EXPECTED_V7, 7)


def validate_schema_v8(connection):
    _validate_ledger_schema(connection, _EXPECTED_V8, 8)


def validate_schema_v9(connection):
    _validate_ledger_schema(connection, _EXPECTED_V9, 9)


def migrate_v9(store):
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (8, 9):
                raise CoreError('Migração para v9 exige banco SQLite v8 válido.')
            (validate_schema_v8 if version == 8 else validate_schema_v9)(connection)
            check_integrity_v4(connection, store)
            store._check_managed_integrity(connection, allow_started=True)
            store._check_contract_integrity(connection)
            store._check_acceptance_integrity(connection)
            if version == 8:
                connection.execute(_DISCOVERY_SESSIONS_SQL)
                connection.execute('PRAGMA user_version = 9')
                validate_schema_v9(connection)
            connection.commit()
            return {'schema_version': 9, 'changed': version == 8}
        except BaseException:
            connection.rollback()
            raise


def migrate_v8(store):
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (7, 8):
                raise CoreError('Migração para v8 exige banco SQLite v7 válido.')
            (validate_schema_v7 if version == 7 else validate_schema_v8)(connection)
            check_integrity_v4(connection, store)
            store._check_managed_integrity(connection, allow_started=version == 8)
            store._check_contract_integrity(connection)
            if version == 7:
                connection.execute(_MANAGED_ACCEPTANCES_SQL)
                connection.execute('PRAGMA user_version = 8')
                validate_schema_v8(connection)
            else:
                store._check_acceptance_integrity(connection)
            connection.commit()
            return {'schema_version': 8, 'changed': version == 7}
        except BaseException:
            connection.rollback()
            raise


def migrate_v7(store):
    """Acrescenta os contratos de execução sem modificar vínculos existentes."""
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (6, 7):
                raise CoreError('Migração para v7 exige banco SQLite v6 válido.')
            (validate_schema_v6 if version == 6 else validate_schema_v7)(connection)
            check_integrity_v4(connection, store)
            store._check_managed_integrity(connection)
            if version == 6:
                connection.execute(_MANAGED_CONTRACTS_SQL)
                connection.execute('PRAGMA user_version = 7')
                validate_schema_v7(connection)
            else:
                store._check_contract_integrity(connection)
            connection.commit()
            return {'schema_version': 7, 'changed': version == 6}
        except BaseException:
            connection.rollback()
            raise


def migrate_v6(store):
    """Acrescenta vínculos de tickets, sem reescrever o ledger nem os planos."""
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 5:
                validate_schema_v5(connection)
                check_integrity_v4(connection, store)
                connection.execute(_MANAGED_RUNS_SQL)
                connection.execute('PRAGMA user_version = 6')
                validate_schema_v6(connection)
            elif version == 6:
                validate_schema_v6(connection)
                check_integrity_v4(connection, store)
                store._check_managed_integrity(connection)
            else:
                raise CoreError('Migração para v6 exige banco SQLite v5 válido.')
            connection.commit()
            return {'schema_version': 6, 'changed': version == 5}
        except BaseException:
            connection.rollback()
            raise


def migrate_v5(store):
    """Acrescenta a persistência do PLAN, sem reescrever o ledger v4."""
    with store._connection('rw') as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 4:
                validate_schema_v4(connection)
                check_integrity_v4(connection, store)
                for sql in _V5_TABLES.values():
                    connection.execute(sql)
                connection.execute('PRAGMA user_version = 5')
                validate_schema_v5(connection)
            elif version == 5:
                validate_schema_v5(connection)
                check_integrity_v4(connection, store)
            else:
                raise CoreError('Migração para v5 exige banco SQLite v4 válido.')
            connection.commit()
            return {'schema_version': 5, 'changed': version == 4}
        except BaseException:
            connection.rollback()
            raise


def check_integrity_v4(connection, store) -> int:
    """Verificação profunda v4 (usada por doctor e migrate_v4, nunca em show/policy)."""
    if connection.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok':
        raise CoreError('Banco v4 com dados ou restrições inválidos.')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise CoreError('Banco v4 com referências órfãs.')
    run_rows = connection.execute('SELECT id FROM "runs" ORDER BY id').fetchall()
    total_transitions = 0
    total_evidence = 0
    total_checkpoints = 0
    for row in run_rows:
        loaded = store._load_v4(connection, row['id'])
        total_transitions += len(loaded.events)
        total_evidence += len(loaded.evidence_refs)
        total_checkpoints += len(loaded.checkpoints)
    if (total_transitions != connection.execute('SELECT count(*) FROM "transitions"').fetchone()[0]
            or total_evidence != connection.execute('SELECT count(*) FROM "evidence_refs"').fetchone()[0]
            or total_checkpoints != connection.execute('SELECT count(*) FROM "checkpoints"').fetchone()[0]):
        raise CoreError('Banco v4 contém registros órfãos fora do conjunto de execuções.')
    return len(run_rows)


def migrate_v4(store):
    """Migra banco v3 íntegro para ledger unificado v4 em uma única transação."""
    with store._connection('rw') as connection:
        connection.execute('PRAGMA legacy_alter_table = OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 4:
                validate_schema_v4(connection)
                check_integrity_v4(connection, store)
                connection.commit()
                return {'schema_version': 4, 'migrated': False}
            if version != 3:
                raise CoreError('Migração v4 exige banco v3 íntegro.')
            validate_schema(connection, 3)
            check_integrity(connection, store, 3)

            runs = connection.execute('SELECT * FROM "runs" ORDER BY id').fetchall()
            events = connection.execute('SELECT * FROM "events" ORDER BY sequence').fetchall()
            blocks = {row['run_id']: row for row in connection.execute('SELECT * FROM "block_requests"').fetchall()}
            unblocks = {row['run_id']: row for row in connection.execute('SELECT * FROM "unblock_requests"').fetchall()}

            connection.execute(RUNS_V4_SQL.replace('"runs"', '"runs_v4"', 1))
            runs_by_id = {row['id']: row for row in runs}
            events_by_run: dict[str, list] = {row['id']: [] for row in runs}
            for event in events:
                events_by_run[event['run_id']].append(event)

            for run in runs:
                updated_at = events_by_run[run['id']][-1]['created_at']
                connection.execute(
                    'INSERT INTO "runs_v4" (id, request_id, spec_id, spec_version, ticket_id, mode, state, created_at, updated_at, revision) '
                    'VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)',
                    (run['id'], run['request_id'], run['spec_id'], run['spec_version'],
                     'STANDARD', run['state'], run['created_at'], updated_at, run['revision']),
                )

            connection.execute(EVIDENCE_V4_SQL.replace('"runs"', '"runs_v4"'))
            connection.execute(TRANSITIONS_V4_SQL.replace('"runs"', '"runs_v4"'))
            connection.execute(CHECKPOINTS_V4_SQL.replace('"runs"', '"runs_v4"'))

            for event in events:
                rid = event['run_id']
                if event['kind'] == 'RUN_CREATED':
                    connection.execute(
                        'INSERT INTO "transitions" (sequence, run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, actor_route) VALUES (?, ?, 0, ?, ?, NULL, ?, ?, ?, ?)',
                        (event['sequence'], rid, 'CREATE_RUN', 'RUN_CREATED', 'PENDING',
                         runs_by_id[rid]['request_id'], event['created_at'], 'CORE_LOCAL'),
                    )
                elif event['kind'] == 'RUN_BLOCKED':
                    block = blocks[rid]
                    meta = json.dumps({
                        'core_manifest_sha256': block['core_manifest_sha256'],
                        'control_sha256': block['control_sha256'],
                        'authorization_rule_version': block['authorization_rule_version'],
                        'result_schema_version': block['result_schema_version'],
                    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    connection.execute(
                        'INSERT INTO "transitions" (sequence, run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, reason, reason_code, block_category, next_action, authorization_id, '
                        'actor_route, legacy_meta_json) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (event['sequence'], rid, 'BLOCK_RUN', 'RUN_BLOCKED', 'PENDING', 'BLOCKED',
                         block['request_id'], event['created_at'], block['reason'], 'ADMIN_PAUSE', 'ADMIN_PAUSE',
                         'Retire a pausa administrativa quando for seguro continuar.',
                         block['authorization_id'], block['controller_route'], meta),
                    )
                elif event['kind'] == 'RUN_UNBLOCKED':
                    unblock = unblocks[rid]
                    meta = json.dumps({
                        'core_manifest_sha256': unblock['core_manifest_sha256'],
                        'control_sha256': unblock['control_sha256'],
                        'authorization_rule_version': unblock['authorization_rule_version'],
                        'blocked_event_sequence': unblock['blocked_event_sequence'],
                    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                    connection.execute(
                        'INSERT INTO "transitions" (sequence, run_id, revision, operation, kind, from_state, to_state, '
                        'request_id, created_at, reason, authorization_id, actor_route, legacy_meta_json) '
                        'VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (event['sequence'], rid, 'UNBLOCK_RUN', 'RUN_UNBLOCKED', 'BLOCKED', 'PENDING',
                         unblock['request_id'], event['created_at'], unblock['reason'],
                         unblock['authorization_id'], unblock['controller_route'], meta),
                    )
                else:
                    raise CoreError('Tipo de evento v3 desconhecido durante migração v4.')

            for old in ('unblock_requests', 'block_requests', 'events', 'runs'):
                connection.execute(f'DROP TABLE "{old}"')
            connection.execute('ALTER TABLE "runs_v4" RENAME TO "runs"')
            connection.execute(IDX_EVIDENCE_V4_SQL)
            connection.execute(IDX_CHECKPOINTS_V4_SQL)

            validate_schema_v4(connection)
            checked_runs = check_integrity_v4(connection, store)
            if (checked_runs != len(runs)
                    or connection.execute('SELECT count(*) FROM "transitions"').fetchone()[0] != len(events)):
                raise CoreError('Migração v4 não preservou a contagem original de dados.')
            connection.execute('PRAGMA user_version = 4')
            connection.commit()
            return {'schema_version': 4, 'migrated': True, 'migrated_runs': checked_runs, 'migrated_transitions': len(events)}
        except BaseException:
            connection.rollback()
            raise
