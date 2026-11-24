import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ticket_persistence import approved, identity
from udh import migrations
from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError


class ManagedTicketRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'core.db'
        self.core = CoreService(SQLiteStore(self.path))
        self.core.initialize(schema_version=4)
        self.core.migrate(5)
        spec = approved()
        self.ref = identity(spec)
        self.core.approve_ticket_input(spec)
        self.plan = self.core.materialize_ticket_plan(self.ref, expected_revision=1)
        self.digest = self.plan['plan_sha256']

    def activate(self, revision=1):
        self.core.reconcile_ticket_plan(self.ref, expected_revision=revision, plan_sha256=self.digest)

    def managed(self, **overrides):
        args = dict(approval_revision=1, plan_sha256=self.digest, ticket_id='A',
                    request_id='managed', mode='STANDARD')
        return self.core.create_managed_ticket_run(self.ref, **(args | overrides))

    def scalar(self, sql):
        with self.core.store._connection('ro') as db:
            return db.execute(sql).fetchone()[0]

    def test_v6_migration_preserves_plans_and_ledger(self):
        run = self.core.create('change-5', '1', 'legacy', ticket_id='A')
        self.activate()
        head = self.core.get_ticket_plan_head('change-5')
        self.assertTrue(self.core.migrate(6)['changed'])
        self.assertFalse(self.core.migrate(6)['changed'])
        self.core.initialize(schema_version=4)
        report = self.core.doctor(db_path=self.path)
        database = next(check for check in report['checks'] if check['id'] == 'database')
        self.assertEqual(database['details']['schema_version'], 6)
        self.assertEqual(self.core.show(run.run_id), run)
        self.assertEqual(self.core.get_ticket_plan(self.ref), self.plan)
        self.assertEqual(self.core.get_ticket_plan_head('change-5'), head)
        self.assertEqual(self.scalar('SELECT count(*) FROM managed_ticket_runs'), 0)

    def test_v6_migration_rolls_back(self):
        validate = migrations.validate_schema_v6

        def fail_after_ddl(db):
            validate(db)
            raise CoreError('falha simulada')

        with patch.object(migrations, 'validate_schema_v6', side_effect=fail_after_ddl):
            with self.assertRaisesRegex(CoreError, 'falha simulada'):
                self.core.migrate(6)
        self.assertEqual(self.scalar('PRAGMA user_version'), 5)
        self.assertEqual(self.scalar("SELECT count(*) FROM sqlite_master WHERE name = 'managed_ticket_runs'"), 0)
        self.assertEqual(self.core.get_ticket_plan(self.ref), self.plan)

    def test_managed_creation_is_atomic(self):
        self.activate()
        self.core.migrate(6)
        # A falha no banco ocorre depois da gravação do run e de CREATE_RUN.
        with self.core.store._connection('rw') as db:
            db.execute("CREATE TRIGGER fail_binding BEFORE INSERT ON managed_ticket_runs "
                       "BEGIN SELECT RAISE(ABORT, 'falha de vínculo'); END")
        validate = migrations.validate_schema_v6
        # Somente o esquema adulterado de propósito é admitido neste teste de rollback.
        with patch.object(migrations, 'validate_schema_v6'):
            with self.assertRaises(CoreError) as error:
                self.managed()
        self.assertEqual(str(error.exception.__cause__), 'falha de vínculo')
        with self.core.store._connection('rw') as db:
            db.execute('DROP TRIGGER fail_binding')
            validate(db)
        for table in ('runs', 'transitions', 'managed_ticket_runs'):
            self.assertEqual(self.scalar(f'SELECT count(*) FROM {table}'), 0)
        run = self.managed()
        self.assertEqual(run.state, 'PENDING')
        self.assertEqual(self.scalar('SELECT run_id FROM managed_ticket_runs'), run.run_id)
        self.assertEqual([event.operation for event in run.events], ['CREATE_RUN'])

    def test_managed_creation_replay_and_conflicts(self):
        self.core.migrate(6)
        with self.assertRaises(ConflictError):
            self.managed()
        self.activate()
        for overrides in ({'plan_sha256': 'f' * 64}, {'ticket_id': 'missing'}, {'approval_revision': 2}):
            with self.subTest(overrides=overrides), self.assertRaises(ConflictError):
                self.managed(**overrides)
        run = self.managed()
        self.core = CoreService(SQLiteStore(self.path))
        self.assertEqual(self.managed(), run)
        for overrides in ({'mode': 'DEEP'}, {'ticket_id': 'B'}, {'request_id': 'duplicate'}):
            with self.subTest(overrides=overrides), self.assertRaises(ConflictError):
                self.managed(**overrides)
        with self.assertRaises(ConflictError):
            self.core.create('change-5', '1', 'managed', ticket_id='A')
        self.core.create('change-5', '1', 'legacy', ticket_id='B')
        with self.assertRaises(ConflictError):
            self.managed(ticket_id='B', request_id='legacy')

    def test_managed_start_stays_closed(self):
        self.activate()
        self.core.migrate(6)
        managed = self.managed()
        legacy = self.core.create('change-5', '1', 'legacy', ticket_id='A')
        for _ in range(2):
            with self.assertRaisesRegex(ConflictError, 'Gate de início gerenciado'):
                self.core.start(managed.run_id, 0, 'start-managed')
        self.assertEqual(self.core.show(managed.run_id), managed)
        started = self.core.start(legacy.run_id, 0, 'start-legacy')
        self.assertEqual(self.core.start(legacy.run_id, 0, 'start-legacy'), started)
        self.assertEqual(self.core.show(legacy.run_id).state, 'IN_PROGRESS')

    def test_approval_cycle_does_not_reuse_link(self):
        self.activate()
        self.core.migrate(6)
        old = self.managed()
        second = approved('2')
        self.core.approve_ticket_input(second, expected=(self.ref, 1))
        self.assertEqual(self.managed(), old)
        with self.assertRaises(ConflictError):
            self.managed(ticket_id='B', request_id='stale')
        self.core.approve_ticket_input(approved(), expected=(identity(second), 2))
        self.activate(revision=3)
        new = self.managed(approval_revision=3, request_id='new')
        self.assertNotEqual(new.run_id, old.run_id)
        self.assertEqual(self.managed(), old)
        self.assertEqual(self.scalar('SELECT count(*) FROM managed_ticket_runs'), 2)

    def test_doctor_rejects_binding_inconsistent_with_ledger(self):
        self.core.migrate(6)
        self.activate()
        run = self.managed()
        with self.core.store._connection('rw') as db:
            db.execute('UPDATE managed_ticket_runs SET ticket_id = ? WHERE run_id = ?', ('B', run.run_id))
        with self.assertRaisesRegex(CoreError, 'Vínculo de execução gerenciada incompatível'):
            self.core.store.deep_check()
