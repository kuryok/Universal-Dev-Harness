import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_trust_store_and_executor import _StubRuntimeManager
from udh.adapters.opencode import DEFAULT_OPENCODE_LIMITS, OpenCodeAdapter
from udh.adapters.pi import PiAdapter
from udh.executor import TrustedExecutor
from udh.gateway import DEFAULT_STUB_MODEL, GatewayConfig, InferenceGateway
from udh.handoff import HandoffError, commit_handoff_snapshot, init_core_worktree
from udh.policy import policy_v2_sha256
from udh.runtime import ContainerResult, RuntimeManager
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.ticket_handoff import (
    build_managed_handoff_packet,
    render_managed_handoff_prompt,
    verify_managed_handoff_packet,
)
from udh.transitions import ConflictError
from udh.trust_store import TrustStore


def _approved_two_ticket_spec(*, spec_version='1', spec_sha256='1' * 64):
    return {
        'change_id': 'change-5-handoff',
        'spec_version': spec_version,
        'spec_sha256': spec_sha256,
        'tasks': {
            'T1': ['C1-add'],
            'T2': ['C2-add-kept', 'C2-mul'],
        },
        'task_details': {
            'T1': {
                'mode': 'direct',
                'depends_on': [],
                'objective': 'Implementar operação add em calc.py',
                'checks': ['calc.py add 3 4 imprime 7'],
            },
            'T2': {
                'mode': 'direct',
                'depends_on': ['T1'],
                'objective': 'Adicionar operação mul em calc.py preservando add',
                'checks': ['calc.py add 3 4 imprime 7', 'calc.py mul 3 4 imprime 12'],
            },
        },
    }


def _ref(spec):
    return {k: spec[k] for k in ('change_id', 'spec_version', 'spec_sha256')}


def _seal_suite(store: TrustStore, root: Path, name: str, content: str) -> tuple[dict, str]:
    src = root / f'at-src-{name}'
    src.mkdir(parents=True, exist_ok=True)
    (src / f'test_{name}.py').write_text(content, encoding='utf-8')
    sealed = store.seal_at_suite(src)
    at_dir, _ = store.verify_sealed_at(sealed['at_version'])
    manifest_sha = hashlib.sha256((at_dir.parent / 'manifest.json').read_bytes()).hexdigest()
    return sealed, manifest_sha


