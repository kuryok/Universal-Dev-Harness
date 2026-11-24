import hashlib
import unittest

from udh.discovery import DiscoveryProposalError
from udh.discovery_session import DiscoverySession, DiscoverySessionError


def gap(gap_id, *, impact='behavior', question='O que acontece ao reiniciar?'):
    return {'id': gap_id, 'impact': impact, 'reason': 'Define o comportamento após reinício.', 'question': question}


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class DiscoverySessionTests(unittest.TestCase):
    def test_accumulates_material_gaps_without_silent_removal(self):
        original = gap('Z')
        first = DiscoverySession().apply_round({'gaps': [original, gap('color', impact='cosmetic'), gap('A')]})
        original['question'] = 'Mudança na proposta original'
        answer = 'Recuperar a última tela.'
        resolved = first.record_answer('A', answer).confirm_resolution('A', digest(answer))
        second = resolved.apply_round({'gaps': [gap('Z'), gap('B', impact='data')]})
        third = second.apply_round({'gaps': []})
        self.assertEqual(tuple(item.id for item in first.open_gaps), ('A', 'Z'))
        self.assertEqual(tuple(item.id for item in third.open_gaps), ('B', 'Z'))
        self.assertEqual(third.open_gaps[-1].question, gap('Z')['question'])
        self.assertFalse(third.ready_for_document_review)
        self.assertEqual(third.rounds, 3)

    def test_empty_proposal_only_allows_document_review(self):
        initial = DiscoverySession()
        self.assertEqual(initial.open_gaps, ())
        self.assertFalse(initial.ready_for_document_review)
        for proposal in ({'gaps': []}, {'gaps': [gap('color', impact='cosmetic')]}):
            with self.subTest(proposal=proposal):
                session = initial.apply_round(proposal)
                self.assertTrue(session.ready_for_document_review)
                self.assertEqual(session.open_gaps, ())
                self.assertEqual(session.rounds, 1)
        self.assertEqual(initial.rounds, 0)

    def test_resolution_requires_confirmation_for_current_answer(self):
        initial = DiscoverySession().apply_round({'gaps': [gap('A')]})
        first_answer = 'Não restaurar a tela.'
        with self.assertRaises(DiscoverySessionError):
            initial.confirm_resolution('A', digest(first_answer))
        for invalid in ('', ' espaço', 'x' * 1025, '\ud800', False):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(DiscoverySessionError):
                initial.record_answer('A', invalid)
        with self.assertRaises(DiscoverySessionError):
            initial.record_answer('X', first_answer)
        pending = initial.record_answer('A', first_answer)
        self.assertFalse(pending.ready_for_document_review)
        self.assertEqual(pending.open_gaps[0].answer, first_answer)
        with self.assertRaises(DiscoverySessionError):
            pending.confirm_resolution('X', digest(first_answer))
        confirmed = pending.confirm_resolution('A', digest(first_answer))
        self.assertTrue(confirmed.ready_for_document_review)
        self.assertTrue(confirmed.record_answer('A', first_answer).ready_for_document_review)
        second_answer = 'á' * 1024
        changed = confirmed.record_answer('A', second_answer)
        self.assertFalse(changed.ready_for_document_review)
        with self.assertRaises(DiscoverySessionError):
            changed.confirm_resolution('A', digest(first_answer))
        self.assertTrue(changed.confirm_resolution('A', digest(second_answer)).ready_for_document_review)

    def test_round_limits_and_conflicting_ids_are_atomic(self):
        gaps = [gap(f'gap-{index:02d}') for index in range(16)]
        first = DiscoverySession().apply_round({'gaps': gaps})
        for proposal in ({'gaps': [gap('extra')]},
                         {'gaps': [gap('gap-00', question='Outra decisão?')]},
                         {'gaps': [gap('gap-00', impact='cosmetic')]}):
            with self.subTest(proposal=proposal), self.assertRaises(DiscoverySessionError):
                first.apply_round(proposal)
        with self.assertRaises(DiscoveryProposalError):
            first.apply_round({'gaps': gaps[:2], 'confirmed': True})
        self.assertEqual(first.rounds, 1)
        self.assertEqual(tuple(item.id for item in first.open_gaps), tuple(item['id'] for item in gaps))
        third = first.apply_round({'gaps': gaps}).apply_round({'gaps': []})
        with self.assertRaises(DiscoverySessionError):
            third.apply_round({'gaps': []})
        answer = 'Não restaurar.'
        settled = third.record_answer('gap-00', answer).confirm_resolution('gap-00', digest(answer))
        self.assertEqual(settled.rounds, 3)
        self.assertEqual(len(settled.open_gaps), 15)
        self.assertEqual(len(third.open_gaps), 16)
