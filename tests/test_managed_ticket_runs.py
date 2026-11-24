import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from unittest.mock import patch

from tests.test_ticket_persistence import approved, identity
from tests import test_managed_ticket_contract as contract_fixtures
from tests.test_trust_store_and_executor import _StubRuntimeManager
from udh import migrations
from udh.domain import CoreError
from udh.executor import TrustedExecutor
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError
from udh.trust_store import TrustStore


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

    def prepare_gate(self):
        self.activate()
        for version in (6, 7, 8):
            self.core.migrate(version)
        self.trust = TrustStore(self.path.parent / 'trust')
        fixtures = self.path.parent / 'complementary-fixtures'
        fixtures.mkdir()
        (fixtures / 'test_fixture.py').write_text(
            'class Example(TestCase):\n    def test_first(self): pass\n    def test_second(self): pass\n',
            encoding='utf-8',
        )
        self.at_version = self.trust.seal_at_suite(fixtures)['at_version']
        self.manifest_path = self.trust.root / 'sealed_at' / self.at_version[7:] / 'manifest.json'
        self.manifest_sha = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        self.first = self.managed()
        self.second = self.managed(ticket_id='B', request_id='managed-b')

    def register(self, run):
        return self.core.register_managed_ticket_contract(
            run.run_id, expected_revision=0, trust_store=self.trust,
            contract={'approval_ref': 'human-1', 'at_version': self.at_version,
                      'at_manifest_sha256': self.manifest_sha, 'context_ids': [], 'allowed_context_ids': [],
                      'unresolved_gaps': [], 'coverage': [
                          {'task_id': run.ticket_id, 'criterion_id': criterion,
                           'test_ids': [f'test_fixture.Example.test_{name}']}
                          for criterion, name in zip(('C1',) if run.ticket_id == 'A' else ('C1', 'C2'),
                                                     ('first', 'second'))]},
        )

    def trusted_start(self, run):
        return self.core.start(run.run_id, 0, f'start-{run.ticket_id}', trust_store=self.trust)

    def complete_first(self):
        self.register(self.first)
        self.trusted_start(self.first)
        candidate_dir = self.path.parent / 'candidate'
        candidate_dir.mkdir()
        (candidate_dir / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
        candidate = self.trust.promote_artifact(candidate_dir, commit_sha='a' * 40)
        executor = TrustedExecutor(core_service=self.core, trust_store=self.trust,
                                   runtime_manager=_StubRuntimeManager(
                                       contract_fixtures.ManagedTicketContractTests.passing_runtime))
        recorded = executor.verify_run(self.first.run_id, request_id='at-a', at_version=self.at_version,
                                       commit_sha=candidate['commit_sha'], artifact_digest=candidate['artifact_digest'])
        self.at_id = recorded['evidence']['evidence_id']
        self.core.complete(self.first.run_id, 1, 'finish-a', self.at_id)

    def accept_first(self):
        review = self.core.record_managed_ticket_review(
            self.first.run_id, expected_revision=2, request_id='review-a', at_evidence_id=self.at_id,
            independent_context=True, review_context_ref='independent-session', confirmation_ref='human-1',
            findings=[], trust_store=self.trust,
        )
        self.core.accept_managed_ticket(self.first.run_id, expected_revision=2, request_id='accept-a',
                                        at_evidence_id=self.at_id, review_evidence_id=review['evidence_id'],
                                        trust_store=self.trust)
        return self.trust.root / review['summary_ref']

    def test_starts_successor_only_after_official_acceptance(self):
        self.prepare_gate()
        self.register(self.second)
        with self.assertRaises(ConflictError):
            self.trusted_start(self.second)
        self.complete_first()
        with self.assertRaises(ConflictError):
            self.trusted_start(self.second)
        self.assertEqual(self.core.show(self.second.run_id), self.second)
        self.accept_first()
        started = self.trusted_start(self.second)
        self.assertEqual(started['to_state'], 'IN_PROGRESS')
        self.assertEqual(self.core.show(self.second.run_id).revision, 1)
        self.assertEqual(self.core.store.deep_check()['schema_version'], 8)

    def test_refuses_stale_or_tampered_gate_inputs(self):
        self.prepare_gate()
        with self.assertRaises(ConflictError):
            self.trusted_start(self.first)
        self.assertEqual(self.core.show(self.first.run_id), self.first)
        self.complete_first()
        path = self.accept_first()
        self.register(self.second)
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(CoreError):
            self.trusted_start(self.second)
        path.write_bytes(original + b' ')
        with self.assertRaises(CoreError):
            self.trusted_start(self.second)
        path.write_bytes(original)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest['scenarios'] = ['alteração sem mudar at_version']
        self.manifest_path.chmod(0o644)
        self.manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(CoreError):
            self.trusted_start(self.second)
        self.assertEqual(self.core.show(self.second.run_id), self.second)

    def test_managed_start_replay_and_legacy_start(self):
        self.prepare_gate()
        self.register(self.first)
        with self.assertRaisesRegex(ConflictError, 'Gate de início gerenciado'):
            self.core.start(self.first.run_id, 0, 'start-A')
        started = self.trusted_start(self.first)
        newer = approved('2')
        self.core.approve_ticket_input(newer, expected=(self.ref, 1))
        self.assertEqual(self.trusted_start(self.first), started)
        with self.assertRaises(ConflictError):
            self.core.start(self.first.run_id, 1, 'start-A', trust_store=self.trust)
        legacy = self.core.create('change-5', '1', 'legacy', ticket_id='A')
        self.assertEqual(self.core.start(legacy.run_id, 0, 'legacy-start')['to_state'], 'IN_PROGRESS')

    def test_successor_cannot_reuse_acceptance_after_approval_cycle(self):
        self.prepare_gate()
        self.complete_first()
        self.accept_first()
        newer = approved('2')
        self.core.approve_ticket_input(newer, expected=(self.ref, 1))
        self.core.approve_ticket_input(approved(), expected=(identity(newer), 2))
        self.activate(revision=3)
        second = self.managed(ticket_id='B', request_id='managed-b-new-generation', approval_revision=3)
        self.register(second)
        with self.assertRaisesRegex(ConflictError, 'sem aceite oficial nesta geração'):
            self.trusted_start(second)
        self.assertEqual(self.core.show(second.run_id), second)

    def test_start_and_approval_are_serialized(self):
        self.prepare_gate()
        self.register(self.first)
        other = CoreService(SQLiteStore(self.path))
        approval_holds_lock, start_attempted = Event(), Event()
        approval_connection = other.store._connection
        start_connection = self.core.store._connection

        @contextmanager
        def hold_approval(mode):
            with approval_connection(mode) as db:
                def trace(sql):
                    if not approval_holds_lock.is_set() and sql.strip().upper().startswith(
                            'SELECT * FROM TICKET_PLAN_HEADS'):
                        approval_holds_lock.set()
                        start_attempted.wait(5)
                db.set_trace_callback(trace)
                yield db

        @contextmanager
        def observe_start(mode):
            with start_connection(mode) as db:
                db.set_trace_callback(lambda sql: start_attempted.set() if sql == 'BEGIN IMMEDIATE' else None)
                yield db

        with patch.object(other.store, '_connection', hold_approval), patch.object(
                self.core.store, '_connection', observe_start), ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(other.approve_ticket_input, approved('2'), expected=(self.ref, 1))
            self.assertTrue(approval_holds_lock.wait(5))
            start = pool.submit(self.trusted_start, self.first)
            approval.result(timeout=5)
            self.assertTrue(start_attempted.is_set())
            with self.assertRaisesRegex(ConflictError, 'revisão aprovada'):
                start.result(timeout=5)
        self.assertEqual(self.core.show(self.first.run_id), self.first)
