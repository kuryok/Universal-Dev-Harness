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


if __name__ == '__main__':
    unittest.main()
