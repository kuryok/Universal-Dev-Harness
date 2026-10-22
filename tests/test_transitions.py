"""Verificações complementares do bloqueio; não substituem aceite independente."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import AuthorizationError, ConflictError


ROOT = Path(__file__).resolve().parents[1]


def operator_context(**changes):
    context = dict(schema=1, db_path='/state/udh.sqlite',
                   controller_route='OPERATOR_LOCAL', authorization_rule_version=1,
                   allowed_operations=('MIGRATE_V2', 'BLOCK_PENDING'),
                   core_manifest_sha256='a' * 64, control_sha256='b' * 64)
    context.update(changes)
    return context


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='udh-block-', dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'execução ?#.sqlite'
        self.store = SQLiteStore(self.path)
        self.service = CoreService(self.store)
        self.service.initialize()
        self.run = self.service.create('spec', 'v1', 'criação')

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return tuple(connection.iterdump()), connection.execute('PRAGMA user_version').fetchone()[0]

    def block(self, **changes):
        arguments = dict(run_id=self.run.run_id, expected_revision=0,
                         request_id='bloqueio', reason='aguardar decisão',
                         context=operator_context())
        arguments.update(changes)
        return self.service.block(**arguments)

    def test_v1_block_is_rejected_without_implicit_migration(self):
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.block()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.service.show(self.run.run_id), self.run)

    def test_literal_payload_and_original_measurements_survive_replay(self):
        self.service.migrate()
        payload = dict(request_id=' pedido\t', reason='\n decisão e\u0301 \t')
        result = self.block(**payload)
        before = self.snapshot()
        replay = self.block(**payload, context=operator_context(
            core_manifest_sha256='c' * 64, control_sha256='d' * 64))
        self.assertEqual(replay, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result['request_id'], payload['request_id'])
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute('SELECT request_id, reason, core_manifest_sha256, '
                                     'control_sha256 FROM block_requests').fetchone()
        self.assertEqual(row, (payload['request_id'], payload['reason'], 'a' * 64, 'b' * 64))
        event = self.service.show(self.run.run_id).to_dict()['events'][1]
        self.assertEqual(event['reason'], payload['reason'])
        self.assertEqual(event['authorization_id'], result['authorization_id'])

    def test_global_request_id_conflicts_with_other_run_and_changed_payload(self):
        self.service.migrate()
        self.block()
        other = self.service.create('spec', 'v1', 'outra criação')
        before = self.snapshot()
        for changes in (dict(run_id=other.run_id), dict(reason='outro motivo'),
                        dict(expected_revision=1), dict(reason='aguardar decisão ')):
            with self.subTest(changes=changes), self.assertRaises(ConflictError):
                self.block(**changes)
            self.assertEqual(self.snapshot(), before)

    def test_invalid_payload_is_rejected_before_store_io(self):
        store = Mock(spec=SQLiteStore)
        service = CoreService(store)
        for changes in (dict(expected_revision=True), dict(expected_revision=-1),
                        dict(expected_revision=0.0), dict(reason=' \t\n'),
                        dict(request_id='\t\n'), dict(run_id='inválido')):
            arguments = dict(run_id=self.run.run_id, expected_revision=0,
                             request_id='bloqueio', reason='motivo', context=operator_context())
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(CoreError):
                service.block(**arguments)
            self.assertEqual(store.mock_calls, [])

    def test_invalid_context_is_denied_before_store_io(self):
        store = Mock(spec=SQLiteStore)
        service = CoreService(store)
        contexts = (None, {}, operator_context(schema=True),
                    operator_context(authorization_rule_version=True),
                    operator_context(db_path=str(self.path)),
                    operator_context(controller_route='CANDIDATE'),
                    operator_context(allowed_operations=['MIGRATE_V2', 'BLOCK_PENDING']),
                    operator_context(core_manifest_sha256='A' * 64),
                    operator_context(control_sha256='0' * 63))
        for context in contexts:
            with self.subTest(context=context), self.assertRaises(AuthorizationError):
                service.block(self.run.run_id, 0, 'bloqueio', 'motivo', context=context)
            self.assertEqual(store.mock_calls, [])

    def test_each_write_failure_rolls_back_complete_transition(self):
        self.service.migrate()
        original_connection = self.store._connection
        before = self.snapshot()

        class FailingConnection:
            def __init__(self, connection, fail_at):
                self.connection = connection
                self.fail_at = fail_at
                self.writes = 0

            def execute(self, sql, *args, **kwargs):
                if sql.lstrip().split()[0].upper() in ('INSERT', 'UPDATE', 'DELETE'):
                    self.writes += 1
                    if self.writes == self.fail_at:
                        raise sqlite3.OperationalError('Falha complementar injetada.')
                return self.connection.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.connection, name)

        for fail_at in (1, 2, 3):
            proxies = []

            @contextmanager
            def connection_with_failure(mode):
                with original_connection(mode) as connection:
                    proxy = FailingConnection(connection, fail_at)
                    proxies.append(proxy)
                    yield proxy

            with self.subTest(write=fail_at):
                with patch.object(self.store, '_connection', connection_with_failure):
                    with self.assertRaises(CoreError):
                        self.block()
                self.assertTrue(any(proxy.writes == fail_at for proxy in proxies))
                self.assertEqual(self.snapshot(), before)
                run = self.service.show(self.run.run_id)
                self.assertEqual((run.state, run.revision, len(run.events)), ('PENDING', 0, 1))
        self.assertEqual(self.block()['revision'], 1)


if __name__ == '__main__':
    unittest.main()
