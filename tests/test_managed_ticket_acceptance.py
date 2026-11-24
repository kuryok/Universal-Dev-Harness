import unittest
from unittest.mock import patch

from tests import test_managed_ticket_contract as fixtures
from tests.test_ticket_persistence import approved
from udh import migrations
from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError


class ManagedTicketAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedTicketContractTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.core = self.fixture.core
        self.run_id = self.fixture.run.run_id

    def test_v8_acceptance_migration_preserves_runs_and_rolls_back(self):
        self.core.migrate(7)
        validate = migrations.validate_schema_v8

        def fail(db):
            validate(db)
            raise CoreError('falha após DDL')

        with patch.object(migrations, 'validate_schema_v8', side_effect=fail):
            with self.assertRaisesRegex(CoreError, 'falha após DDL'):
                self.core.migrate(8)
        self.assertEqual(self.core.store.deep_check()['schema_version'], 7)
        self.assertEqual(self.core.migrate(8), {'schema_version': 8, 'changed': True})
        self.assertFalse(self.core.migrate(8)['changed'])
        self.core.initialize(schema_version=4)
        self.assertEqual(self.core.show(self.run_id), self.fixture.run)
        with self.assertRaisesRegex(ConflictError, 'Gate de início'):
            self.core.start(self.run_id, 0, 'start')

    def done_fixture(self):
        # Migra antes de preparar o snapshot sintético, que não é válido em v7.
        migrate = self.core.migrate

        def migrate_and_prepare(target):
            result = migrate(target)
            if target == 7:
                migrate(8)
            return result

        with patch.object(self.core, 'migrate', side_effect=migrate_and_prepare):
            self.fixture.executor_fixture()
        # Em produção o gate de início gerenciado ainda não está habilitado.
        self.at = self.fixture.verify(self.fixture.passing_runtime)
        self.at_id = self.at['evidence']['evidence_id']
        self.core.complete(self.run_id, 1, 'finish', self.at_id)

    def review(self, **overrides):
        args = dict(expected_revision=2, request_id='review', at_evidence_id=self.at_id,
                    independent_context=True, review_context_ref='review-session', confirmation_ref='human-1',
                    findings=[{'id': 'F1', 'severity': 'LOW', 'resolution': 'resolvido no candidato'}],
                    trust_store=self.fixture.trust)
        return self.core.record_managed_ticket_review(self.run_id, **{**args, **overrides})

    def accept(self, review_id, **overrides):
        args = dict(expected_revision=2, request_id='accept', at_evidence_id=self.at_id,
                    review_evidence_id=review_id, trust_store=self.fixture.trust)
        return self.core.accept_managed_ticket(self.run_id, **{**args, **overrides})

    def test_records_official_acceptance_and_replays(self):
        self.done_fixture()
        review = self.review()
        review_id = review['evidence_id']
        self.assertEqual(self.review(), review)
        accepted = self.accept(review_id)
        self.assertEqual(accepted['commit_sha'], self.fixture.verify_args['commit_sha'])
        self.assertEqual(accepted['artifact_digest'], self.fixture.verify_args['artifact_digest'])
        self.assertEqual(accepted['contract_sha256'], self.fixture.contract['contract_sha256'])
        self.assertEqual(accepted['at_evidence_sha256'], self.at['evidence']['evidence_sha256'])
        self.assertEqual(accepted['review_evidence_sha256'], review['evidence_sha256'])
        self.assertEqual(self.core.store.deep_check()['schema_version'], 8)
        self.assertEqual(self.core.show(self.run_id).state, 'DONE')
        self.core = CoreService(SQLiteStore(self.fixture.db))
        self.core.approve_ticket_input(approved('2'), expected=(self.fixture.ref, 1))
        self.assertEqual(self.accept(review_id), accepted)
        with self.assertRaises(ConflictError):
            self.accept(review_id, expected_revision=1)
        with self.assertRaises(ConflictError):
            self.accept(review_id, request_id='replacement')
        self.assertEqual(self.core.store.deep_check()['schema_version'], 8)

    def test_requires_bound_trusted_at_and_independent_review(self):
        self.done_fixture()
        with self.assertRaises(ConflictError):
            self.accept(self.at_id)
        for overrides in ({'independent_context': False}, {'confirmation_ref': ''},
                          {'findings': [{'id': 'F1', 'severity': 'HIGH', 'resolution': None}]}):
            with self.subTest(overrides=overrides), self.assertRaises(CoreError):
                self.review(**overrides)
        self.assertEqual(len(self.core.show(self.run_id).evidence_refs), 1)
        with self.assertRaises(ConflictError):
            self.core.record_evidence(self.run_id, 'generic', kind='REVIEW_REPORT', verdict='FAIL',
                                      artifact_digest=self.fixture.verify_args['artifact_digest'],
                                      evidence_sha256='c' * 64, collector_route='OPERATOR_LOCAL', summary_ref='file')
        review = self.review()
        for ref in (review['summary_ref'], self.at['evidence']['summary_ref']):
            path = self.fixture.trust.root / ref
            original = path.read_bytes()
            path.chmod(0o644)
            path.write_bytes(original.replace(b'"verdict":"PASS"', b'"verdict":"FAIL"'))
            with self.subTest(ref=ref), self.assertRaises(CoreError):
                self.accept(review['evidence_id'])
            path.write_bytes(original)
        self.assertEqual(self.accept(review['evidence_id'])['run_id'], self.run_id)

    def test_rejects_new_acceptance_after_approval_changes(self):
        self.done_fixture()
        review = self.review()
        self.core.approve_ticket_input(approved('2'), expected=(self.fixture.ref, 1))
        with self.assertRaises(ConflictError):
            self.accept(review['evidence_id'])
        with self.core.store._connection('ro') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM managed_ticket_acceptances').fetchone()[0], 0)
