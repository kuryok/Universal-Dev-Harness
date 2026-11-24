import unittest
from copy import deepcopy
from itertools import permutations

from udh.ticket_plan import TicketPlanError, validate_ticket_plan


def ticket(ticket_id, coverage=None, depends_on=()):
    return {
        'id': ticket_id, 'objective': 'Entregar comportamento observável',
        'coverage': [{'task_id': task, 'criterion_id': criterion} for task, criterion in
                     (coverage if coverage is not None else [('A', 'C1')])],
        'depends_on': list(depends_on), 'checks': ['Segundo texto', 'Primeiro texto'],
    }


class TicketPlanTests(unittest.TestCase):
    def setUp(self):
        identity = {'change_id': 'change-5', 'spec_version': '1.0', 'spec_sha256': 'a' * 64}
        self.approved = {**identity, 'tasks': {'A': ['C1']}}
        self.proposal = {**identity, 'tickets': [ticket('a')]}

    def assert_invalid(self, approved, proposal):
        with self.assertRaises(TicketPlanError):
            validate_ticket_plan(approved, proposal)

    def test_rejects_unapproved_or_mismatched_spec(self):
        self.assertTrue(issubclass(TicketPlanError, ValueError))
        self.assert_invalid(None, self.proposal)
        for field, value in [('change_id', 'Change-5'), ('spec_version', '2.0'),
                             ('spec_sha256', 'b' * 64), ('approved', True)]:
            with self.subTest(field=field):
                self.assert_invalid(self.approved, {**self.proposal, field: value})
        for invalid in ['x' * 64, 'A' * 64, 'a' * 63, 'sha256:' + 'a' * 64, 'a' * 64 + '\n']:
            with self.subTest(invalid=invalid):
                self.assert_invalid({**self.approved, 'spec_sha256': invalid},
                                    {**self.proposal, 'spec_sha256': invalid})

    def test_rejects_invalid_shapes_and_limits(self):
        for replacement in [None, {}, (), [], self.proposal['tickets'] * 129]:
            self.assert_invalid(self.approved, {**self.proposal, 'tickets': replacement})
        for replacement in [[], {}, {'A': ['C1', 'C1']}, {'A': [f'C{i}' for i in range(33)]},
                            {f'T{i}': ['C1'] for i in range(129)}]:
            self.assert_invalid({**self.approved, 'tasks': replacement}, self.proposal)
        for field, value in [
            ('id', True), ('id', 'a' * 65), ('id', 'a\n'), ('id', 'é'), ('id', '-x'),
            ('objective', ''), ('objective', ' x'), ('objective', 'x\u2003'), ('objective', 'á' * 513),
            ('coverage', []), ('coverage', self.proposal['tickets'][0]['coverage'] * 129),
            ('coverage', [{'task_id': [], 'criterion_id': 'C1'}]),
            ('coverage', [{'task_id': 'A', 'criterion_id': 'C1', 'extra': True}]),
            ('depends_on', [f'a{i}' for i in range(128)]), ('depends_on', [[]]),
            ('checks', []), ('checks', [f'x{i}' for i in range(17)]),
            ('checks', ['x', 'x']), ('checks', ['']), ('checks', ['x\n']), ('checks', ['á' * 513]),
            ('approved', True),
        ]:
            with self.subTest(field=field, value=value):
                self.assert_invalid(self.approved, {**self.proposal, 'tickets': [{**ticket('a'), field: value}]})
        missing = ticket('a')
        del missing['depends_on']
        self.assert_invalid(self.approved, {**self.proposal, 'tickets': [missing]})

        # Um plano válido nos limites máximos de tarefas, tickets, referências e textos.
        task_ids = ['T' * 64] + [f'T{i}' for i in range(1, 128)]
        criteria = [f'C{i}' for i in range(32)]
        approved = {**self.approved, 'tasks': {task: criteria for task in task_ids}}
        ids = ['a' * 64] + [f'b{i}' for i in range(126)]
        items = [ticket(i, [(task, c) for c in criteria]) for i, task in zip(ids, task_ids)]
        items.append(ticket('z', [(task, c) for task in task_ids[-4:] for c in criteria], ids))
        items[-1].update(objective='á' * 512, checks=['á' * 511 + chr(65 + i) for i in range(16)])
        normalized = validate_ticket_plan(approved, {**self.proposal, 'tickets': items})
        self.assertEqual(len(normalized['tickets']), 128)
        self.assertEqual(normalized['tickets'][-1]['id'], 'z')

    def test_rejects_invalid_dependencies(self):
        for items in [
            [ticket('a'), ticket('a')], [ticket('a', depends_on=['missing'])],
            [ticket('a', depends_on=['a'])], [ticket('a'), ticket('b', depends_on=['a', 'a'])],
            [ticket('a', depends_on=['c']), ticket('b', depends_on=['a']), ticket('c', depends_on=['b'])],
        ]:
            with self.subTest(items=items):
                self.assert_invalid(self.approved, {**self.proposal, 'tickets': items})

    def test_stable_topological_order(self):
        items = [ticket('a', depends_on=['c']), ticket('c'), ticket('z')]
        for order in permutations(items):
            with self.subTest(order=[item['id'] for item in order]):
                result = validate_ticket_plan(self.approved, {**self.proposal, 'tickets': list(order)})
                self.assertEqual([item['id'] for item in result['tickets']], ['c', 'a', 'z'])

    def test_requires_approved_traceability_and_coverage(self):
        approved = {**self.approved, 'tasks': {'A': ['C1', 'C2'], 'B': ['C1']}}
        pairs = [('A', 'C1'), ('A', 'C2'), ('B', 'C1')]
        valid = {**self.proposal, 'tickets': [ticket('a', pairs), ticket('b', [pairs[0]])]}
        self.assertEqual(len(validate_ticket_plan(approved, valid)['tickets']), 2)
        for invalid in [pairs[:2], [pairs[0], pairs[2]], pairs + [('B', 'C2')],
                        pairs + [('X', 'C1')], pairs + [pairs[0]]]:
            with self.subTest(invalid=invalid):
                self.assert_invalid(approved, {**self.proposal, 'tickets': [ticket('a', invalid)]})
        self.assert_invalid({**approved, 'tasks': {'A': ['C1'], 'B': []}}, self.proposal)

    def test_normalization_is_deterministic(self):
        approved = {**self.approved, 'tasks': {'A': ['C1', 'C2'], 'B': ['C1']}}
        expected = {**self.proposal, 'tickets': [
            ticket('a'), ticket('b'), ticket('c', [('A', 'C2'), ('B', 'C1')], ['a', 'b']),
        ]}
        proposed = deepcopy(expected)
        proposed['tickets'][-1]['coverage'].reverse()
        proposed['tickets'][-1]['depends_on'].reverse()
        proposed['tickets'].reverse()
        snapshot = deepcopy((approved, proposed))
        result = validate_ticket_plan(approved, proposed)
        self.assertEqual(result, expected)
        self.assertEqual(validate_ticket_plan(approved, proposed), result)
        self.assertEqual(validate_ticket_plan(approved, result), result)
        result['tickets'][-1]['coverage'][0]['task_id'] = 'alterado'
        result['tickets'][0]['checks'].clear()
        self.assertEqual((approved, proposed), snapshot)
