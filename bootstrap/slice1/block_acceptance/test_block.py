"""Aceite da transição protegida, sem importar o Core candidato."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from test_migration import OperatorFixture


class BlockAcceptance(OperatorFixture):
    def ready(self):
        run = self.seed()[0]
        self.payload(self.operator('migrate', '--to', '2'))
        return run['run_id']

    def block(self, run_id, request='block-1', reason=' Pausa explícita ', revision='0'):
        return self.operator('block', '--run-id', run_id, '--expected-revision', revision,
                             '--request-id', request, '--reason', reason)

    def assert_blocked(self, run_id, result):
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['run_id'], run_id)
        self.assertEqual((result['from_state'], result['to_state'], result['revision']),
                         ('PENDING', 'BLOCKED', 1))
        self.assertEqual(str(UUID(result['authorization_id'])), result['authorization_id'])
        self.assertTrue(result['created_at'].endswith('Z'))
        runs, events, requests = self.rows('runs'), self.rows('events'), self.rows('block_requests')
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]['state'], runs[0]['revision']), ('BLOCKED', 1))
        self.assertEqual(len(events), 2)
        self.assertEqual(len(requests), 1)
        blocked = next(row for row in events if row['kind'] == 'RUN_BLOCKED')
        created = next(row for row in events if row['kind'] == 'RUN_CREATED')
        self.assertGreater(blocked['sequence'], created['sequence'])
        self.assertEqual(blocked['run_id'], run_id)
        self.assertEqual(blocked['sequence'], result['event_sequence'])
        self.assertEqual(blocked['created_at'], result['created_at'])
        request = requests[0]
        for key in ('request_id', 'authorization_id'):
            self.assertEqual(request[key], result[key])
        self.assertEqual(request['run_id'], run_id)
        self.assertEqual(request['expected_revision'], 0)
        self.assertEqual(request['event_sequence'], blocked['sequence'])
        self.assertEqual(request['controller_route'], 'OPERATOR_LOCAL')
        self.assertEqual(request['authorization_rule_version'], 1)
        for key in ('core_manifest_sha256', 'control_sha256'):
            self.assertRegex(request[key], r'^[0-9a-f]{64}$')

    def test_b1_persisted_block_and_v2_reader(self):
        run_id = self.ready()
        result = self.payload(self.block(run_id))
        self.assert_blocked(run_id, result)
        self.assertEqual(self.rows('block_requests')[0]['reason'], ' Pausa explícita ')
        shown = self.payload(self.core('show', '--run-id', run_id))
        self.assertEqual((shown['schema_version'], shown['revision'], shown['state']), (2, 1, 'BLOCKED'))
        event = next(row for row in shown['events'] if row['kind'] == 'RUN_BLOCKED')
        for key in ('request_id', 'authorization_id', 'revision', 'from_state', 'to_state'):
            self.assertEqual(event[key], result[key])
        self.assertEqual(event['reason'], ' Pausa explícita ')
        before = self.snapshot()
        self.assertEqual(self.core('init').returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_b3_exact_replay_and_payload_conflicts(self):
        run_id = self.ready()
        result = self.payload(self.block(run_id))
        before = self.snapshot()
        self.assertEqual(self.payload(self.block(run_id)), result)
        for request, reason, revision in (('block-1', 'outra', '0'),
                                           ('block-2', ' Pausa explícita ', '0'),
                                           ('block-1', ' Pausa explícita ', '1')):
            response = self.block(run_id, request, reason, revision)
            self.assertEqual(response.returncode, 4, response.stderr)
            self.assertEqual(response.stdout, '')
            self.assertTrue(response.stderr)
            self.assertEqual(self.snapshot(), before)

    def test_b3_equal_concurrent_requests(self):
        run_id = self.ready()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.block(run_id), range(2)))
        payloads = [self.payload(result) for result in results]
        self.assertEqual(payloads[0], payloads[1])
        self.assert_blocked(run_id, payloads[0])

    def test_b3_distinct_concurrent_requests(self):
        run_id = self.ready()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda request: self.block(run_id, request), ('a', 'b')))
        self.assertEqual(sorted(result.returncode for result in results), [0, 4])
        winner = self.payload(next(result for result in results if result.returncode == 0))
        loser = next(result for result in results if result.returncode == 4)
        self.assertEqual(loser.stdout, '')
        self.assertTrue(loser.stderr)
        self.assert_blocked(run_id, winner)

    def test_b5_common_policy_and_unsupported_targets_no_effect(self):
        run_id = self.ready()
        before = self.snapshot()
        for target in ('IN_PROGRESS', 'DONE', 'ACCEPTED', 'DELIVERED'):
            response = self.operator('block', '--run-id', run_id, '--expected-revision', '0',
                                     '--request-id', 'forbidden', '--reason', 'teste', '--target', target)
            self.assertEqual(response.returncode, 2, response.stderr)
            self.assertEqual(response.stdout, '')
            self.assertEqual(self.snapshot(), before)
        response = self.core('policy', '--run-id', run_id, '--action', 'TRANSITION_RUN')
        self.assertEqual(response.returncode, 3)
        self.assertEqual(__import__('json').loads(response.stdout)['decision'], 'DENY')
        self.assertEqual(self.snapshot(), before)

    def test_b2_adversary_isolation_and_claims(self):
        self.seed()
        before = self.snapshot()
        result = self.payload(self.operator('--probe-adversary'))
        self.assertIs(result['runtime_removed'], True)
        for key in ('state_mount_absent', 'control_mount_absent', 'runtime_socket_absent',
                    'inherited_admin_fds_absent', 'authority_claims_rejected',
                    'snapshot_substitution_rejected', 'symlink_rejected'):
            self.assertIs(result['checks'][key], True, key)
        self.assertEqual(self.snapshot(), before)

    def test_b4_instrumented_interruption_and_replay(self):
        run_id = self.ready()
        def arguments(identifier):
            return ('block', '--run-id', identifier, '--expected-revision', '0',
                    '--request-id', 'block-1', '--reason', ' Pausa explícita ')
        checkpoints = self.discover(*arguments(run_id))
        for point in checkpoints:
            with self.subTest(checkpoint=point):
                self.setUp()
                run_id = self.ready()
                before = self.snapshot()
                self.interrupt(point, *arguments(run_id))
                if point.startswith('BEFORE_WRITE:'):
                    self.assertEqual(self.snapshot(), before)
                else:
                    persisted = self.snapshot()
                    self.assert_blocked(run_id, self.payload(self.block(run_id)))
                    self.assertEqual(self.snapshot(), persisted)
                result = self.payload(self.block(run_id))
                self.assert_blocked(run_id, result)
                persisted = self.snapshot()
                self.assertEqual(self.payload(self.block(run_id)), result)
                self.assertEqual(self.snapshot(), persisted)
