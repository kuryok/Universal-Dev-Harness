"""Testes complementares da TrustStore e do Executor Confiável F-04."""
import os
from pathlib import Path
import tempfile
import unittest

from udh.policy import policy_v2_sha256
from udh.trust_store import TamperingError, TrustStore, TrustStoreError


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = TrustStore(self.root / 'trust-store')

    def tearDown(self):
        self.tmp.cleanup()

    def test_seal_and_verify_at_detects_bitflip_and_extra_files(self):
        at_src = self.root / 'at'
        at_src.mkdir()
        (at_src / 'test_one.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n', encoding='utf-8')
        manifest = self.store.seal_at_suite(at_src, scenarios=('Cenário 1',))
        at_dir, verified = self.store.verify_sealed_at(manifest['at_version'])
        self.assertEqual(verified['at_version'], manifest['at_version'])

        # Arquivo extra injetado na store invalida verificação
        extra = at_dir / 'extra.py'
        extra.write_text('x = 1\n', encoding='utf-8')
        with self.assertRaises(TrustStoreError):
            self.store.verify_sealed_at(manifest['at_version'])
        extra.unlink()

        # Bit-flip no arquivo selado invalida verificação
        target = at_dir / 'test_one.py'
        os.chmod(target, 0o644)
        target.write_text('# altered\n', encoding='utf-8')
        with self.assertRaises(TrustStoreError):
            self.store.verify_sealed_at(manifest['at_version'])

    def test_promote_artifact_rejects_recursive_shadowing_and_symlinks(self):
        # 1. Sombreamento recursivo de sitecustomize.py em subdiretório
        ws1 = self.root / 'ws1'
        (ws1 / 'sub').mkdir(parents=True)
        (ws1 / 'sub' / 'sitecustomize.py').write_text('x = 1\n', encoding='utf-8')
        with self.assertRaises(TamperingError):
            self.store.promote_artifact(ws1)

        # 2. Pacote unittest/ em subdiretório
        ws2 = self.root / 'ws2'
        (ws2 / 'deep' / 'unittest').mkdir(parents=True)
        (ws2 / 'deep' / 'unittest' / '__init__.py').write_text('x = 1\n', encoding='utf-8')
        with self.assertRaises(TamperingError):
            self.store.promote_artifact(ws2)

        # 3. Arquivo .pth
        ws3 = self.root / 'ws3'
        ws3.mkdir()
        (ws3 / 'inject.pth').write_text('import sys\n', encoding='utf-8')
        with self.assertRaises(TamperingError):
            self.store.promote_artifact(ws3)

        # 4. Symlink
        ws4 = self.root / 'ws4'
        ws4.mkdir()
        (ws4 / 'link.py').symlink_to('/etc/passwd')
        with self.assertRaises(TamperingError):
            self.store.promote_artifact(ws4)

        # 5. Workspace limpo promove e verifica normalmente
        ws_ok = self.root / 'ws-ok'
        ws_ok.mkdir()
        (ws_ok / 'app.py').write_text('print("clean")\n', encoding='utf-8')
        promoted = self.store.promote_artifact(ws_ok)
        tree, verified = self.store.verify_artifact(promoted['artifact_digest'])
        self.assertTrue((tree / 'app.py').is_file())
        self.assertEqual(verified['artifact_digest'], promoted['artifact_digest'])

    def test_conformance_cache_invalidates_to_stale_on_policy_or_digest_change(self):
        pol = policy_v2_sha256()
        dig = 'sha256:' + 'a' * 64
        self.store.save_conformance(
            policy_sha256=pol,
            image_digest=dig,
            adapter_name='pi',
            adapter_version='1.2.0',
            status='PASS',
        )
        valid = self.store.load_conformance(policy_sha256=pol, image_digest=dig)
        self.assertIsNotNone(valid)
        self.assertEqual(valid['status'], 'PASS')

        # Mudança de digest -> STALE
        stale_dig = self.store.load_conformance(policy_sha256=pol, image_digest='sha256:' + 'b' * 64)
        self.assertEqual(stale_dig['status'], 'STALE')

        # Mudança de política -> STALE
        stale_pol = self.store.load_conformance(policy_sha256='c' * 64, image_digest=dig)
        self.assertEqual(stale_pol['status'], 'STALE')


