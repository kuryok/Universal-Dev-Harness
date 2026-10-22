"""Aceite independente da migração; somente transporte e SQLite confiáveis no host."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import unittest


def prefix(name):
    value = json.loads(os.environ[name])
    if not isinstance(value, list) or not value or any(not isinstance(x, str) for x in value):
        raise RuntimeError('INFRA_ERROR: prefixo de transporte inválido')
    return value


class OperatorFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['UDH_OPERATOR_STATE_ROOT'])
        self.db = self.root / 'udh.sqlite'
        self.safe_tree()
        for path in self.root.iterdir():
            if path.name not in ('udh.sqlite', 'udh.sqlite-journal', 'udh.sqlite-wal', 'udh.sqlite-shm'):
                raise RuntimeError('Arquivo inesperado no estado privado')
            path.unlink()

    def safe_tree(self):
        for path in (self.root, *self.root.parents):
            if stat.S_ISLNK(path.lstat().st_mode):
                raise RuntimeError('Ancestral redirecionável')
        if not self.root.is_dir():
            raise RuntimeError('Estado privado não é diretório')
        for path in self.root.iterdir():
            if not stat.S_ISREG(path.lstat().st_mode):
                raise RuntimeError('Estado contém arquivo especial ou symlink')

    def invoke(self, name, *args):
        try:
            result = subprocess.run(prefix(name) + list(args), capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('INFRA_ERROR: transporte não terminou corretamente') from error
        if result.returncode in (124, 125):
            raise RuntimeError('INFRA_ERROR: ' + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def operator(self, *args):
        return self.invoke('UDH_OPERATOR_PREFIX_JSON', *args)

    def legacy(self, *args):
        return self.invoke('UDH_LEGACY_OPERATOR_PREFIX_JSON', *args)

    def core(self, command, *args):
        return self.invoke('UDH_CORE_PREFIX_JSON', command, '--db', str(self.db), *args)

    def seed(self, count=1):
        self.payload(self.core('init'))
        return [self.payload(self.core('create', '--spec-id', ' spec ',
                    '--spec-version', ' v1 ', '--request-id', f'create-{index}')) for index in range(count)]

    def payload(self, result):
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def snapshot(self):
        self.safe_tree()
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)) as connection:
            return (connection.execute('PRAGMA user_version').fetchone()[0],
                    tuple(connection.iterdump()))

    def rows(self, table):
        self.assertIn(table, ('runs', 'events', 'block_requests', 'unblock_requests'))
        self.safe_tree()
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            return [dict(row) for row in connection.execute('SELECT * FROM ' + table)]

    def observe(self, checkpoint, *args):
        observation = self.payload(self.operator('--observe', checkpoint, *args))
        instrumentation = observation['instrumentation']
        self.assertIs(instrumentation['core_bytes_modified'], False)
        for key in ('launcher_sha256', 'core_manifest_sha256'):
            self.assertRegex(instrumentation[key], r'^[0-9a-f]{64}$')
        return observation

    def discover(self, *args):
        observation = self.observe('list', *args)
        result = observation['result']
        self.assertEqual(result['returncode'], 0, result['stderr'])
        self.assertEqual(result['stderr'], '')
        self.assertEqual(observation['outcome'], 'OBSERVED_COMPLETE')
        checkpoints = observation['checkpoints']
        if (not isinstance(checkpoints, list) or len(checkpoints) != len(set(checkpoints))
                or not any(point.startswith('BEFORE_WRITE:') for point in checkpoints)
                or not any(point.startswith('AFTER_COMMIT:') for point in checkpoints)
                or any(not __import__('re').fullmatch(r'(BEFORE_WRITE|AFTER_COMMIT):[0-9]+', point)
                       for point in checkpoints)):
            raise RuntimeError('INFRA_ERROR: markers ausentes ou inválidos após sucesso funcional')
        return checkpoints

    def interrupt(self, checkpoint, *args):
        observation = self.observe(checkpoint, *args)
        self.assertEqual(observation['outcome'], 'OBSERVED_INTERRUPT')
        self.assertEqual(observation['checkpoint'], checkpoint)
        for key in ('marker_received', 'process_killed', 'runtime_removed'):
            if observation.get(key) is not True:
                raise RuntimeError('INFRA_ERROR: interrupção não comprovada: ' + key)
        self.safe_tree()
        # O SQLite confiável recupera eventual hot journal antes da leitura RO.
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=rw', uri=True)) as connection:
            connection.execute('SELECT count(*) FROM sqlite_master').fetchone()


from concurrent.futures import ThreadPoolExecutor
from uuid import UUID


class UnblockAcceptance(OperatorFixture):
    """Contrato novo; nenhum import ou chamada direta ao Core candidato."""

    def arguments(self, run_id, request='unblock-1', reason=' Retirada explícita ', revision='1'):
        return ('unblock', '--run-id', run_id, '--expected-revision', revision,
                '--request-id', request, '--reason', reason)

    def block(self, run_id, request='block-1', legacy=False, revision='0'):
        operation = self.legacy if legacy else self.operator
        return operation('block', '--run-id', run_id, '--expected-revision', revision,
                         '--request-id', request, '--reason', ' Pausa explícita ')

    def v2(self, count=1, blocked=True):
        runs = self.seed(count)
        self.payload(self.legacy('migrate', '--to', '2'))
        historical = self.payload(self.block(runs[0]['run_id'], legacy=True)) if blocked else None
        return runs, historical

    def ready(self):
        runs, historical = self.v2()
        self.assertEqual(self.payload(self.operator('migrate', '--to', '3')),
                         {'schema_version': 3, 'migrated': True})
        return runs[0]['run_id'], historical

    def refused(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr)

    def assert_pending(self, run_id, result):
        self.assertEqual(set(result), {'schema_version', 'request_id', 'run_id', 'from_state',
                                     'to_state', 'revision', 'event_sequence', 'created_at',
                                     'authorization_id'})
        self.assertEqual((result['schema_version'], result['run_id'], result['from_state'],
                          result['to_state'], result['revision']),
                         (3, run_id, 'BLOCKED', 'PENDING', 2))
        self.assertEqual(str(UUID(result['authorization_id'])), result['authorization_id'])
        self.assertRegex(result['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$')
        timestamp = datetime.fromisoformat(result['created_at'][:-1] + '+00:00')
        self.assertEqual(timestamp.utcoffset(), timezone.utc.utcoffset(timestamp))
        shown = self.payload(self.core('show', '--run-id', run_id))
        self.assertEqual((shown['schema_version'], shown['state'], shown['revision']), (3, 'PENDING', 2))
        self.assertEqual([event['kind'] for event in shown['events']],
                         ['RUN_CREATED', 'RUN_BLOCKED', 'RUN_UNBLOCKED'])
        blocked, event = shown['events'][1:]
        for key in ('request_id', 'authorization_id', 'created_at', 'from_state', 'to_state', 'revision'):
            self.assertEqual(event[key], result[key])
        self.assertEqual(event['sequence'], result['event_sequence'])
        self.assertEqual(event['blocked_event_sequence'], blocked['sequence'])
        self.assertGreater(event['sequence'], blocked['sequence'])
        self.assertEqual(blocked['reason'], ' Pausa explícita ')
        requests = self.rows('unblock_requests')
        self.assertEqual(len(requests), 1)
        request = requests[0]
        for key in ('run_id', 'request_id', 'authorization_id'):
            self.assertEqual(request[key], result[key])
        self.assertEqual(request['expected_revision'], 1)
        self.assertEqual(request['event_sequence'], event['sequence'])
        self.assertEqual(request['blocked_event_sequence'], blocked['sequence'])
        self.assertEqual(request['reason'], event['reason'])
        self.assertEqual(request['controller_route'], 'OPERATOR_LOCAL')
        self.assertEqual(request['authorization_rule_version'], 1)
        for key, variable in (('core_manifest_sha256', 'UDH_OPERATOR_CORE_MANIFEST_SHA256'),
                              ('control_sha256', 'UDH_OPERATOR_CONTROL_SHA256')):
            expected = os.environ.get(variable)
            if not expected or not __import__('re').fullmatch(r'[0-9a-f]{64}', expected):
                raise RuntimeError('INFRA_ERROR: medição protegida ausente ou inválida: ' + variable)
            self.assertEqual(request[key], expected)

    def test_u1_persisted_unblock(self):
        run_id, _ = self.ready()
        result = self.payload(self.operator(*self.arguments(run_id)))
        self.assert_pending(run_id, result)
        self.assertEqual(self.rows('unblock_requests')[0]['reason'], ' Retirada explícita ')

    def test_u2_legacy_denial_without_database_access(self):
        self.seed()
        before = self.snapshot()
        for args in (('migrate', '--to', '3'), self.arguments('00000000-0000-0000-0000-000000000001')):
            response = self.legacy(*args)
            self.assertEqual(response.returncode, 3, response.stderr)
            payload = json.loads(response.stdout)
            self.assertEqual((payload['decision'], payload['reason_code']), ('DENY', 'AUTHORIZATION_REQUIRED'))
            self.assertEqual(response.stderr, '')
            self.assertEqual(self.snapshot(), before)
        # Uma sentinela inválida discrimina a ordem de autorização/validação.
        # Não é prova de ausência de syscalls de leitura; a fronteira é verificada externamente.
        self.setUp()
        sentinel = b'UDH: sentinela deliberadamente incompativel com SQLite\x00'
        self.safe_tree()
        self.db.write_bytes(sentinel)
        for args in (('migrate', '--to', '3'), self.arguments('00000000-0000-0000-0000-000000000001')):
            response = self.legacy(*args)
            self.assertEqual(response.returncode, 3, response.stderr)
            payload = json.loads(response.stdout)
            self.assertEqual((payload['decision'], payload['reason_code']), ('DENY', 'AUTHORIZATION_REQUIRED'))
            self.assertEqual(response.stderr, '')
            self.safe_tree()
            self.assertEqual(self.db.read_bytes(), sentinel)

    def test_u2_adversary_claims_and_substitution(self):
        self.seed()
        before = self.snapshot()
        result = self.payload(self.operator('--probe-adversary'))
        self.assertIs(result['runtime_removed'], True)
        for key in ('state_mount_absent', 'control_mount_absent', 'runtime_socket_absent',
                    'inherited_admin_fds_absent', 'authority_claims_rejected',
                    'snapshot_substitution_rejected', 'symlink_rejected', 'db_override_rejected',
                    'configuration_substitution_rejected', 'unblock_context_denied_without_sqlite'):
            self.assertIs(result['checks'][key], True, key)
        self.assertEqual(self.snapshot(), before)

    def test_u3_replay_conflicts_and_namespaces(self):
        run_id, _ = self.ready()
        other = self.payload(self.core('create', '--spec-id', 's', '--spec-version', '1',
                                       '--request-id', 'other'))['run_id']
        result = self.payload(self.operator(*self.arguments(run_id, request='block-1')))
        before = self.snapshot()
        self.assertEqual(self.payload(self.operator(*self.arguments(run_id, request='block-1'))), result)
        for identifier, reason, revision in ((run_id, 'diferente', '1'), (other, ' Retirada explícita ', '1'),
                                             (run_id, ' Retirada explícita ', '2')):
            self.refused(self.operator(*self.arguments(identifier, 'block-1', reason, revision)), 4)
            self.assertEqual(self.snapshot(), before)
        self.assert_pending(run_id, result)

    def test_u3_concurrent_equal_and_distinct(self):
        for requests in (('same', 'same'), ('first', 'second')):
            with self.subTest(requests=requests):
                self.setUp()
                run_id, _ = self.ready()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda request: self.operator(*self.arguments(run_id, request)), requests))
                self.assertEqual(sorted(response.returncode for response in responses),
                                 [0, 0] if requests[0] == requests[1] else [0, 4])
                winners = [self.payload(response) for response in responses if response.returncode == 0]
                if len(winners) == 2:
                    self.assertEqual(winners[0], winners[1])
                self.assert_pending(run_id, winners[0])

    def test_u4_historical_replay_and_terminal_cycle(self):
        for migrated in (True, False):
            with self.subTest(migrated=migrated):
                self.setUp()
                if migrated:
                    run_id, historical = self.ready()
                else:
                    runs, _ = self.v2(blocked=False)
                    run_id = runs[0]['run_id']
                    self.payload(self.operator('migrate', '--to', '3'))
                    historical = self.payload(self.block(run_id))
                self.assertEqual(historical['schema_version'], 2 if migrated else 3)
                result = self.payload(self.operator(*self.arguments(run_id)))
                before = self.snapshot()
                self.assertEqual(self.payload(self.block(run_id)), historical)
                self.assertEqual(self.snapshot(), before)
                for response in (self.block(run_id, 'new'), self.block(run_id, 'new', revision='2'),
                                 self.operator(*self.arguments(run_id, 'new'))):
                    self.refused(response, 4)
                    self.assertEqual(self.snapshot(), before)
                self.assert_pending(run_id, result)

    def test_u5_migration_preserves_all_legacy_values(self):
        for count, blocked in ((0, False), (2, False), (2, True)):
            with self.subTest(count=count, blocked=blocked):
                self.setUp()
                self.v2(count=count, blocked=blocked)
                original = {table: self.rows(table) for table in ('runs', 'events', 'block_requests')}
                self.assertEqual(self.payload(self.operator('migrate', '--to', '3')),
                                 {'schema_version': 3, 'migrated': True})
                for table in ('runs', 'events'):
                    self.assertEqual(self.rows(table), original[table])
                self.assertEqual(self.rows('block_requests'),
                                 [dict(row, result_schema_version=2) for row in original['block_requests']])
                self.assertEqual(self.rows('unblock_requests'), [])
                before = self.snapshot()
                self.assertEqual(self.payload(self.operator('migrate', '--to', '3')),
                                 {'schema_version': 3, 'migrated': False})
                self.refused(self.operator('migrate', '--to', '2'), 1)
                self.refused(self.core('init'), 1)
                self.assertEqual(self.snapshot(), before)

    def test_u5_invalid_sources_unchanged(self):
        self.refused(self.operator('migrate', '--to', '3'), 1)
        self.assertFalse(self.db.exists())
        for sql in (None, 'PRAGMA user_version=0', 'PRAGMA user_version=99', 'CREATE TABLE unexpected(x)',
                    'CREATE TRIGGER unexpected AFTER INSERT ON runs BEGIN SELECT 1; END',
                    "UPDATE runs SET id='invalid-uuid'"):
            with self.subTest(sql=sql):
                self.setUp()
                if sql is None:
                    self.seed()
                else:
                    self.v2(blocked=False)
                    self.mutate(sql)
                before = self.snapshot()
                self.refused(self.operator('migrate', '--to', '3'), 1)
                self.assertEqual(self.snapshot(), before)

    def mutate(self, sql):
        self.safe_tree()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute(sql)
            connection.commit()

    def test_u6_migration_concurrent(self):
        self.v2(count=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.operator('migrate', '--to', '3'), range(2)))
        payloads = [self.payload(response) for response in responses]
        self.assertEqual(sorted(payload['migrated'] for payload in payloads), [False, True])
        self.assertTrue(all(payload['schema_version'] == 3 for payload in payloads))
        self.assertEqual(self.rows('unblock_requests'), [])

    def test_u6_migration_each_write_and_commit(self):
        self.v2(count=2)
        checkpoints = self.discover('migrate', '--to', '3')
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                self.setUp()
                self.v2(count=2)
                legacy_rows = {table: self.rows(table) for table in ('runs', 'events', 'block_requests')}
                before = self.snapshot()
                self.interrupt(checkpoint, 'migrate', '--to', '3')
                if checkpoint.startswith('BEFORE_WRITE:'):
                    self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.payload(self.operator('migrate', '--to', '3')),
                                 {'schema_version': 3, 'migrated': checkpoint.startswith('BEFORE_WRITE:')})
                for table in ('runs', 'events'):
                    self.assertEqual(self.rows(table), legacy_rows[table])
                self.assertEqual(self.rows('block_requests'),
                                 [dict(row, result_schema_version=2) for row in legacy_rows['block_requests']])
                self.assertEqual(self.rows('unblock_requests'), [])
                persisted = self.snapshot()
                self.assertEqual(self.payload(self.operator('migrate', '--to', '3')),
                                 {'schema_version': 3, 'migrated': False})
                self.assertEqual(self.snapshot(), persisted)

    def test_u6_unblock_each_write_and_commit(self):
        run_id, _ = self.ready()
        checkpoints = self.discover(*self.arguments(run_id))
        if (sum(point.startswith('BEFORE_WRITE:') for point in checkpoints) != 3
                or sum(point.startswith('AFTER_COMMIT:') for point in checkpoints) != 1):
            raise RuntimeError('INFRA_ERROR: conjunto de markers de unblock incompleto após sucesso')
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                self.setUp()
                run_id, _ = self.ready()
                before = self.snapshot()
                self.interrupt(checkpoint, *self.arguments(run_id))
                if checkpoint.startswith('BEFORE_WRITE:'):
                    self.assertEqual(self.snapshot(), before)
                else:
                    self.assertEqual(len(self.rows('unblock_requests')), 1)
                result = self.payload(self.operator(*self.arguments(run_id)))
                self.assert_pending(run_id, result)
                persisted = self.snapshot()
                self.assertEqual(self.payload(self.operator(*self.arguments(run_id))), result)
                self.assertEqual(self.snapshot(), persisted)

    def test_u7_semantically_invalid_journals_refused(self):
        mutations = (
            "UPDATE unblock_requests SET blocked_event_sequence=(SELECT sequence FROM events WHERE kind='RUN_CREATED' LIMIT 1)",
            "UPDATE unblock_requests SET event_sequence=blocked_event_sequence",
            "UPDATE unblock_requests SET reason='   '",
            "UPDATE unblock_requests SET authorization_id='invalid-uuid'",
            "UPDATE unblock_requests SET request_id='   '",
            "UPDATE unblock_requests SET expected_revision=2",
            "UPDATE unblock_requests SET core_manifest_sha256='INVALID'",
            "DELETE FROM unblock_requests",
            "UPDATE runs SET state='BLOCKED', revision=1",
            "UPDATE unblock_requests SET run_id=(SELECT id FROM runs WHERE revision=0 LIMIT 1)",
            "UPDATE events SET sequence=sequence+100 WHERE kind='RUN_CREATED'",
            ("UPDATE unblock_requests SET blocked_event_sequence=(SELECT sequence FROM events WHERE kind='RUN_CREATED' AND run_id=unblock_requests.run_id)",
             "DELETE FROM block_requests", "DELETE FROM events WHERE kind='RUN_BLOCKED'"),
        )
        for sql in mutations:
            with self.subTest(sql=sql):
                self.setUp()
                run_id, _ = self.ready()
                self.payload(self.core('create', '--spec-id', 's', '--spec-version', '1', '--request-id', 'other'))
                self.payload(self.operator(*self.arguments(run_id)))
                self.safe_tree()
                with closing(sqlite3.connect(self.db)) as connection:
                    connection.execute('PRAGMA ignore_check_constraints=ON')
                    for statement in sql if isinstance(sql, tuple) else (sql,):
                        connection.execute(statement)
                    connection.commit()
                    self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                before = self.snapshot()
                self.refused(self.core('show', '--run-id', run_id), 1)
                self.refused(self.operator(*self.arguments(run_id)), 1)
                self.refused(self.operator('migrate', '--to', '3'), 1)
                self.assertEqual(self.snapshot(), before)

    def test_u8_common_cli_and_policy_limits(self):
        run = self.seed()[0]
        before = self.snapshot()
        for command in ('unblock', 'migrate', 'block'):
            self.refused(self.core(command), 2)
        response = self.core('policy', '--run-id', run['run_id'], '--action', 'TRANSITION_RUN')
        self.assertEqual(response.returncode, 3)
        self.assertEqual(json.loads(response.stdout)['decision'], 'DENY')
        self.assertEqual(self.snapshot(), before)

    def test_u8_unblock_flags_cannot_grant_targets(self):
        run_id, _ = self.ready()
        before = self.snapshot()
        for flags in (('--target', 'IN_PROGRESS'), ('--target', 'DONE'), ('--target', 'ACCEPTED'),
                      ('--target', 'DELIVERED'), ('--approved', 'true')):
            self.refused(self.operator(*self.arguments(run_id), *flags), 2)
            self.assertEqual(self.snapshot(), before)
        response = self.core('policy', '--run-id', run_id, '--action', 'TRANSITION_RUN')
        self.assertEqual(response.returncode, 3)
        self.assertEqual(json.loads(response.stdout)['decision'], 'DENY')
        self.assertEqual(self.snapshot(), before)

    def test_r1_legacy_v2_seed_and_replay_preserved(self):
        runs, historical = self.v2()
        before = self.snapshot()
        self.assertEqual(self.payload(self.block(runs[0]['run_id'], legacy=True)), historical)
        self.assertEqual(historical['schema_version'], 2)
        self.assertEqual(self.snapshot(), before)
        shown = self.payload(self.core('show', '--run-id', runs[0]['run_id']))
        self.assertEqual((shown['schema_version'], shown['state'], shown['revision']), (2, 'BLOCKED', 1))

    def test_u3_create_key_namespace_and_current_state_replay(self):
        run_id, _ = self.ready()
        result = self.payload(self.operator(*self.arguments(run_id, request='create-0')))
        before = self.snapshot()
        current = self.payload(self.core('create', '--spec-id', ' spec ', '--spec-version', ' v1 ',
                                         '--request-id', 'create-0'))
        self.assertEqual((current['run_id'], current['state'], current['revision']), (run_id, 'PENDING', 2))
        self.assertEqual(self.payload(self.operator(*self.arguments(run_id, request='create-0'))), result)
        self.assertEqual(self.snapshot(), before)
