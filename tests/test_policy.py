"""Testes complementares de limites internos; AT independentes ficam no bootstrap."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from udh.domain import CoreError
from udh.policy import evaluate
from udh.service import CoreService
from udh.store import SQLiteStore


class PolicyTests(unittest.TestCase):
    def test_invalid_input_does_not_access_store(self):
        store = Mock()
        core = CoreService(store)
        for action in ('', ' \t', None):
            with self.subTest(action=action), self.assertRaises(CoreError):
                core.policy('invalid-uuid', action)
        with self.assertRaises(CoreError):
            core.policy('invalid-uuid', 'UNKNOWN')
        store.get.assert_not_called()

    def test_hash_excludes_identity_and_presented_message_is_not_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            core = CoreService(SQLiteStore(Path(folder) / 'state.sqlite'))
            core.initialize()
            run = core.create('spec', 'v1', 'request')
            allowed = evaluate(run, 'READ_RUN')
            allowed['decision'] = 'ALLOW_TO_EXECUTE'
            denied = evaluate(run, 'EXECUTE_AGENT')
            self.assertEqual(denied['decision'], 'DENY')
            other = core.create('spec', 'v1', 'request2')
            self.assertEqual(evaluate(other, 'READ_RUN')['policy_sha256'], denied['policy_sha256'])
            self.assertEqual(evaluate(run, 'READ_RUN')['decision'], 'ALLOW')
