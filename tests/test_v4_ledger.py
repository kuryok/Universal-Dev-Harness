"""Testes complementares do ledger SQLite v4, leitura O(1), migração v3→v4 e F-03."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from udh import __main__ as main_cli
from udh import lifecycle as lifecycle_cli
from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.transitions import ConflictError


HASH64_A = 'a' * 64
HASH64_B = 'b' * 64
HASH40_A = '1' * 40
HASH40_B = '2' * 40


def _operator_context_v3():
    return {
        'schema': 2,
        'db_path': '/state/udh.sqlite',
        'controller_route': 'OPERATOR_LOCAL',
        'authorization_rule_version': 1,
        'allowed_operations': ('MIGRATE_V2', 'BLOCK_PENDING', 'MIGRATE_V3', 'UNBLOCK_RUN'),
        'core_manifest_sha256': HASH64_A,
        'control_sha256': HASH64_B,
    }


def _invoke(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = fn(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class V4LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'udh.sqlite'
        self.service = CoreService(SQLiteStore(self.db))

    def tearDown(self):
        self.tmp.cleanup()

    def test_v4_init_create_and_repeatable_lifecycle_with_evidence_gate(self):
        self.assertEqual(self.service.initialize(schema_version=4), 4)
        self.assertEqual(self.service.initialize(schema_version=4), 4)
        # init v1 padrão não faz downgrade de v4
        with self.assertRaises(CoreError):
            self.service.initialize(schema_version=1)

        run = self.service.create('spec-v4', '1.0', 'req-create', ticket_id='T-01', mode='DEEP')
        self.assertEqual(run.schema_version, 4)
        self.assertEqual(run.state, 'PENDING')
        self.assertEqual(run.revision, 0)
        self.assertEqual(run.ticket_id, 'T-01')
        self.assertEqual(run.mode, 'DEEP')

        # Replay idempotente de create e conflito explícito em v4
        same = self.service.create('spec-v4', '1.0', 'req-create', ticket_id='T-01', mode='DEEP')
        self.assertEqual(same.to_dict(), run.to_dict())
        with self.assertRaises(ConflictError):
            self.service.create('spec-v4', '1.0', 'req-create', ticket_id='T-02', mode='DEEP')

        # Ciclo 1: PENDING/0 -> IN_PROGRESS/1 -> BLOCKED/2 -> PENDING/3
        s1 = self.service.start(run.run_id, 0, 'req-s1')
        self.assertEqual((s1['to_state'], s1['revision']), ('IN_PROGRESS', 1))
        self.assertEqual(self.service.start(run.run_id, 0, 'req-s1'), s1)

        b1 = self.service.block_v4(
            run.run_id, 1, 'req-b1',
            category='FUNCTIONAL_FAILURE', reason_code='AT_FAILED',
            reason='Teste de aceite falhou', next_action='Corrigir implementação e reexecutar.',
        )
        self.assertEqual((b1['from_state'], b1['to_state'], b1['revision']), ('IN_PROGRESS', 'BLOCKED', 2))

        u1 = self.service.unblock_v4(run.run_id, 2, 'req-u1', 'Retry autorizado')
        self.assertEqual((u1['from_state'], u1['to_state'], u1['revision']), ('BLOCKED', 'PENDING', 3))

        # Ciclo 2 repetível: PENDING/3 -> BLOCKED/4 -> PENDING/5 -> IN_PROGRESS/6
        b2 = self.service.block_v4(
            run.run_id, 3, 'req-b2',
            category='ADMIN_PAUSE', reason_code='OPERATOR_HOLD',
            reason='Pausa administrativa', next_action='Retirar pausa quando pronto.',
        )
        self.assertEqual((b2['from_state'], b2['to_state'], b2['revision']), ('PENDING', 'BLOCKED', 4))
        u2 = self.service.unblock_v4(run.run_id, 4, 'req-u2', 'Retomar ciclo')
        self.assertEqual(u2['revision'], 5)
        s2 = self.service.start(run.run_id, 5, 'req-s2')
        self.assertEqual((s2['to_state'], s2['revision']), ('IN_PROGRESS', 6))

        # Replay histórico de b1 preservado sem alterar estado atual
        self.assertEqual(
            self.service.block_v4(
                run.run_id, 1, 'req-b1',
                category='FUNCTIONAL_FAILURE', reason_code='AT_FAILED',
                reason='Teste de aceite falhou', next_action='Corrigir implementação e reexecutar.',
            ),
            b1,
        )
        with self.assertRaises(ConflictError):
            self.service.block_v4(
                run.run_id, 1, 'req-b1',
                category='FUNCTIONAL_FAILURE', reason_code='AT_FAILED',
                reason='Motivo adulterado', next_action='Corrigir implementação e reexecutar.',
            )

        # Evidência FAIL e REVIEW_REPORT não permitem DONE
        ev_fail = self.service.record_evidence(
            run.run_id, 'ev-fail', kind='AT_RESULT', verdict='FAIL',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='TRUSTED_EXECUTOR', summary_ref='at-fail.json',
        )
        ev_review = self.service.record_evidence(
            run.run_id, 'ev-review', kind='REVIEW_REPORT', verdict='PASS',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='OPERATOR_LOCAL', summary_ref='review.json',
        )
        for bad_ev in (ev_fail['evidence_id'], ev_review['evidence_id']):
            with self.assertRaises(ConflictError):
                self.service.complete(run.run_id, 6, 'req-done-bad', bad_ev)

        # Evidência PASS de OUTRA execução não permite DONE
        other = self.service.create('spec-v4', '1.0', 'req-other')
        ev_other = self.service.record_evidence(
            other.run_id, 'ev-other', kind='AT_RESULT', verdict='PASS',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='TRUSTED_EXECUTOR', summary_ref='other.json',
        )
        with self.assertRaises(ConflictError):
            self.service.complete(run.run_id, 6, 'req-done-other', ev_other['evidence_id'])

        # Evidência oficial PASS da própria execução permite DONE
        ev_pass = self.service.record_evidence(
            run.run_id, 'ev-pass', kind='AT_RESULT', verdict='PASS',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='TRUSTED_EXECUTOR', summary_ref='at-pass.json',
            at_version='sha256:' + HASH64_A, commit_sha=HASH40_A,
        )
        self.assertEqual(
            self.service.record_evidence(
                run.run_id, 'ev-pass', kind='AT_RESULT', verdict='PASS',
                artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
                collector_route='TRUSTED_EXECUTOR', summary_ref='at-pass.json',
                at_version='sha256:' + HASH64_A, commit_sha=HASH40_A,
            ),
            ev_pass,
        )
        done = self.service.complete(run.run_id, 6, 'req-done', ev_pass['evidence_id'])
        self.assertEqual((done['to_state'], done['revision'], done['evidence_id']), ('DONE', 7, ev_pass['evidence_id']))
        self.assertEqual(self.service.complete(run.run_id, 6, 'req-done', ev_pass['evidence_id']), done)

        # DONE é terminal
        with self.assertRaises(ConflictError):
            self.service.start(run.run_id, 7, 'req-after-done')
        with self.assertRaises(ConflictError):
            self.service.block_v4(
                run.run_id, 7, 'req-blk-after-done',
                category='ADMIN_PAUSE', reason_code='HOLD', reason='x', next_action='y',
            )

        # Restrição SQL impede COMPLETE_RUN sem evidence_id diretamente no banco
        con = sqlite3.connect(self.db)
        try:
            con.execute('PRAGMA foreign_keys = ON')
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute(
                    'INSERT INTO "transitions" (run_id, revision, operation, kind, from_state, to_state, '
                    'request_id, created_at, actor_route, evidence_id) '
                    "VALUES (?, 1, 'COMPLETE_RUN', 'RUN_COMPLETED', 'IN_PROGRESS', 'DONE', 'sql-bypass', ?, 'TRUSTED_EXECUTOR', NULL)",
                    (other.run_id, '2026-11-23T12:00:00.000000Z'),
                )
        finally:
            con.close()

    def test_checkpoint_f03_and_unknown_pendencies_fail_closed(self):
        self.service.initialize(schema_version=4)
        run = self.service.create('spec-f03', '1.0', 'req-f03', ticket_id='T-03', mode='STANDARD')
        self.service.start(run.run_id, 0, 's-1')
        ev_pass = self.service.record_evidence(
            run.run_id, 'ev-1', kind='AT_RESULT', verdict='PASS',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='TRUSTED_EXECUTOR', summary_ref='at.json',
        )

        cp1 = self.service.record_checkpoint(
            run.run_id, 1, 'cp-1', 'EXTERNAL_PUBLISH',
            base_commit_sha=HASH40_A, head_commit_sha=HASH40_B,
            worktree_snapshot_sha256=HASH64_A,
            evidence_hashes=(HASH64_B,),
            external_idempotency_keys=('ext-key-1',),
            unknown_pendencies=('push-status-unknown',),
        )
        # Replay idempotente e conflito
        self.assertEqual(
            self.service.record_checkpoint(
                run.run_id, 1, 'cp-1', 'EXTERNAL_PUBLISH',
                base_commit_sha=HASH40_A, head_commit_sha=HASH40_B,
                worktree_snapshot_sha256=HASH64_A,
                evidence_hashes=(HASH64_B,),
                external_idempotency_keys=('ext-key-1',),
                unknown_pendencies=('push-status-unknown',),
            ),
            cp1,
        )
        with self.assertRaises(ConflictError):
            self.service.record_checkpoint(run.run_id, 1, 'cp-1', 'OTHER_STEP')

        # Pendência UNKNOWN ativa impede COMPLETE_RUN
        with self.assertRaises(ConflictError):
            self.service.complete(run.run_id, 1, 'done-blocked', ev_pass['evidence_id'])

        # Mesmo após block + unblock, pendência UNKNOWN impede START_RUN até reconciliação
        self.service.block_v4(
            run.run_id, 1, 'blk-unk', category='POLICY_VIOLATION',
            reason_code='UNKNOWN_EXTERNAL_EFFECT', reason='Efeito externo desconhecido',
            next_action='Consultar destino e reconciliar checkpoint.',
        )
        self.service.unblock_v4(run.run_id, 2, 'unblk-unk', 'Pronto para reconciliar')
        with self.assertRaises(ConflictError):
            self.service.start(run.run_id, 3, 'start-blocked')

        # Novo checkpoint reconciliado (unknown_pendencies vazio) libera avanço
        self.service.record_checkpoint(
            run.run_id, 3, 'cp-2', 'RECONCILED',
            unknown_pendencies=(),
        )
        started = self.service.start(run.run_id, 3, 'start-ok')
        self.assertEqual(started['to_state'], 'IN_PROGRESS')
        done = self.service.complete(run.run_id, 4, 'done-ok', ev_pass['evidence_id'])
        self.assertEqual(done['to_state'], 'DONE')

    def test_v3_to_v4_migration_preserves_all_legacy_data_and_rejects_invalid_sources(self):
        # Recusa v1 e v2 sem encadear silenciosamente
        self.service.initialize(schema_version=1)
        with self.assertRaises(CoreError):
            self.service.migrate(4)
        self.service.migrate(2)
        with self.assertRaises(CoreError):
            self.service.migrate(4)

        # Popula v3 com PENDING/0, BLOCKED/1 e PENDING/2 (reutilizando mesmo request_id entre ops!)
        ctx = _operator_context_v3()
        self.service.migrate(3, context=ctx)
        r_pending = self.service.create('spec-a', '1.0', 'shared-key-1')
        r_blocked = self.service.create('spec-b', '2.0', 'shared-key-2')
        b_res = self.service.block(r_blocked.run_id, 0, 'shared-key-2', 'Motivo bloqueio v3', context=ctx)
        r_unblocked = self.service.create('spec-c', '3.0', 'shared-key-3')
        self.service.block(r_unblocked.run_id, 0, 'blk-c', 'Bloqueio c', context=ctx)
        u_res = self.service.unblock(r_unblocked.run_id, 1, 'shared-key-3', 'Desbloqueio c', context=ctx)

        mig = self.service.migrate(4)
        self.assertEqual(mig, {'schema_version': 4, 'migrated': True, 'migrated_runs': 3, 'migrated_transitions': 6})
        # Idempotência da migração v4
        self.assertEqual(self.service.migrate(4), {'schema_version': 4, 'migrated': False})
        # Downgrades bloqueados
        with self.assertRaises(CoreError):
            self.service.migrate(2)
        with self.assertRaises(CoreError):
            self.service.migrate(3, context=ctx)

        loaded_p = self.service.show(r_pending.run_id)
        loaded_b = self.service.show(r_blocked.run_id)
        loaded_u = self.service.show(r_unblocked.run_id)
        self.assertEqual((loaded_p.state, loaded_p.revision, loaded_p.mode), ('PENDING', 0, 'STANDARD'))
        self.assertEqual((loaded_b.state, loaded_b.revision, loaded_b.events[1].authorization_id),
                         ('BLOCKED', 1, b_res['authorization_id']))
        self.assertEqual(loaded_b.events[1].block_category, 'ADMIN_PAUSE')
        self.assertEqual(loaded_b.events[1].legacy_meta['result_schema_version'], 3)
        self.assertEqual((loaded_u.state, loaded_u.revision, loaded_u.events[2].authorization_id),
                         ('PENDING', 2, u_res['authorization_id']))
        self.assertEqual(loaded_u.events[2].legacy_meta['blocked_event_sequence'], loaded_u.events[1].sequence)

        # Após migração v4, execução que era PENDING/2 em v3 agora pode avançar livremente!
        s_next = self.service.start(r_unblocked.run_id, 2, 'start-after-v3')
        self.assertEqual((s_next['to_state'], s_next['revision']), ('IN_PROGRESS', 3))

    def test_v4_o1_reads_never_run_quick_check_or_full_scan_and_doctor_catches_corruption(self):
        self.service.initialize(schema_version=4)
        runs = [self.service.create('spec-scale', '1.0', f'req-{i}') for i in range(60)]
        target = runs[30]

        traced_sql: list[str] = []
        store = SQLiteStore(self.db)
        orig_connect = sqlite3.connect

        def tracing_connect(*args, **kwargs):
            conn = orig_connect(*args, **kwargs)
            conn.set_trace_callback(traced_sql.append)
            return conn

        try:
            sqlite3.connect = tracing_connect
            loaded = store.get(target.run_id)
            self.assertEqual(loaded.run_id, target.run_id)
            pol = CoreService(store).policy(target.run_id, 'READ_RUN')
            self.assertEqual(pol['decision'], 'ALLOW')
        finally:
            sqlite3.connect = orig_connect

        joined = '\n'.join(traced_sql).lower()
        self.assertNotIn('quick_check', joined)
        self.assertNotIn('foreign_key_check', joined)
        self.assertNotIn('select id from', joined)

        # Verifica planos de consulta com índice B-tree
        con = sqlite3.connect(self.db)
        try:
            for query in (
                'SELECT * FROM "transitions" WHERE run_id = ? ORDER BY revision',
                'SELECT * FROM "evidence_refs" WHERE run_id = ? ORDER BY created_at, evidence_id',
                'SELECT * FROM "checkpoints" WHERE run_id = ? ORDER BY run_revision, created_at, checkpoint_id',
            ):
                detail = con.execute('EXPLAIN QUERY PLAN ' + query, (target.run_id,)).fetchone()[3]
                self.assertIn('USING INDEX', detail)
        finally:
            con.close()

        # Corrupção em OUTRA execução não quebra show O(1) do target, mas doctor detecta!
        corrupt_run = runs[0]
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE "runs" SET state = ? WHERE id = ?', ('DONE', corrupt_run.run_id))
            con.commit()
        finally:
            con.close()

        # show do target saudável ainda funciona em O(1)
        self.assertEqual(self.service.show(target.run_id).run_id, target.run_id)
        # show da execução corrompida recusa
        with self.assertRaises(CoreError):
            self.service.show(corrupt_run.run_id)
        # deep_check (doctor) detecta a corrupção global
        with self.assertRaises(CoreError):
            store.deep_check()

    def test_concurrent_v4_transitions_discriminate_equal_and_distinct_requests(self):
        self.service.initialize(schema_version=4)
        run = self.service.create('spec-conc', '1.0', 'req-conc')

        def start_call(req_id):
            svc = CoreService(SQLiteStore(self.db))
            try:
                return ('OK', svc.start(run.run_id, 0, req_id))
            except ConflictError:
                return ('CONFLICT', None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            res_eq = list(pool.map(start_call, ('same-start', 'same-start')))
        self.assertEqual([r[0] for r in res_eq], ['OK', 'OK'])
        self.assertEqual(res_eq[0][1], res_eq[1][1])

        def block_call(req_id):
            svc = CoreService(SQLiteStore(self.db))
            try:
                return ('OK', svc.block_v4(
                    run.run_id, 1, req_id,
                    category='ADMIN_PAUSE', reason_code='RACE',
                    reason=f'Motivo {req_id}', next_action='Resolver',
                ))
            except ConflictError:
                return ('CONFLICT', None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            res_diff = list(pool.map(block_call, ('blk-a', 'blk-b')))
        statuses = sorted(r[0] for r in res_diff)
        self.assertEqual(statuses, ['CONFLICT', 'OK'])

    def test_cli_surfaces_and_common_cli_v1_v3_boundaries(self):
        # CLI comum recusa block/unblock/migrate (preserva U8 dos AT congelados)
        for forbidden in ('block', 'unblock', 'migrate'):
            code, out, err = _invoke(main_cli.main, [forbidden])
            self.assertEqual(code, 2)
            self.assertEqual(out, '')

        # CLI comum init v4 + create com ticket/mode
        code, out, err = _invoke(main_cli.main, ['init', '--db', str(self.db), '--schema-version', '4'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'schema_version': 4})

        code, out, err = _invoke(main_cli.main, [
            'create', '--db', str(self.db), '--spec-id', 's1', '--spec-version', 'v1',
            '--request-id', 'cli-1', '--ticket-id', 'T-10', '--mode', 'FAST',
        ])
        self.assertEqual((code, err), (0, ''))
        run = json.loads(out)
        self.assertEqual((run['schema_version'], run['ticket_id'], run['mode']), (4, 'T-10', 'FAST'))

        # CLI lifecycle executa start -> record-evidence -> checkpoint -> complete
        code, out, err = _invoke(lifecycle_cli.main, [
            'start', '--db', str(self.db), '--run-id', run['run_id'],
            '--expected-revision', '0', '--request-id', 'cli-start',
        ])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['to_state'], 'IN_PROGRESS')

        # Conflito de revisão retorna exit 4
        code, out, err = _invoke(lifecycle_cli.main, [
            'start', '--db', str(self.db), '--run-id', run['run_id'],
            '--expected-revision', '0', '--request-id', 'cli-start-other',
        ])
        self.assertEqual((code, out), (4, ''))
        self.assertIn('Conflito', err)

        code, out, err = _invoke(lifecycle_cli.main, [
            'record-evidence', '--db', str(self.db), '--run-id', run['run_id'],
            '--request-id', 'cli-ev', '--kind', 'AT_RESULT', '--verdict', 'PASS',
            '--artifact-digest', HASH64_A, '--evidence-sha256', HASH64_B,
            '--collector-route', 'TRUSTED_EXECUTOR', '--summary-ref', 'sum.json',
        ])
        self.assertEqual((code, err), (0, ''))
        ev = json.loads(out)

        code, out, err = _invoke(lifecycle_cli.main, [
            'checkpoint', '--db', str(self.db), '--run-id', run['run_id'],
            '--expected-revision', '1', '--request-id', 'cli-cp',
            '--last-step', 'VERIFY_AT', '--evidence-hashes-json', f'["{HASH64_B}"]',
        ])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['last_confirmed_step'], 'VERIFY_AT')

        code, out, err = _invoke(lifecycle_cli.main, [
            'complete', '--db', str(self.db), '--run-id', run['run_id'],
            '--expected-revision', '1', '--request-id', 'cli-done',
            '--evidence-id', ev['evidence_id'],
        ])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['to_state'], 'DONE')

    def test_r2_hardening_checkpoint_json_types_nan_and_initial_transition_match(self):
        self.service.initialize(schema_version=4)
        run = self.service.create('spec-r2', '1.0', 'req-r2')

        # CLI rejeita NaN / Infinity sem traceback (exit 1)
        for bad_arg in ('[NaN]', '[Infinity]', '[-Infinity]'):
            code, out, err = _invoke(lifecycle_cli.main, [
                'checkpoint', '--db', str(self.db), '--run-id', run.run_id,
                '--expected-revision', '0', '--request-id', 'cp-nan',
                '--last-step', 'STEP', '--unknown-pendencies-json', bad_arg,
            ])
            self.assertEqual(code, 1)
            self.assertEqual(out, '')
            self.assertNotIn('Traceback', err)

        # Service rejeita str, dict, int e itens inválidos em listas de checkpoint
        for bad_kw in (
            {'unknown_pendencies': 'pending'},
            {'unknown_pendencies': {'a': 1}},
            {'unknown_pendencies': 123},
            {'unknown_pendencies': ('',)},
            {'unknown_pendencies': ({},)},
            {'unknown_pendencies': ({'x': float('nan')},)},
            {'evidence_hashes': ('not-a-sha256',)},
            {'external_idempotency_keys': ('   ',)},
        ):
            with self.subTest(bad_kw=bad_kw), self.assertRaises(CoreError):
                self.service.record_checkpoint(run.run_id, 0, 'cp-bad', 'STEP', **bad_kw)

        # Checkpoint válido com objeto JSON em unknown_pendencies funciona
        cp_ok = self.service.record_checkpoint(
            run.run_id, 0, 'cp-obj', 'STEP',
            evidence_hashes=(HASH64_A,),
            external_idempotency_keys=('ext-1',),
            unknown_pendencies=({'target': 'github', 'key': 'k1'},),
        )
        self.assertEqual(cp_ok['unknown_pendencies'], [{'target': 'github', 'key': 'k1'}])

        # Adulteração de NaN no SQLite é detectada por show e deep_check/doctor
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE "checkpoints" SET unknown_pendencies_json = ? WHERE checkpoint_id = ?',
                        ('[NaN]', cp_ok['checkpoint_id']))
            con.commit()
        finally:
            con.close()
        with self.assertRaises(CoreError):
            self.service.show(run.run_id)
        with self.assertRaises(CoreError):
            SQLiteStore(self.db).deep_check()

        # Restaura JSON válido e adultera transitions[0].request_id -> rejeitado pelo leitor e deep_check
        con = sqlite3.connect(self.db)
        try:
            con.execute('UPDATE "checkpoints" SET unknown_pendencies_json = ? WHERE checkpoint_id = ?',
                        ('[]', cp_ok['checkpoint_id']))
            con.execute('UPDATE "transitions" SET request_id = ? WHERE run_id = ? AND revision = 0',
                        ('tampered-req', run.run_id))
            con.commit()
        finally:
            con.close()
        with self.assertRaises(CoreError):
            self.service.show(run.run_id)
        with self.assertRaises(CoreError):
            SQLiteStore(self.db).deep_check()


if __name__ == '__main__':
    unittest.main()
