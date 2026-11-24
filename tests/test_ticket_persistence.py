import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from udh import migrations
from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError


def approved(version='1', digest='a', *, split=False):
    return {
        'change_id': 'change-5', 'spec_version': version, 'spec_sha256': digest * 64,
        'tasks': {'A': ['C1'], 'B': ['C1', 'C2']},
        'task_details': {
            'A': {'mode': 'direct', 'depends_on': [], 'objective': 'Fazer A', 'checks': ['Conferir A']},
            'B': ({'mode': 'split', 'depends_on': ['A']} if split else
                  {'mode': 'direct', 'depends_on': ['A'], 'objective': 'Fazer B', 'checks': ['Conferir B']}),
        },
    }


def identity(spec):
    return {key: spec[key] for key in ('change_id', 'spec_version', 'spec_sha256')}


def proposed(spec):
    return {**identity(spec), 'tickets': [
        {'id': 'b2', 'objective': 'Fazer B2', 'coverage': [{'task_id': 'B', 'criterion_id': 'C2'}],
         'depends_on': ['b1'], 'checks': ['Conferir B2']},
        {'id': 'b1', 'objective': 'Fazer B1', 'coverage': [{'task_id': 'B', 'criterion_id': 'C1'}],
         'depends_on': [], 'checks': ['Conferir B1']},
    ]}


class TicketPersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'core.db'
        self.core = CoreService(SQLiteStore(self.path))
        self.core.initialize(schema_version=4)

    def scalar(self, sql):
        with self.core.store._connection('ro') as db:
            return db.execute(sql).fetchone()[0]

    def test_migrates_v4_explicitly_and_preserves_ledger(self):
        run = self.core.create('legacy', '1', 'create')
        self.core.start(run.run_id, 0, 'start')
        self.core.record_evidence(run.run_id, 'evidence', kind='AT_RESULT', verdict='PASS',
                                  artifact_digest='a' * 64, evidence_sha256='b' * 64,
                                  collector_route='TRUSTED_EXECUTOR', summary_ref='evidence.json',
                                  at_version='sha256:' + 'c' * 64, commit_sha='d' * 40)
        self.core.record_checkpoint(run.run_id, 1, 'checkpoint', 'IMPLEMENT')
        before = self.core.show(run.run_id)
        self.assertEqual(self.scalar('PRAGMA user_version'), 4)
        self.core.migrate(5)
        self.core.migrate(5)
        self.assertEqual(self.scalar('PRAGMA user_version'), 5)
        self.assertEqual(self.core.show(run.run_id), before)
        self.assertEqual(self.core.list_runs(), [before])
        self.core.complete(run.run_id, 1, 'done', before.evidence_refs[0].evidence_id)
        self.assertEqual(self.core.show(run.run_id).state, 'DONE')

    def test_migration_rolls_back(self):
        original = migrations.validate_schema_v5
        def fail_after_ddl(db):
            original(db)
            raise CoreError('falha injetada')
        with patch.object(migrations, 'validate_schema_v5', side_effect=fail_after_ddl):
            with self.assertRaisesRegex(CoreError, 'falha injetada'):
                self.core.migrate(5)
        self.assertEqual(self.scalar('PRAGMA user_version'), 4)
        self.assertEqual(self.scalar("SELECT count(*) FROM sqlite_master WHERE name LIKE 'ticket_plan%'"), 0)
        self.assertEqual(self.core.store.deep_check()['schema_version'], 4)

    def test_v5_supports_existing_initialization_and_doctor(self):
        self.core.migrate(5)
        self.core.initialize(schema_version=4)
        self.core.create('legacy', '1', 'create-after-v5')
        checks = self.core.doctor(db_path=self.path)['checks']
        database = next(check for check in checks if check['id'] == 'database')
        self.assertEqual(database['status'], 'READY')
        self.assertEqual(database['details']['schema_version'], 5)
        self.assertEqual(database['details']['runs_checked'], 1)
        v1 = CoreService(SQLiteStore(self.path.with_name('legacy.db')))
        v1.initialize()
        with self.assertRaises(CoreError):
            v1.migrate(5)
        self.assertEqual(v1.store.deep_check()['schema_version'], 1)

    def test_materializes_approved_tasks_and_subdivision(self):
        self.core.migrate(5)
        direct = approved()
        self.core.approve_ticket_input(direct)
        result = self.core.materialize_ticket_plan(identity(direct), expected_revision=1)
        self.assertEqual([(t['id'], t['depends_on']) for t in result['plan']['tickets']],
                         [('A', []), ('B', ['A'])])
        split = approved('2', split=True)
        self.core.approve_ticket_input(split, expected=(identity(direct), 1))
        invalid = proposed(split)
        invalid['tickets'][0]['depends_on'].append('A')
        with self.assertRaises(CoreError):
            self.core.materialize_ticket_plan(identity(split), expected_revision=2, proposal=invalid)
        self.assertIsNone(self.core.get_ticket_plan(identity(split)))
        result = self.core.materialize_ticket_plan(identity(split), expected_revision=2, proposal=proposed(split))
        self.assertEqual([(t['id'], t['depends_on']) for t in result['plan']['tickets']],
                         [('A', []), ('b1', ['A']), ('b2', ['A', 'b1'])])
        self.assertEqual(self.scalar('SELECT count(*) FROM runs'), 0)

    def test_approved_input_cannot_change_under_same_identity(self):
        self.core.migrate(5)
        spec = approved()
        self.core.approve_ticket_input(spec)
        changed = deepcopy(spec)
        changed['task_details']['B']['depends_on'].clear()
        with self.assertRaises(ConflictError):
            self.core.approve_ticket_input(changed, expected=(identity(spec), 1))
        self.assertIsNone(self.core.get_ticket_plan(identity(spec)))
        result = self.core.materialize_ticket_plan(identity(spec), expected_revision=1)
        self.assertEqual(result['plan']['tickets'][-1]['depends_on'], ['A'])

    def test_replay_after_reopen_and_conflict(self):
        self.core.migrate(5)
        spec = approved(split=True)
        ref = identity(spec)
        self.core.approve_ticket_input(spec)
        proposal = proposed(spec)
        first = self.core.materialize_ticket_plan(ref, expected_revision=1, proposal=proposal)
        self.core = CoreService(SQLiteStore(self.path))
        proposal['tickets'].reverse()
        self.assertEqual(self.core.materialize_ticket_plan(ref, expected_revision=1, proposal=proposal), first)
        proposal['tickets'][0]['objective'] = 'Outro objetivo'
        with self.assertRaises(ConflictError):
            self.core.materialize_ticket_plan(ref, expected_revision=1, proposal=proposal)
        self.assertEqual(self.core.get_ticket_plan(ref), first)
        self.assertEqual(self.scalar('SELECT count(*) FROM ticket_plans'), 1)

    def test_reconciles_only_current_approved_identity(self):
        self.core.migrate(5)
        a, b, c = approved(), approved('2'), approved('2', 'b')
        self.core.approve_ticket_input(a)
        first = self.core.materialize_ticket_plan(identity(a), expected_revision=1)
        self.assertIsNone(self.core.get_ticket_plan_head('change-5')['active_plan_sha256'])
        self.core.reconcile_ticket_plan(identity(a), expected_revision=1, plan_sha256=first['plan_sha256'])
        head = self.core.approve_ticket_input(a, expected=(identity(a), 1))
        self.assertEqual(head['active_plan_sha256'], first['plan_sha256'])
        previous = a
        for revision, spec in enumerate((b, c, a), 2):
            head = self.core.approve_ticket_input(spec, expected=(identity(previous), revision - 1))
            self.assertEqual(head['revision'], revision)
            self.assertIsNone(head['active_plan_sha256'])
            with self.assertRaises(ConflictError):
                self.core.reconcile_ticket_plan(identity(a), expected_revision=1, plan_sha256=first['plan_sha256'])
            previous = spec
        with self.assertRaises(ConflictError):
            self.core.materialize_ticket_plan(identity(b), expected_revision=2)
        self.assertIsNone(self.core.get_ticket_plan(identity(b)))
        self.assertEqual(self.core.materialize_ticket_plan(identity(a), expected_revision=1), first)
        self.assertIsNone(self.core.get_ticket_plan_head('change-5')['active_plan_sha256'])
        self.assertEqual(self.core.get_ticket_plan(identity(a)), first)
        self.core.reconcile_ticket_plan(identity(a), expected_revision=4, plan_sha256=first['plan_sha256'])
        self.assertEqual(self.core.get_ticket_plan_head('change-5')['active_plan_sha256'], first['plan_sha256'])
