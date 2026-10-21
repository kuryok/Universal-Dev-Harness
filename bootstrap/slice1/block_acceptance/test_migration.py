"""Aceite independente da migração; somente transporte e SQLite confiáveis no host."""
from contextlib import closing
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
        raise RuntimeError('Prefixo de transporte inválido')
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
        self.assertIn(table, ('runs', 'events', 'block_requests'))
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
        self.assertEqual(len(checkpoints), len(set(checkpoints)))
        self.assertTrue(any(point.startswith('BEFORE_WRITE:') for point in checkpoints))
        self.assertTrue(any(point.startswith('AFTER_COMMIT:') for point in checkpoints))
        for point in checkpoints:
            self.assertRegex(point, r'^(BEFORE_WRITE|AFTER_COMMIT):[0-9]+$')
        return checkpoints

    def interrupt(self, checkpoint, *args):
        observation = self.observe(checkpoint, *args)
        self.assertEqual(observation['outcome'], 'OBSERVED_INTERRUPT')
        self.assertEqual(observation['checkpoint'], checkpoint)
        for key in ('marker_received', 'process_killed', 'runtime_removed'):
            self.assertIs(observation[key], True)
        self.safe_tree()
        # O SQLite confiável recupera eventual hot journal antes da leitura RO.
        with closing(sqlite3.connect(self.db.as_uri() + '?mode=rw', uri=True)) as connection:
            connection.execute('SELECT count(*) FROM sqlite_master').fetchone()


class MigrationAcceptance(OperatorFixture):
    def test_m1_empty_and_populated_preserve_and_replay(self):
        for count in (0, 3):
            with self.subTest(count=count):
                if self.db.exists():
                    self.db.unlink()
                self.seed(count)
                runs, events = self.rows('runs'), self.rows('events')
                self.assertEqual(self.payload(self.operator('migrate', '--to', '2')),
                                 {'schema_version': 2, 'migrated': True})
                self.assertEqual(self.rows('runs'), [dict(row, revision=0) for row in runs])
                self.assertEqual(self.rows('events'), events)
                self.assertEqual(self.rows('block_requests'), [])
                before = self.snapshot()
                self.assertEqual(before[0], 2)
                self.assertEqual(self.payload(self.operator('migrate', '--to', '2')),
                                 {'schema_version': 2, 'migrated': False})
                self.assertEqual(self.snapshot(), before)

    def test_m2_missing_rejected_without_creation(self):
        result = self.operator('migrate', '--to', '2')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertFalse(self.db.exists())

    def test_m2_invalid_database_unchanged(self):
        mutations = ('PRAGMA user_version=0', 'PRAGMA user_version=99',
                     'CREATE TABLE unexpected(x TEXT)',
                     'CREATE TRIGGER unexpected AFTER INSERT ON runs BEGIN SELECT 1; END',
                     "UPDATE runs SET id='invalid-uuid'")
        for sql in mutations:
            with self.subTest(sql=sql):
                if self.db.exists():
                    self.db.unlink()
                self.seed()
                with closing(sqlite3.connect(self.db)) as connection:
                    connection.execute(sql)
                    connection.commit()
                before = self.snapshot()
                result = self.operator('migrate', '--to', '2')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_r1_v1_formats_and_common_refusal(self):
        run = self.seed()[0]
        shown = self.payload(self.core('show', '--run-id', run['run_id']))
        self.assertEqual(shown, run)
        self.assertNotIn('revision', shown)
        self.assertNotIn('schema_version', shown)
        before = self.snapshot()
        for command in ('migrate', 'block'):
            self.assertEqual(self.core(command).returncode, 2)
            self.assertEqual(self.snapshot(), before)

    def test_m3_concurrent_migration(self):
        from concurrent.futures import ThreadPoolExecutor
        self.seed(3)
        runs, events = self.rows('runs'), self.rows('events')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.operator('migrate', '--to', '2'), range(2)))
        payloads = [self.payload(result) for result in results]
        self.assertEqual(sorted(payload['migrated'] for payload in payloads), [False, True])
        self.assertTrue(all(payload['schema_version'] == 2 for payload in payloads))
        self.assertEqual(self.rows('runs'), [dict(row, revision=0) for row in runs])
        self.assertEqual(self.rows('events'), events)
        self.assertEqual(self.rows('block_requests'), [])

    def test_m3_instrumented_interruption_and_replay(self):
        self.seed(3)
        checkpoints = self.discover('migrate', '--to', '2')
        for point in checkpoints:
            with self.subTest(checkpoint=point):
                self.setUp()
                self.seed(3)
                runs, events = self.rows('runs'), self.rows('events')
                before = self.snapshot()
                self.interrupt(point, 'migrate', '--to', '2')
                if point.startswith('BEFORE_WRITE:'):
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.payload(self.operator('migrate', '--to', '2')),
                                     {'schema_version': 2, 'migrated': True})
                else:
                    self.assertEqual(self.snapshot()[0], 2)
                    self.assertEqual(self.payload(self.operator('migrate', '--to', '2')),
                                     {'schema_version': 2, 'migrated': False})
                self.assertEqual(self.rows('runs'), [dict(row, revision=0) for row in runs])
                self.assertEqual(self.rows('events'), events)
                self.assertEqual(self.rows('block_requests'), [])
