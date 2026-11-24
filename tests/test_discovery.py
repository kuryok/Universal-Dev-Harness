import unittest
from copy import deepcopy

from udh.discovery import DiscoveryProposalError, select_discovery_questions


def gap(gap_id='A', impact='behavior', *, reason='Muda o que o app faz ao reiniciar.',
        question='O app deve restaurar a última tela ao reiniciar?'):
    return {'id': gap_id, 'impact': impact, 'reason': reason, 'question': question}


class DiscoveryQuestionsTests(unittest.TestCase):
    def test_selects_only_material_questions_in_stable_order(self):
        self.assertEqual(select_discovery_questions({'gaps': []}), {'questions': []})
        cosmetic = gap('C', 'cosmetic', reason='Muda somente a cor do ícone.', question='Qual cor prefere?')
        self.assertEqual(select_discovery_questions({'gaps': [cosmetic]}), {'questions': []})

        behavior = gap('b')
        data = gap('A', 'data', reason='Afeta a recuperação dos dados.', question='Os dados devem sobreviver ao reinício?')
        delivery = gap('D', 'delivery', reason='Define onde o app precisa rodar.', question='O app deve abrir offline?')
        proposal = {'gaps': [behavior, cosmetic, delivery, data]}
        before = deepcopy(proposal)
        expected = {'questions': [data, delivery, behavior]}
        result = select_discovery_questions(proposal)
        self.assertEqual(result, expected)
        self.assertEqual(select_discovery_questions({'gaps': list(reversed(proposal['gaps']))}), expected)
        result['questions'][0]['reason'] = 'Alteração no resultado.'
        self.assertEqual(proposal, before)

    def test_accepts_exact_limits(self):
        first = gap('A' * 64, reason='á' * 512, question='q' * 512)
        gaps = [first] + [gap(f'z{i:02}') for i in range(15)]
        self.assertEqual(select_discovery_questions({'gaps': gaps}), {'questions': gaps})

    def test_rejects_invalid_discovery_proposal(self):
        malformed_items = (
            None, {}, {**gap(), 'approved': True}, gap(1), gap('á'), gap('_A'), gap('A\n'), gap('A' * 65),
            gap(impact=['behavior']), gap(impact='unknown'), gap(reason=''), gap(reason=' espaço'),
            gap(reason='á' * 513), gap(question='Pergunta?\n'), gap(question=None), gap(question='q' * 513),
            gap(impact='cosmetic', question=''),
        )
        proposals = [None, [], {}, {'gaps': (), 'approved': True}, {'gaps': ()}, {'gaps': [], 'approved': True},
                     {'gaps': [gap(f'A{i}') for i in range(17)]}, {'gaps': [gap(), gap(impact='cosmetic')]},
                     *({'gaps': [gap('valid'), item]} for item in malformed_items)]
        for index, proposal in enumerate(proposals):
            with self.subTest(case=index), self.assertRaises(DiscoveryProposalError):
                select_discovery_questions(proposal)
