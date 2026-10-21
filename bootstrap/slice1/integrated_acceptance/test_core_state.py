"""AT transitórios integrados ao controlador; não constituem aceite oficial."""
import concurrent.futures
from contextlib import closing
import datetime
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import uuid


class InfrastructureError(RuntimeError):
    """Preparação ou execução do ambiente indisponível."""


class CoreStateAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        roots = []
        for name in ('UDH_CANDIDATE_ROOT', 'UDH_TEST_WORK_ROOT'):
            value = os.environ.get(name)
            if not value or not Path(value).is_dir():
                raise InfrastructureError(f'{name} deve apontar para diretório existente')
            roots.append(Path(value).resolve())
        cls.candidate, cls.work = roots
        try:
            prefix = json.loads(os.environ.get('UDH_CLI_PREFIX_JSON', ''))
        except (ValueError, TypeError) as exc:
            raise InfrastructureError('UDH_CLI_PREFIX_JSON deve conter lista JSON de argumentos') from exc
        if not isinstance(prefix, list) or not prefix or any(
            not isinstance(part, str) or not part or '\x00' in part for part in prefix
        ):
            raise InfrastructureError('UDH_CLI_PREFIX_JSON exige lista não vazia de strings não vazias sem NUL')
        cls.cli_prefix = prefix
        if not (cls.candidate / 'udh' / '__main__.py').is_file():
            raise InfrastructureError('Candidato sem entrada udh/__main__.py')
        if cls.work == cls.candidate or cls.candidate.is_relative_to(cls.work):
            raise InfrastructureError('Work root não pode conter o candidato')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.work, prefix='at-core-')
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'state.sqlite'

    def cli(self, command, *args, db=None):
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env['PYTHONNOUSERSITE'] = '1'
        try:
            result = subprocess.run(
                [*self.cli_prefix, command, '--db', str(db or self.db), *args],
                cwd=self.work, env=env, capture_output=True, text=True,
                encoding='utf-8', timeout=15,
            )
            if result.returncode == 125:
                raise InfrastructureError(f'Controlador externo indisponível: {result.stderr}')
            return result
        except (OSError, UnicodeError) as exc:
            raise InfrastructureError('Falha ao iniciar/decodificar processo CLI') from exc
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureError('Transporte CLI excedeu 15 segundos') from exc

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail('stdout deve conter um único objeto JSON')
        self.assertIsInstance(payload, dict)
        return payload

    def failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr.strip())
        self.assertNotIn('Traceback', result.stderr)
        # Idioma exige inspeção humana; não há texto literal imposto pelo contrato.

    def init(self):
        value = self.success(self.cli('init'))
        self.assertIs(type(value.get('schema_version')), int)
        self.assertEqual(value['schema_version'], 1)

    def create(self, spec='slice1', version='v1', request='req-1', db=None):
        return self.cli('create', '--spec-id', spec, '--spec-version', version,
                        '--request-id', request, db=db)

    def show(self, run):
        return self.success(self.cli('show', '--run-id', run['run_id']))

    def timestamp(self, value):
        self.assertIsInstance(value, str)
        self.assertRegex(value, re.compile(r'\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z'))
        try:
            parsed = datetime.datetime.fromisoformat(value[:-1] + '+00:00')
        except ValueError:
            self.fail('Timestamp deve ser RFC 3339 UTC')
        self.assertEqual(parsed.utcoffset(), datetime.timedelta(0))
        self.assertIn('T', value)

    def run_contract(self, value, spec='slice1', version='v1', request='req-1'):
        for key in ('run_id', 'request_id', 'spec_id', 'spec_version', 'state'):
            self.assertIsInstance(value.get(key), str)
        try:
            uuid.UUID(value['run_id'])
        except ValueError:
            self.fail('run_id deve ser UUID válido')
        self.assertEqual((value['spec_id'], value['spec_version'], value['request_id'], value['state']),
                         (spec, version, request, 'PENDING'))
        self.timestamp(value.get('created_at'))
        self.assertIsInstance(value.get('events'), list)
        self.assertEqual(len(value['events']), 1)
        event = value['events'][0]
        self.assertIsInstance(event, dict)
        self.assertIs(type(event.get('sequence')), int)
        self.assertGreater(event['sequence'], 0)
        self.assertEqual(event.get('kind'), 'RUN_CREATED')
        self.timestamp(event.get('created_at'))

    def snapshot(self, db=None):
        with closing(sqlite3.connect(db or self.db)) as conn, conn:
            schema = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            tables = [r[1] for r in schema if r[0] == 'table']
            rows = {name: sorted(conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                    for name in tables}
            return conn.execute('PRAGMA user_version').fetchone()[0], schema, rows

    def counts(self, expected):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for table in ('runs', 'events'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], expected)

    def test_a1_reinitialization_preserves_run(self):
        self.init()
        run = self.success(self.create())
        self.run_contract(run)
        before = self.snapshot()
        self.init()
        self.assertEqual(self.show(run), run)
        self.assertEqual(self.snapshot(), before)
        self.counts(1)

    def test_a2_incompatible_fixtures_unchanged(self):
        fixtures = (
            'CREATE TABLE marker(value TEXT); INSERT INTO marker VALUES ("preservar"); PRAGMA user_version=2;',
            'CREATE TABLE alien(value TEXT); INSERT INTO alien VALUES ("preservar");',
            'CREATE TABLE runs(id TEXT PRIMARY KEY); INSERT INTO runs VALUES ("preservar"); PRAGMA user_version=1;',
        )
        for index, sql in enumerate(fixtures):
            with self.subTest(fixture=index):
                db = Path(self.temp.name) / f'fixture-{index}.sqlite'
                with closing(sqlite3.connect(db)) as conn, conn:
                    conn.executescript(sql)
                before = self.snapshot(db)
                self.failure(self.cli('init', db=db))
                self.assertEqual(self.snapshot(db), before)

    def test_a3_durable_across_processes(self):
        self.init()
        run = self.success(self.create())
        self.run_contract(run)
        self.assertEqual(self.show(run), run)
        self.counts(1)

    def test_a4_event_failure_rolls_back(self):
        self.init()
        prior = self.success(self.create())
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'evento recusado pela fixture'); END")
        before = self.snapshot()
        self.failure(self.create(request='req-fail'))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(prior), prior)
        self.counts(1)

    def test_a5_invalid_inputs_and_missing_database(self):
        self.init()
        prior = self.success(self.create())
        before = self.snapshot()
        for field in ('spec', 'version', 'request'):
            for value in ('', '   '):
                with self.subTest(field=field, value=value):
                    self.failure(self.create(**{field: value}))
                    self.assertEqual(self.snapshot(), before)
        self.failure(self.cli('show', '--run-id', str(uuid.uuid4())))
        for command in ('create', 'show'):
            missing = Path(self.temp.name) / f'missing-{command}.sqlite'
            result = (self.create(db=missing) if command == 'create' else
                      self.cli('show', '--run-id', str(uuid.uuid4()), db=missing))
            self.failure(result)
            self.assertFalse(missing.exists())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(prior), prior)

    def test_a6_literal_references(self):
        self.init()
        values = ("spec'); DROP TABLE runs; --", ' v1 ', "req-'1")
        run = self.success(self.create(*values))
        self.run_contract(run, *values)
        self.assertEqual(self.show(run), run)
        other = self.success(self.create(request='req-2'))
        self.run_contract(other, request='req-2')
        self.assertEqual(self.show(other), other)
        self.assertEqual(self.show(run), run)
        self.counts(2)

    def test_a7_no_promotion_and_persistent_state_constraint(self):
        self.init()
        run = self.success(self.create())
        before = self.snapshot()
        self.failure(self.cli('promote', '--run-id', run['run_id']))
        self.assertEqual(self.snapshot(), before)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            try:
                conn.execute('INSERT INTO runs (id,request_id,spec_id,spec_version,state,created_at) VALUES (?,?,?,?,?,?)',
                             (str(uuid.uuid4()), 'req-accepted', 'slice1', 'v1', 'ACCEPTED', run['created_at']))
            except sqlite3.IntegrityError:
                conn.rollback()
            else:
                conn.rollback()
                self.fail('SQLite permitiu inserir estado ACCEPTED')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(run), run)

    def test_a9_idempotence_and_conflicting_payload(self):
        self.init()
        run = self.success(self.create())
        self.run_contract(run)
        before = self.snapshot()
        self.assertEqual(self.success(self.create()), run)
        self.failure(self.create(version='v2'))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.show(run), run)
        self.counts(1)

    def concurrent_create(self, versions):
        # Dois processos vivos simultaneamente, com prazo finito em cada chamada.
        import threading
        barrier = threading.Barrier(2, timeout=5)
        def invoke(version):
            barrier.wait()
            return self.create(version=version)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke, version) for version in versions]
            return [future.result(timeout=25) for future in futures]

    def test_a10_concurrent_identical_requests(self):
        self.init()
        results = self.concurrent_create(('v1', 'v1'))
        first, second = [self.success(result) for result in results]
        self.run_contract(first)
        self.assertEqual(first, second)
        self.assertEqual(self.show(first), first)
        self.counts(1)

    def test_a10_concurrent_divergent_requests(self):
        self.init()
        results = self.concurrent_create(('v1', 'v2'))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        for version, result in zip(('v1', 'v2'), results):
            if result.returncode == 0:
                winner = self.success(result)
                self.run_contract(winner, version=version)
            else:
                self.failure(result)
        self.assertEqual(self.show(winner), winner)
        self.counts(1)


if __name__ == '__main__':
    unittest.main()