class ManagedTicketHandoffUnitTests(unittest.TestCase):
    """Testes determinísticos rápidos para C5-T4-01 e C5-T4-02."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='udh-t4-unit-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / 'core.db'
        self.store = TrustStore(self.root / 'trust-store')
        self.ws = self.root / 'workspace'
        self.ws.mkdir()
        self.git_dir = self.root / 'core-git-dir'

        self.core = CoreService(SQLiteStore(self.db_path))
        self.core.initialize(schema_version=4)
        for ver in (5, 6, 7, 8, 9):
            self.core.migrate(ver)

        self.spec = _approved_two_ticket_spec()
        self.ref = _ref(self.spec)
        self.core.approve_ticket_input(self.spec)
        self.plan = self.core.materialize_ticket_plan(self.ref, expected_revision=1)
        self.plan_sha = self.plan['plan_sha256']
        self.core.reconcile_ticket_plan(self.ref, expected_revision=1, plan_sha256=self.plan_sha)

        self.at1, self.at1_msha = _seal_suite(
            self.store, self.root, 't1_calc',
            'import unittest\n'
            'class TestCalcT1(unittest.TestCase):\n'
            '    def test_add(self):\n'
            '        self.assertEqual(3 + 4, 7)\n',
        )
        self.at2, self.at2_msha = _seal_suite(
            self.store, self.root, 't2_calc',
            'import unittest\n'
            'class TestCalcT2(unittest.TestCase):\n'
            '    def test_add_kept(self):\n'
            '        self.assertEqual(3 + 4, 7)\n'
            '    def test_mul(self):\n'
            '        self.assertEqual(3 * 4, 12)\n',
        )

        (self.ws / 'calc.py').write_text('# inicial\n', encoding='utf-8')
        self.base_git = init_core_worktree(self.git_dir, self.ws, initial_message='udh: t4 base')

        self.run1 = self.core.create_managed_ticket_run(
            self.ref, approval_revision=1, plan_sha256=self.plan_sha,
            ticket_id='T1', request_id='req-create-t1', mode='STANDARD',
        )
        self.run2 = self.core.create_managed_ticket_run(
            self.ref, approval_revision=1, plan_sha256=self.plan_sha,
            ticket_id='T2', request_id='req-create-t2', mode='STANDARD',
        )
        self.core.register_managed_ticket_contract(
            self.run1.run_id, expected_revision=0, trust_store=self.store,
            contract={
                'approval_ref': 'app-t1',
                'at_version': self.at1['at_version'],
                'at_manifest_sha256': self.at1_msha,
                'coverage': [{'task_id': 'T1', 'criterion_id': 'C1-add', 'test_ids': ['test_t1_calc.TestCalcT1.test_add']}],
                'context_ids': [],
                'allowed_context_ids': ['ctx-spec-calc'],
                'unresolved_gaps': [],
            },
        )
        self.core.register_managed_ticket_contract(
            self.run2.run_id, expected_revision=0, trust_store=self.store,
            contract={
                'approval_ref': 'app-t2',
                'at_version': self.at2['at_version'],
                'at_manifest_sha256': self.at2_msha,
                'coverage': [
                    {'task_id': 'T2', 'criterion_id': 'C2-add-kept', 'test_ids': ['test_t2_calc.TestCalcT2.test_add_kept']},
                    {'task_id': 'T2', 'criterion_id': 'C2-mul', 'test_ids': ['test_t2_calc.TestCalcT2.test_mul']},
                ],
                'context_ids': ['ctx-spec-calc'],
                'allowed_context_ids': ['ctx-spec-calc', 'ctx-extra-unused'],
                'unresolved_gaps': [],
            },
        )
        self.ctx_refs = {
            'ctx-spec-calc': {
                'kind': 'spec-summary',
                'ref_sha256': hashlib.sha256(b'calc-spec-v1').hexdigest(),
                'summary': 'calc.py aceita add e mul com dois inteiros em stdout.',
            },
        }

    def _finish_t1(self, *, accept=True, record_ckpt=True, unknown_pendencies=()):
        self.core.start(self.run1.run_id, 0, 'req-start-t1', trust_store=self.store)
        (self.ws / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        snap = commit_handoff_snapshot(self.git_dir, self.ws, message='udh: t1 done')
        art = self.store.promote_artifact(self.ws, commit_sha=snap['head_commit_sha'])
        def _stub_pass(kwargs):
            nonce = json.loads(kwargs['stdin_bytes'].decode('utf-8'))['nonce']
            tests = ['test_t1_calc.TestCalcT1.test_add']
            payload = {
                'udhf04': 1, 'nonce': nonce, 'ok': True, 'tampering_ok': True, 'tampering_reason': None,
                'tests_run': len(tests), 'failures': 0, 'errors': 0, 'skipped': 0,
                'expected_tests': tests, 'passed_tests': tests,
            }
            return ContainerResult(
                container_name='stub-f04',
                profile='artifact',
                image_ref=kwargs['image_ref'],
                returncode=0,
                stdout=json.dumps(payload) + '\n',
                stderr='',
                outcome=None,
                elapsed_seconds=0.01,
                inspection={},
                cleanup_verified=True,
            )

        executor = TrustedExecutor(
            core_service=self.core,
            trust_store=self.store,
            runtime_manager=_StubRuntimeManager(_stub_pass),
        )
        ev = executor.verify_run(
            self.run1.run_id, request_id='req-verify-t1',
            artifact_digest=art['artifact_digest'], at_version=self.at1['at_version'],
            commit_sha=snap['head_commit_sha'],
        )
        self.assertEqual(ev['report']['verdict'], 'PASS')
        executor.complete_verified_run(
            self.run1.run_id, expected_revision=1, request_id='req-complete-t1',
            evidence_id=ev['evidence']['evidence_id'],
            artifact_digest=art['artifact_digest'], at_version=self.at1['at_version'],
        )
        if record_ckpt:
            self.core.record_checkpoint(
                self.run1.run_id, expected_revision=2, request_id='req-ckpt-t1',
                last_confirmed_step='t1_verified_and_completed',
                base_commit_sha=self.base_git['base_commit_sha'],
                head_commit_sha=snap['head_commit_sha'],
                worktree_snapshot_sha256=snap['worktree_snapshot_sha256'],
                evidence_hashes=(ev['evidence']['evidence_sha256'],),
                external_idempotency_keys=('idem-t1',),
                unknown_pendencies=unknown_pendencies,
            )
        review = None
        acceptance = None
        if accept:
            review = self.core.record_managed_ticket_review(
                self.run1.run_id, expected_revision=2, request_id='req-review-t1',
                at_evidence_id=ev['evidence']['evidence_id'],
                independent_context=True, review_context_ref='rev-ctx-1',
                confirmation_ref='human-ok-1', findings=[], trust_store=self.store,
            )
            acceptance = self.core.accept_managed_ticket(
                self.run1.run_id, expected_revision=2, request_id='req-accept-t1',
                at_evidence_id=ev['evidence']['evidence_id'],
                review_evidence_id=review['evidence_id'], trust_store=self.store,
            )
        return {'snap': snap, 'art': art, 'at': ev, 'review': review, 'acceptance': acceptance}

    def test_blocks_handoff_until_predecessor_officially_accepted(self):
        # 1. T2 bloqueado enquanto T1 ainda PENDING
        with self.assertRaises(ConflictError):
            self.core.start(self.run2.run_id, 0, 'req-start-t2-early', trust_store=self.store)
        with self.assertRaises(HandoffError) as err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read', 'write'), context_refs=self.ctx_refs,
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(err.exception.reason_code, 'HANDOFF_PRECONDITION_FAILED')

        # 2. T1 DONE com AT PASS mas sem F-02 + aceite oficial -> T2 continua bloqueado
        self._finish_t1(accept=False)
        self.assertEqual(self.core.show(self.run1.run_id).state, 'DONE')
        with self.assertRaises(ConflictError):
            self.core.start(self.run2.run_id, 0, 'req-start-t2-done-only', trust_store=self.store)

    def test_builds_verified_managed_handoff_from_persisted_state(self):
        t1 = self._finish_t1(accept=True)
        # Reabrir SQLite para provar recuperação pura a partir do estado persistido
        self.core = CoreService(SQLiteStore(self.db_path))

        started = self.core.start(self.run2.run_id, 0, 'req-start-t2', trust_store=self.store)
        self.assertEqual(started['to_state'], 'IN_PROGRESS')

        # Ticket raiz sem predecessora não admite handoff entre tickets
        with self.assertRaises(HandoffError) as root_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run1.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read',), conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(root_err.exception.reason_code, 'HANDOFF_PRECONDITION_FAILED')

        packet = build_managed_handoff_packet(
            core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
            git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
            allowed_tools=('read', 'write', 'edit'), context_refs=self.ctx_refs,
            conformant_adapters=('pi', 'opencode'),
        )
        self.assertEqual(verify_managed_handoff_packet(packet), packet)
        self.assertEqual(packet['ticket_id'], 'T2')
        self.assertEqual(packet['plan_identity']['plan_sha256'], self.plan_sha)
        self.assertEqual(packet['task_contract']['objective'], 'Adicionar operação mul em calc.py preservando add')
        self.assertEqual(packet['authorized_context']['context_ids'], ['ctx-spec-calc'])
        self.assertEqual(packet['authorized_context']['items'][0]['id'], 'ctx-spec-calc')
        self.assertEqual(len(packet['predecessors']), 1)
        pred = packet['predecessors'][0]
        self.assertEqual(pred['ticket_id'], 'T1')
        self.assertEqual(pred['commit_sha'], t1['snap']['head_commit_sha'])
        self.assertEqual(pred['acceptance_snapshot_sha256'], t1['acceptance']['snapshot_sha256'])
        self.assertEqual(pred['at_evidence_id'], t1['at']['evidence']['evidence_id'])
        self.assertEqual(pred['review_evidence_id'], t1['review']['evidence_id'])
        self.assertEqual(packet['git']['head_commit_sha'], t1['snap']['head_commit_sha'])
        self.assertEqual(packet['git']['changed_files'], ['calc.py'])

        prompt = render_managed_handoff_prompt(packet)
        self.assertIn(packet['packet_sha256'], prompt)
        self.assertIn('ctx-spec-calc', prompt)

    def test_rejects_unauthorized_context_and_conversational_keys(self):
        self._finish_t1(accept=True)
        self.core.start(self.run2.run_id, 0, 'req-start-t2', trust_store=self.store)

        bad_contexts = [
            None,
            {},
            {**self.ctx_refs, 'ctx-extra-unused': self.ctx_refs['ctx-spec-calc']},
            {'ctx-unknown': self.ctx_refs['ctx-spec-calc']},
            {'ctx-spec-calc': {'kind': 'spec-summary', 'ref_sha256': 'bad', 'summary': 'ok'}},
            {'ctx-spec-calc': {'kind': 'spec-summary', 'ref_sha256': 'a' * 64, 'summary': 'x' * 4097}},
        ]
        for bad_ctx in bad_contexts:
            with self.subTest(bad_ctx=type(bad_ctx).__name__), self.assertRaises(HandoffError) as err:
                build_managed_handoff_packet(
                    core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                    git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                    allowed_tools=('read', 'write'), context_refs=bad_ctx,
                    conformant_adapters=('pi', 'opencode'),
                )
            self.assertEqual(err.exception.reason_code, 'HANDOFF_CONTEXT_DENIED')

        # Chave conversacional proibida dentro de context_refs
        with self.assertRaises(HandoffError) as conv_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read', 'write'),
                context_refs={'ctx-spec-calc': {**self.ctx_refs['ctx-spec-calc'], 'transcript': 'leaked'}},
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(conv_err.exception.reason_code, 'HANDOFF_INTEGRITY_VIOLATION')

        # Mesmo adaptador ou adaptador de destino não conforme
        with self.assertRaises(HandoffError) as same_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='pi',
                allowed_tools=('read',), context_refs=self.ctx_refs,
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(same_err.exception.reason_code, 'HANDOFF_UNAUTHORIZED')

        with self.assertRaises(HandoffError) as nonconf_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read',), context_refs=self.ctx_refs,
                conformant_adapters=('pi',),
            )
        self.assertEqual(nonconf_err.exception.reason_code, 'REQUESTED_ADAPTER_NOT_CONFORMANT')

        # Pacote adulterado após selagem rejeitado em verify e render
        valid = build_managed_handoff_packet(
            core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
            git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
            allowed_tools=('read',), context_refs=self.ctx_refs,
            conformant_adapters=('pi', 'opencode'),
        )
        tampered = copy.deepcopy(valid)
        tampered['task_contract']['objective'] = 'Adulterado'
        with self.assertRaises(HandoffError) as tamp_err:
            render_managed_handoff_prompt(tampered)
        self.assertEqual(tamp_err.exception.reason_code, 'HANDOFF_INTEGRITY_VIOLATION')

    def test_rejects_tampered_evidence_checkpoint_worktree_or_stale_plan(self):
        # 1. Predecessora sem checkpoint F-03 bloqueia handoff mesmo após aceite
        t1 = self._finish_t1(accept=True, record_ckpt=False)
        self.core.start(self.run2.run_id, 0, 'req-start-t2', trust_store=self.store)
        with self.assertRaises(HandoffError) as no_ckpt:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read',), context_refs=self.ctx_refs,
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(no_ckpt.exception.reason_code, 'HANDOFF_PRECONDITION_FAILED')

        # 2. Checkpoint com UNKNOWN ativa bloqueia
        self.core.record_checkpoint(
            self.run1.run_id, expected_revision=2, request_id='req-ckpt-t1-unk',
            last_confirmed_step='t1_unknown',
            base_commit_sha=self.base_git['base_commit_sha'],
            head_commit_sha=t1['snap']['head_commit_sha'],
            worktree_snapshot_sha256=t1['snap']['worktree_snapshot_sha256'],
            evidence_hashes=(t1['at']['evidence']['evidence_sha256'],),
            unknown_pendencies=({'kind': 'unconfirmed_side_effect'},),
        )
        with self.assertRaises(HandoffError) as unk_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read',), context_refs=self.ctx_refs,
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(unk_err.exception.reason_code, 'UNKNOWN_PENDENCY_ACTIVE')

        # Reconcilia checkpoint limpo para os próximos controles negativos
        self.core.record_checkpoint(
            self.run1.run_id, expected_revision=2, request_id='req-ckpt-t1-clean',
            last_confirmed_step='t1_reconciled',
            base_commit_sha=self.base_git['base_commit_sha'],
            head_commit_sha=t1['snap']['head_commit_sha'],
            worktree_snapshot_sha256=t1['snap']['worktree_snapshot_sha256'],
            evidence_hashes=(t1['at']['evidence']['evidence_sha256'],),
            unknown_pendencies=(),
        )

        # 3. Worktree sujo / .gitignore
        smuggled = self.ws / 'smuggled.py'
        gitignore = self.ws / '.gitignore'
        gitignore.write_text('smuggled.py\n', encoding='utf-8')
        smuggled.write_text('print(1)\n', encoding='utf-8')
        try:
            with self.assertRaises(HandoffError) as dirty_err:
                build_managed_handoff_packet(
                    core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                    git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                    allowed_tools=('read',), context_refs=self.ctx_refs,
                    conformant_adapters=('pi', 'opencode'),
                )
            self.assertEqual(dirty_err.exception.reason_code, 'HANDOFF_INTEGRITY_VIOLATION')
        finally:
            smuggled.unlink(missing_ok=True)
            gitignore.unlink(missing_ok=True)

        # 4. Commit não registrado no checkpoint (HEAD != checkpoint.head_commit_sha)
        extra = self.ws / 'extra.txt'
        extra.write_text('drift\n', encoding='utf-8')
        commit_handoff_snapshot(self.git_dir, self.ws, message='udh: unrecorded drift')
        try:
            with self.assertRaises(HandoffError) as drift_err:
                build_managed_handoff_packet(
                    core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                    git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                    allowed_tools=('read',), context_refs=self.ctx_refs,
                    conformant_adapters=('pi', 'opencode'),
                )
            self.assertEqual(drift_err.exception.reason_code, 'HANDOFF_INTEGRITY_VIOLATION')
        finally:
            subprocess.run(
                ['git', '-c', 'core.hooksPath=/dev/null', '--git-dir', str(self.git_dir), '--work-tree', str(self.ws),
                 'reset', '--hard', t1['snap']['head_commit_sha']],
                capture_output=True, text=True, check=True, timeout=15,
            )
            extra.unlink(missing_ok=True)

        # 5. Evidência F-04 adulterada fisicamente na TrustStore
        ev_file = self.store.root / t1['at']['evidence']['summary_ref']
        orig = ev_file.read_bytes()
        try:
            os.chmod(ev_file, 0o644)
            ev_file.write_bytes(b'{"tampered":true}\n')
            with self.assertRaises(HandoffError) as ev_err:
                build_managed_handoff_packet(
                    core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                    git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                    allowed_tools=('read',), context_refs=self.ctx_refs,
                    conformant_adapters=('pi', 'opencode'),
                )
            self.assertEqual(ev_err.exception.reason_code, 'HANDOFF_EVIDENCE_MISMATCH')
        finally:
            os.chmod(ev_file, 0o644)
            ev_file.write_bytes(orig)
            os.chmod(ev_file, 0o444)

        # 6. Troca de geração aprovada torna o plano atual obsoleto
        new_spec = _approved_two_ticket_spec(spec_version='2', spec_sha256='2' * 64)
        self.core.approve_ticket_input(new_spec, expected=(self.ref, 1))
        with self.assertRaises(HandoffError) as stale_err:
            build_managed_handoff_packet(
                core_service=self.core, trust_store=self.store, run_id=self.run2.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                allowed_tools=('read',), context_refs=self.ctx_refs,
                conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(stale_err.exception.reason_code, 'HANDOFF_PRECONDITION_FAILED')


class ManagedTicketHandoffE2ETests(unittest.TestCase):
    """Cenário offline real C5-T4-03: Pi 1.2.0 -> OpenCode 1.18.35 + Executor F-04 artifact-only."""

    def test_offline_pi_to_opencode_dependent_tickets_e2e(self):
        manager = RuntimeManager()
        pol_sha = policy_v2_sha256()
        sentinel = 'PI_SESSION_TRANSCRIPT_SENTINEL_NEVER_LEAK'

        with tempfile.TemporaryDirectory(prefix='udh-t4-e2e-') as tmp:
            root = Path(tmp)
            db_path = root / 'core.db'
            store = TrustStore(root / 'trust-store')
            ws = root / 'workspace'
            ws.mkdir()
            git_dir = root / 'core-git-dir'

            core = CoreService(SQLiteStore(db_path))
            core.initialize(schema_version=4)
            for ver in (5, 6, 7, 8, 9):
                core.migrate(ver)

            doc_conf = core.doctor(store_dir=store.root, run_conformance=True)
            self.assertEqual(doc_conf['overall_status'], 'READY')
            rt_check = next(c for c in doc_conf['checks'] if c['id'] == 'runtime_components')
            self.assertEqual(rt_check['details']['conformant_adapters'], ['pi', 'opencode'])

            spec = _approved_two_ticket_spec()
            ref = _ref(spec)
            core.approve_ticket_input(spec)
            plan = core.materialize_ticket_plan(ref, expected_revision=1)
            plan_sha = plan['plan_sha256']
            core.reconcile_ticket_plan(ref, expected_revision=1, plan_sha256=plan_sha)

            at1, at1_msha = _seal_suite(
                store, root, 't1_calc',
                'import subprocess, sys, unittest\n'
                'class TestCalcT1(unittest.TestCase):\n'
                '    def test_add(self):\n'
                '        p = subprocess.run([sys.executable, "/artifact/calc.py", "add", "3", "4"], capture_output=True, text=True, check=True)\n'
                '        self.assertEqual(p.stdout.strip(), "7")\n',
            )
            at2, at2_msha = _seal_suite(
                store, root, 't2_calc',
                'import subprocess, sys, unittest\n'
                'class TestCalcT2(unittest.TestCase):\n'
                '    def test_add_kept(self):\n'
                '        p = subprocess.run([sys.executable, "/artifact/calc.py", "add", "3", "4"], capture_output=True, text=True, check=True)\n'
                '        self.assertEqual(p.stdout.strip(), "7")\n'
                '    def test_mul(self):\n'
                '        p = subprocess.run([sys.executable, "/artifact/calc.py", "mul", "3", "4"], capture_output=True, text=True, check=True)\n'
                '        self.assertEqual(p.stdout.strip(), "12")\n',
            )

            (ws / 'calc.py').write_text('# stub inicial\n', encoding='utf-8')
            base_git = init_core_worktree(git_dir, ws, initial_message='udh: t4 e2e base')

            run1 = core.create_managed_ticket_run(
                ref, approval_revision=1, plan_sha256=plan_sha,
                ticket_id='T1', request_id='req-e2e-create-t1', mode='STANDARD',
            )
            run2 = core.create_managed_ticket_run(
                ref, approval_revision=1, plan_sha256=plan_sha,
                ticket_id='T2', request_id='req-e2e-create-t2', mode='STANDARD',
            )
            core.register_managed_ticket_contract(
                run1.run_id, expected_revision=0, trust_store=store,
                contract={
                    'approval_ref': 'app-e2e-t1',
                    'at_version': at1['at_version'],
                    'at_manifest_sha256': at1_msha,
                    'coverage': [{'task_id': 'T1', 'criterion_id': 'C1-add', 'test_ids': ['test_t1_calc.TestCalcT1.test_add']}],
                    'context_ids': [],
                    'allowed_context_ids': ['ctx-calc-spec'],
                    'unresolved_gaps': [],
                },
            )
            core.register_managed_ticket_contract(
                run2.run_id, expected_revision=0, trust_store=store,
                contract={
                    'approval_ref': 'app-e2e-t2',
                    'at_version': at2['at_version'],
                    'at_manifest_sha256': at2_msha,
                    'coverage': [
                        {'task_id': 'T2', 'criterion_id': 'C2-add-kept', 'test_ids': ['test_t2_calc.TestCalcT2.test_add_kept']},
                        {'task_id': 'T2', 'criterion_id': 'C2-mul', 'test_ids': ['test_t2_calc.TestCalcT2.test_mul']},
                    ],
                    'context_ids': ['ctx-calc-spec'],
                    'allowed_context_ids': ['ctx-calc-spec'],
                    'unresolved_gaps': [],
                },
            )

            # T2 bloqueado antes de T1 ser aceito
            with self.assertRaises(ConflictError):
                core.start(run2.run_id, 0, 'req-e2e-t2-blocked-initial', trust_store=store)

            # 1. Ticket T1 no Pi 1.2.0 (imagem OCI dedicada, network=none)
            core.start(run1.run_id, 0, 'req-e2e-start-t1', trust_store=store)
            pi_gw_cfg = GatewayConfig(
                run_id=run1.run_id,
                policy_sha256=pol_sha,
                audit_path=store.audit_path_for(f'{run1.run_id}-pi'),
                backend='stub',
                allowed_models=(DEFAULT_STUB_MODEL,),
                allow_tools=True,
                allowed_tool_names=('read', 'write', 'edit'),
                stub_turns=(
                    {
                        'require_tools': True,
                        'tool_calls': [{
                            'id': 'call_t4_pi_1',
                            'name': 'write',
                            'arguments': {
                                'path': 'calc.py',
                                'content': (
                                    'import sys\n'
                                    'op, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
                                    'if op == "add":\n'
                                    '    print(a + b)\n'
                                ),
                            },
                        }],
                    },
                    {
                        'require_tools': True,
                        'text': f'T1 concluído pelo Pi. {sentinel}',
                    },
                ),
            )
            with InferenceGateway(pi_gw_cfg) as pi_gw:
                pi_res = PiAdapter(runtime_manager=manager).run(
                    workspace=ws,
                    gateway=pi_gw,
                    profile='developer_python_stdlib',
                    prompt='Implemente a operação add em calc.py.',
                    allowed_tools=('read', 'write', 'edit'),
                    forbidden_rw_roots=(store.root, git_dir),
                )
            self.assertTrue(pi_res.ok, pi_res.reason_code)
            self.assertIn(sentinel, pi_res.assistant_text)

            t1_git = commit_handoff_snapshot(git_dir, ws, message='udh: t4 e2e t1 pi')
            t1_art = store.promote_artifact(ws, commit_sha=t1_git['head_commit_sha'])
            executor = TrustedExecutor(core_service=core, trust_store=store, runtime_manager=manager)
            t1_at = executor.verify_run(
                run1.run_id,
                request_id='req-e2e-verify-t1',
                artifact_digest=t1_art['artifact_digest'],
                at_version=at1['at_version'],
                commit_sha=t1_git['head_commit_sha'],
                use_artifact_image=True,
            )
            self.assertEqual(t1_at['report']['verdict'], 'PASS')
            self.assertEqual(t1_at['report']['tests_run'], 1)
            executor.complete_verified_run(
                run1.run_id,
                expected_revision=1,
                request_id='req-e2e-complete-t1',
                evidence_id=t1_at['evidence']['evidence_id'],
                artifact_digest=t1_art['artifact_digest'],
                at_version=at1['at_version'],
            )
            core.record_checkpoint(
                run1.run_id,
                expected_revision=2,
                request_id='req-e2e-ckpt-t1',
                last_confirmed_step='t1_pi_verified_done',
                base_commit_sha=base_git['base_commit_sha'],
                head_commit_sha=t1_git['head_commit_sha'],
                worktree_snapshot_sha256=t1_git['worktree_snapshot_sha256'],
                evidence_hashes=(t1_at['evidence']['evidence_sha256'],),
                external_idempotency_keys=('idem-e2e-t1',),
                unknown_pendencies=(),
            )

            # T1 está DONE, mas sem F-02 + aceite oficial -> T2 ainda bloqueado
            with self.assertRaises(ConflictError):
                core.start(run2.run_id, 0, 'req-e2e-t2-blocked-no-accept', trust_store=store)

            t1_review = core.record_managed_ticket_review(
                run1.run_id, expected_revision=2, request_id='req-e2e-review-t1',
                at_evidence_id=t1_at['evidence']['evidence_id'],
                independent_context=True, review_context_ref='rev-e2e-t1',
                confirmation_ref='human-e2e-t1', findings=[], trust_store=store,
            )
            t1_accept = core.accept_managed_ticket(
                run1.run_id, expected_revision=2, request_id='req-e2e-accept-t1',
                at_evidence_id=t1_at['evidence']['evidence_id'],
                review_evidence_id=t1_review['evidence_id'], trust_store=store,
            )

            # 2. Reabrir CoreService e iniciar T2 via gate transacional + handoff Pi -> OpenCode
            core = CoreService(SQLiteStore(db_path))
            executor = TrustedExecutor(core_service=core, trust_store=store, runtime_manager=manager)
            t2_start = core.start(run2.run_id, 0, 'req-e2e-start-t2', trust_store=store)
            self.assertEqual(t2_start['to_state'], 'IN_PROGRESS')

            ctx_refs = {
                'ctx-calc-spec': {
                    'kind': 'spec-summary',
                    'ref_sha256': hashlib.sha256(b'calc-spec-v1').hexdigest(),
                    'summary': 'calc.py deve suportar add e mul recebendo dois inteiros via argv.',
                },
            }
            packet = build_managed_handoff_packet(
                core_service=core,
                trust_store=store,
                run_id=run2.run_id,
                git_dir=git_dir,
                workspace=ws,
                from_adapter='pi',
                to_adapter='opencode',
                allowed_tools=('read', 'write', 'edit', 'glob', 'grep', 'list'),
                context_refs=ctx_refs,
            )
            verify_managed_handoff_packet(packet)
            handoff_prompt = render_managed_handoff_prompt(packet)

            self.assertNotIn(sentinel, json.dumps(packet, ensure_ascii=False))
            self.assertNotIn(sentinel, handoff_prompt)
            self.assertEqual(packet['predecessors'][0]['acceptance_snapshot_sha256'], t1_accept['snapshot_sha256'])

            # 3. Ticket T2 no OpenCode 1.18.35 (imagem OCI dedicada, network=none, E3/E4)
            oc_gw_cfg = GatewayConfig(
                run_id=run2.run_id,
                policy_sha256=pol_sha,
                audit_path=store.audit_path_for(f'{run2.run_id}-opencode'),
                backend='stub',
                allowed_models=(DEFAULT_STUB_MODEL,),
                allow_tools=True,
                allowed_tool_names=('read', 'write', 'edit', 'glob', 'grep', 'list'),
                stub_turns=(
                    {
                        'require_tools': True,
                        'tool_calls': [{
                            'id': 'call_t4_oc_1',
                            'name': 'write',
                            'arguments': {
                                'filePath': '/workspace/calc.py',
                                'content': (
                                    'import sys\n'
                                    'op, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
                                    'if op == "add":\n'
                                    '    print(a + b)\n'
                                    'elif op == "mul":\n'
                                    '    print(a * b)\n'
                                ),
                            },
                        }],
                    },
                    {
                        'require_tools': True,
                        'text': 'T2 concluído pelo OpenCode.',
                    },
                ),
            )
            with InferenceGateway(oc_gw_cfg) as oc_gw:
                oc_res = OpenCodeAdapter(runtime_manager=manager).run(
                    workspace=ws,
                    gateway=oc_gw,
                    profile='developer_python_stdlib',
                    prompt=handoff_prompt,
                    allowed_tools=('read', 'write', 'edit', 'glob', 'grep', 'list'),
                    limits=DEFAULT_OPENCODE_LIMITS,
                    forbidden_rw_roots=(store.root, git_dir),
                )
            self.assertTrue(oc_res.ok, oc_res.reason_code)
            self.assertTrue(oc_res.config_integrity_ok)
            self.assertIn('T2 concluído pelo OpenCode.', oc_res.assistant_text)

            t2_git = commit_handoff_snapshot(git_dir, ws, message='udh: t4 e2e t2 opencode')
            self.assertNotEqual(t2_git['head_commit_sha'], t1_git['head_commit_sha'])
            t2_art = store.promote_artifact(ws, commit_sha=t2_git['head_commit_sha'])
            t2_at = executor.verify_run(
                run2.run_id,
                request_id='req-e2e-verify-t2',
                artifact_digest=t2_art['artifact_digest'],
                at_version=at2['at_version'],
                commit_sha=t2_git['head_commit_sha'],
                use_artifact_image=True,
            )
            self.assertEqual(t2_at['report']['verdict'], 'PASS')
            self.assertEqual(t2_at['report']['tests_run'], 2)
            executor.complete_verified_run(
                run2.run_id,
                expected_revision=1,
                request_id='req-e2e-complete-t2',
                evidence_id=t2_at['evidence']['evidence_id'],
                artifact_digest=t2_art['artifact_digest'],
                at_version=at2['at_version'],
            )
            core.record_checkpoint(
                run2.run_id,
                expected_revision=2,
                request_id='req-e2e-ckpt-t2',
                last_confirmed_step='t2_opencode_verified_done',
                base_commit_sha=t1_git['head_commit_sha'],
                head_commit_sha=t2_git['head_commit_sha'],
                worktree_snapshot_sha256=t2_git['worktree_snapshot_sha256'],
                evidence_hashes=(t2_at['evidence']['evidence_sha256'],),
                external_idempotency_keys=('idem-e2e-t2',),
                unknown_pendencies=(),
            )
            t2_review = core.record_managed_ticket_review(
                run2.run_id, expected_revision=2, request_id='req-e2e-review-t2',
                at_evidence_id=t2_at['evidence']['evidence_id'],
                independent_context=True, review_context_ref='rev-e2e-t2',
                confirmation_ref='human-e2e-t2', findings=[], trust_store=store,
            )
            t2_accept = core.accept_managed_ticket(
                run2.run_id, expected_revision=2, request_id='req-e2e-accept-t2',
                at_evidence_id=t2_at['evidence']['evidence_id'],
                review_evidence_id=t2_review['evidence_id'], trust_store=store,
            )
            self.assertEqual(core.show(run2.run_id).state, 'DONE')
            self.assertEqual(t2_accept['commit_sha'], t2_git['head_commit_sha'])


if __name__ == '__main__':
    unittest.main()