class _StubRuntimeManager:
    def __init__(self, handler):
        self._handler = handler

    def load_manifest(self):
        from udh.runtime import ImageManifest
        dig = 'sha256:' + 'e' * 64
        return ImageManifest(
            schema_version=1,
            stack_profile='python3-stdlib-v1',
            image_repository='localhost/udh-runtime',
            image_digest=dig,
            image_ref=f'localhost/udh-runtime@{dig}',
            pi_version='1.2.0',
            pi_entrypoint='/opt/pi/cli.js',
            node_bin='/opt/udh/bin/node',
            bridge_path='/opt/udh/bridge.mjs',
            at_runner_path='/opt/udh/at_runner.py',
            bridge_sha256='a' * 64,
            at_runner_sha256='b' * 64,
        )

    def run_artifact(self, **kwargs):
        return self._handler(kwargs)


class TrustedExecutorUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = TrustStore(self.root / 'trust-store')
        self.db_path = self.root / 'v4.sqlite'
        from udh.service import CoreService
        from udh.store import SQLiteStore
        self.svc = CoreService(SQLiteStore(self.db_path))
        self.svc.initialize(schema_version=4)
        self.run = self.svc.create('spec-ex', '1', 'req-ex-create')
        self.svc.start(self.run.run_id, 0, 'req-ex-start')

        at_src = self.root / 'at'
        at_src.mkdir()
        (at_src / 'test_sample.py').write_text(
            'import unittest\n'
            'class S(unittest.TestCase):\n'
            '    def test_one(self): pass\n'
            '    def test_two(self): pass\n',
            encoding='utf-8',
        )
        self.at_version = self.store.seal_at_suite(at_src)['at_version']
        ws = self.root / 'ws'
        ws.mkdir()
        (ws / 'app.py').write_text('print("ok")\n', encoding='utf-8')
        self.artifact_digest = self.store.promote_artifact(ws)['artifact_digest']

    def tearDown(self):
        self.tmp.cleanup()

    def _make_result(self, stdout: str, returncode: int = 0, outcome=None):
        from udh.runtime import ContainerResult
        return ContainerResult(
            container_name='stub-c',
            profile='artifact',
            image_ref='localhost/udh-runtime@sha256:' + 'e' * 64,
            returncode=returncode,
            stdout=stdout,
            stderr='',
            outcome=outcome,
            elapsed_seconds=0.01,
            inspection={},
            cleanup_verified=True,
        )

    def test_executor_rejects_bad_nonce_multiline_and_incomplete_ast_coverage(self):
        import json
        from udh.executor import TrustedExecutor

        # 1. Nonce forjado
        bad_nonce_mgr = _StubRuntimeManager(lambda kw: self._make_result(json.dumps({
            'udhf04': 1, 'nonce': '0' * 64, 'tampering_ok': True, 'ok': True,
            'expected_tests': ['test_sample.S.test_one', 'test_sample.S.test_two'],
            'passed_tests': ['test_sample.S.test_one', 'test_sample.S.test_two'],
            'tests_run': 2, 'failures': 0, 'errors': 0, 'skipped': 0,
        }) + '\n'))
        ex1 = TrustedExecutor(core_service=self.svc, trust_store=self.store, runtime_manager=bad_nonce_mgr)
        r1 = ex1.verify_run(self.run.run_id, request_id='r-bad-nonce', artifact_digest=self.artifact_digest, at_version=self.at_version)
        self.assertEqual(r1['report']['verdict'], 'FAIL')
        self.assertEqual(r1['report']['reason_code'], 'INVALID_OR_FORGED_RUNNER_ENVELOPE')

        # 2. Múltiplas linhas no stdout (poluição de canal)
        def _multiline(kw):
            nonce = json.loads(kw['stdin_bytes'].decode())['nonce']
            return self._make_result('PASS\n' + json.dumps({'udhf04': 1, 'nonce': nonce, 'ok': True}) + '\n')
        ex2 = TrustedExecutor(core_service=self.svc, trust_store=self.store, runtime_manager=_StubRuntimeManager(_multiline))
        r2 = ex2.verify_run(self.run.run_id, request_id='r-multi', artifact_digest=self.artifact_digest, at_version=self.at_version)
        self.assertEqual(r2['report']['verdict'], 'FAIL')

        # 3. Runner omitindo 1 dos 2 casos esperados pelo inventário AST do Core
        def _missing_test(kw):
            nonce = json.loads(kw['stdin_bytes'].decode())['nonce']
            return self._make_result(json.dumps({
                'udhf04': 1, 'nonce': nonce, 'tampering_ok': True, 'ok': True,
                'expected_tests': ['test_sample.S.test_one'],
                'passed_tests': ['test_sample.S.test_one'],
                'tests_run': 1, 'failures': 0, 'errors': 0, 'skipped': 0,
            }) + '\n')
        ex3 = TrustedExecutor(core_service=self.svc, trust_store=self.store, runtime_manager=_StubRuntimeManager(_missing_test))
        r3 = ex3.verify_run(self.run.run_id, request_id='r-ast', artifact_digest=self.artifact_digest, at_version=self.at_version)
        self.assertEqual(r3['report']['verdict'], 'FAIL')

    def test_complete_verified_run_rejects_stale_pass_after_newer_fail_and_non_trusted_route(self):
        import json
        from udh.executor import TrustedExecutor
        from udh.transitions import ConflictError

        # Registra um PASS válido primeiro
        def _valid(kw):
            nonce = json.loads(kw['stdin_bytes'].decode())['nonce']
            return self._make_result(json.dumps({
                'udhf04': 1, 'nonce': nonce, 'tampering_ok': True, 'ok': True,
                'expected_tests': ['test_sample.S.test_one', 'test_sample.S.test_two'],
                'passed_tests': ['test_sample.S.test_one', 'test_sample.S.test_two'],
                'tests_run': 2, 'failures': 0, 'errors': 0, 'skipped': 0,
            }) + '\n')
        ex = TrustedExecutor(core_service=self.svc, trust_store=self.store, runtime_manager=_StubRuntimeManager(_valid))
        ev_pass = ex.verify_run(self.run.run_id, request_id='r-pass-1', artifact_digest=self.artifact_digest, at_version=self.at_version)
        self.assertEqual(ev_pass['report']['verdict'], 'PASS')

        # Registra um FAIL posterior para o MESMO (artifact_digest, at_version)
        ex_fail = TrustedExecutor(
            core_service=self.svc, trust_store=self.store,
            runtime_manager=_StubRuntimeManager(lambda kw: self._make_result('', returncode=1)),
        )
        ev_fail = ex_fail.verify_run(self.run.run_id, request_id='r-fail-2', artifact_digest=self.artifact_digest, at_version=self.at_version)
        self.assertEqual(ev_fail['report']['verdict'], 'FAIL')

        # Tentar concluir usando o PASS antigo deve ser recusado (latest é FAIL)
        with self.assertRaises(ConflictError):
            ex.complete_verified_run(
                self.run.run_id, expected_revision=1, request_id='r-comp-stale',
                evidence_id=ev_pass['evidence']['evidence_id'],
                artifact_digest=self.artifact_digest, at_version=self.at_version,
            )

        # Evidência PASS mais recente mas com collector_route=OPERATOR_LOCAL também é recusada
        ev_op = self.svc.record_evidence(
            self.run.run_id, 'r-op-pass', kind='AT_RESULT', verdict='PASS',
            artifact_digest=self.artifact_digest, evidence_sha256='f' * 64,
            collector_route='OPERATOR_LOCAL', summary_ref='op.json', at_version=self.at_version,
        )
        with self.assertRaises(ConflictError):
            ex.complete_verified_run(
                self.run.run_id, expected_revision=1, request_id='r-comp-op',
                evidence_id=ev_op['evidence_id'],
                artifact_digest=self.artifact_digest, at_version=self.at_version,
            )


if __name__ == '__main__':
    unittest.main()
