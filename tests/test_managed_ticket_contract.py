import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ticket_persistence import approved, identity
from tests.test_trust_store_and_executor import _StubRuntimeManager
from udh import migrations
from udh.domain import CoreError
from udh.executor import TrustedExecutor
from udh.runtime import ContainerResult, RuntimeIsolationError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError
from udh.trust_store import TrustStore


class ManagedTicketContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'core.db'
        self.core = CoreService(SQLiteStore(self.db))
        self.core.initialize(schema_version=4)
        for target in (5, 6):
            self.core.migrate(target)
        spec = approved()
        self.ref = identity(spec)
        self.core.approve_ticket_input(spec)
        plan = self.core.materialize_ticket_plan(self.ref, expected_revision=1)
        digest = plan['plan_sha256']
        self.core.reconcile_ticket_plan(self.ref, expected_revision=1, plan_sha256=digest)
        self.run = self.core.create_managed_ticket_run(
            self.ref, approval_revision=1, plan_sha256=digest, ticket_id='B', request_id='managed', mode='STANDARD',
        )

    def coverage_fixture(self):
        self.trust = TrustStore(self.root / 'trust')
        fixtures = self.root / 'complementary-fixtures'
        fixtures.mkdir()
        # Dados sintéticos de unittest: importar esta fixture falharia.
        (fixtures / 'test_fixture.py').write_text(
            'raise RuntimeError("não importe")\nclass Example(TestCase):\n'
            '    def test_first(self): pass\n    def test_second(self): pass\n', encoding='utf-8',
        )
        seal = self.trust.seal_at_suite(fixtures, scenarios=['B/C1', 'B/C2'])
        self.manifest = self.trust.root / 'sealed_at' / seal['at_version'][7:] / 'manifest.json'
        return {
            'approval_ref': 'approved-1', 'at_version': seal['at_version'],
            'at_manifest_sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            'coverage': [{'task_id': 'B', 'criterion_id': 'C1', 'test_ids': ['test_fixture.Example.test_first']},
                         {'task_id': 'B', 'criterion_id': 'C2', 'test_ids': ['test_fixture.Example.test_second']}],
            'allowed_context_ids': ['source', 'notes'], 'context_ids': ['source'], 'unresolved_gaps': [],
        }

    def register(self, payload, *, revision=0):
        return self.core.register_managed_ticket_contract(
            self.run.run_id, expected_revision=revision, contract=payload, trust_store=self.trust,
        )

    def test_contract_migration_preserves_managed_runs(self):
        self.assertTrue(self.core.migrate(7)['changed'])
        self.assertFalse(self.core.migrate(7)['changed'])
        self.core.initialize(schema_version=4)
        self.assertEqual(self.core.show(self.run.run_id), self.run)
        self.assertIsNone(self.core.get_managed_ticket_contract(self.run.run_id))
        report = self.core.doctor(db_path=self.db)
        database = next(check for check in report['checks'] if check['id'] == 'database')
        self.assertEqual(database['details']['schema_version'], 7)
        with self.assertRaises(ConflictError):
            self.core.start(self.run.run_id, 0, 'start')

    def test_contract_migration_rolls_back(self):
        validate = migrations.validate_schema_v7

        def fail(db):
            validate(db)
            raise CoreError('falha simulada')

        with patch.object(migrations, 'validate_schema_v7', side_effect=fail):
            with self.assertRaisesRegex(CoreError, 'falha simulada'):
                self.core.migrate(7)
        with self.core.store._connection('ro') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='managed_ticket_contracts'").fetchone())

    def test_registers_complete_immutable_contract(self):
        self.core.migrate(7)
        payload = self.coverage_fixture()
        result = self.register(payload)
        self.assertEqual(result['contract']['run_id'], self.run.run_id)
        self.assertEqual(result['contract']['ticket_id'], 'B')
        self.core = CoreService(SQLiteStore(self.db))
        payload['coverage'].reverse()
        payload['allowed_context_ids'].reverse()
        self.assertEqual(self.register(payload), result)
        self.assertEqual(self.core.get_managed_ticket_contract(self.run.run_id), result)
        with self.assertRaises(ConflictError):
            self.register({**payload, 'context_ids': ['notes']})
        newer = approved('2')
        self.core.approve_ticket_input(newer, expected=(self.ref, 1))
        with self.assertRaises(ConflictError):
            self.register(payload)
        self.assertEqual(self.core.get_managed_ticket_contract(self.run.run_id), result)

    def test_rejects_invalid_contract_without_write(self):
        self.core.migrate(7)
        payload = self.coverage_fixture()
        coverage = payload['coverage']
        for change in (
            {'coverage': coverage[:1]}, {'coverage': [*coverage, coverage[0]]},
            {'coverage': [coverage[0], {**coverage[1], 'task_id': 'A'}]},
            {'coverage': [coverage[0], {**coverage[1], 'test_ids': ['test_fixture.Example.test_missing']}]},
            {'context_ids': ['missing']}, {'unresolved_gaps': ['comportamento indefinido']},
            {'approval_ref': ''}, {'run_id': self.run.run_id},
        ):
            with self.subTest(change=change), self.assertRaises(CoreError):
                self.register({**payload, **change})
        with self.assertRaises(ConflictError):
            self.register(payload, revision=1)
        self.assertIsNone(self.core.get_managed_ticket_contract(self.run.run_id))

    def test_rejects_manifest_change_with_same_at_version(self):
        self.core.migrate(7)
        payload = self.coverage_fixture()
        manifest = json.loads(self.manifest.read_bytes())
        manifest['scenarios'] = ['outro cenário']
        self.manifest.chmod(0o644)
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')
        self.trust.verify_sealed_at(payload['at_version'])
        with self.assertRaisesRegex(CoreError, 'manifesto'):
            self.register(payload)
        self.assertIsNone(self.core.get_managed_ticket_contract(self.run.run_id))

    def executor_fixture(self, *, register=True):
        self.core.migrate(7)
        payload = self.coverage_fixture()
        self.contract = self.register(payload) if register else None
        with self.assertRaisesRegex(ConflictError, 'Gate de início'):
            self.core.start(self.run.run_id, 0, 'start')
        # Snapshot sintético para testar F-04, sem uma rota de início em produção.
        with self.core.store._connection('rw') as db:
            db.execute('''INSERT INTO transitions (run_id, revision, operation, kind, from_state, to_state,
                          request_id, created_at, actor_route) VALUES
                          (?, 1, 'START_RUN', 'RUN_STARTED', 'PENDING', 'IN_PROGRESS', ?, ?, 'CORE_LOCAL')''',
                       (self.run.run_id, 'fixture-start', self.run.created_at))
            db.execute("UPDATE runs SET state='IN_PROGRESS', revision=1 WHERE id=?", (self.run.run_id,))
            db.commit()
        workspace = self.root / 'candidate'
        workspace.mkdir()
        (workspace / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.candidate = self.trust.promote_artifact(workspace, commit_sha='a' * 40)
        self.verify_args = dict(request_id='verify', artifact_digest=self.candidate['artifact_digest'],
                                commit_sha=self.candidate['commit_sha'], at_version=payload['at_version'])

    def verify(self, runtime, **overrides):
        return TrustedExecutor(core_service=self.core, trust_store=self.trust,
                               runtime_manager=_StubRuntimeManager(runtime)).verify_run(
            self.run.run_id, **{**self.verify_args, **overrides},
        )

    @staticmethod
    def passing_runtime(kwargs):
        tests = ['test_fixture.Example.test_first', 'test_fixture.Example.test_second']
        stdout = json.dumps(dict(udhf04=1, nonce=json.loads(kwargs['stdin_bytes'])['nonce'], ok=True,
                                 tampering_ok=True, tests_run=2, errors=0, failures=0, skipped=0,
                                 expected_tests=tests, passed_tests=tests))
        return ContainerResult('stub', 'artifact', kwargs['image_ref'], 0, stdout, '', None, 0.01, {}, True)

    def assert_report_binding(self, result, verdict):
        raw = Path(result['evidence_path']).read_bytes()
        report = json.loads(raw)
        self.assertEqual(report, result['report'])
        self.assertEqual(report['verdict'], verdict)
        self.assertEqual(report['contract_sha256'], self.contract['contract_sha256'])
        self.assertEqual(report['run_id'], self.run.run_id)
        for field in ('at_version', 'commit_sha', 'artifact_digest'):
            self.assertEqual(report[field], self.verify_args[field])
        evidence = self.core.show(self.run.run_id).evidence_refs[-1]
        self.assertEqual(evidence.verdict, verdict)
        self.assertEqual(evidence.evidence_sha256, hashlib.sha256(raw).hexdigest())

    def test_executor_rejects_missing_managed_contract(self):
        self.executor_fixture(register=False)
        with self.assertRaises(ConflictError):
            self.verify(lambda _: self.fail('runtime não deve iniciar'))
        self.assertFalse(self.core.show(self.run.run_id).evidence_refs)

    def test_executor_requires_pinned_managed_contract(self):
        self.executor_fixture()
        for override in ({'at_version': 'sha256:' + '0' * 64}, {'commit_sha': 'b' * 40},
                         {'commit_sha': None}, {'artifact_digest': None}, {'kind': 'SMOKE_RESULT'}):
            with self.subTest(override=override), self.assertRaises(CoreError):
                self.verify(lambda _: self.fail('runtime não deve iniciar'), **override)
        self.assertFalse(self.core.show(self.run.run_id).evidence_refs)

    def test_executor_rechecks_full_manifest(self):
        self.executor_fixture()
        manifest = json.loads(self.manifest.read_bytes())
        manifest['scenarios'] = ['outro cenário']
        self.manifest.chmod(0o644)
        self.manifest.write_text(json.dumps(manifest), encoding='utf-8')
        result = self.verify(lambda _: self.fail('runtime não deve iniciar'))
        self.assertEqual(result['report']['reason_code'], 'AT_INTEGRITY_VIOLATION')
        self.assert_report_binding(result, 'ERROR')

    def test_executor_binds_report_to_contract(self):
        self.executor_fixture()
        self.assert_report_binding(self.verify(self.passing_runtime), 'PASS')

        def runtime_error(_):
            raise RuntimeIsolationError('falha sintética')

        self.assert_report_binding(self.verify(runtime_error, request_id='verify-error'), 'ERROR')

    def test_executor_drops_pass_on_approval_change(self):
        self.executor_fixture()

        def change_approval(kwargs):
            other = CoreService(SQLiteStore(self.db))
            other.approve_ticket_input(approved('2'), expected=(self.ref, 1))
            return self.passing_runtime(kwargs)

        with self.assertRaises(ConflictError):
            self.verify(change_approval)
        self.assertFalse(self.core.show(self.run.run_id).evidence_refs)
        self.assertFalse(list(self.trust.root.glob('evidence/**/*.json')))
        with self.assertRaises(ConflictError):
            self.verify(lambda _: self.fail('runtime não deve iniciar com plano obsoleto'))

    def test_generic_evidence_cannot_register_managed_pass(self):
        self.executor_fixture()
        with self.assertRaises(ConflictError):
            self.core.record_evidence(self.run.run_id, 'direct', kind='AT_RESULT', verdict='PASS',
                                      evidence_sha256='b' * 64, artifact_digest=self.candidate['artifact_digest'],
                                      collector_route='TRUSTED_EXECUTOR', summary_ref='unverified.json',
                                      at_version=self.verify_args['at_version'], commit_sha='a' * 40)
        self.assertFalse(self.core.show(self.run.run_id).evidence_refs)
