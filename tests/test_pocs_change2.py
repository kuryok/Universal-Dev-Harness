"""Testes ponta a ponta em Podman rootless para POC-01, POC-02, POC-04 e doctor --conformance."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from udh.doctor import run_doctor
from udh.poc import run_poc_01, run_poc_02, run_poc_04
from udh.runtime import DEFAULT_MANIFEST_PATH, RuntimeIsolationError, RuntimeManager
from udh.service import CoreService
from udh.store import SQLiteStore


def _podman_and_image_ready() -> bool:
    try:
        info = subprocess.run(['podman', 'info', '--format', 'json'], capture_output=True, text=True, timeout=5, check=False)
        if info.returncode != 0:
            return False
        mgr = RuntimeManager(manifest_path=DEFAULT_MANIFEST_PATH)
        manifest = mgr.load_manifest()
        mgr.verify_image_present(manifest.image_ref)
        return True
    except (OSError, subprocess.TimeoutExpired, RuntimeIsolationError):
        return False


@unittest.skipUnless(_podman_and_image_ready(), 'Podman rootless ou imagem OCI dedicada indisponível neste sandbox.')
class Change2LivePodmanTests(unittest.TestCase):
    def test_poc_01_live(self):
        result = run_poc_01()
        self.assertEqual(result['verdict'], 'PASS', result)

    def test_poc_02_live(self):
        result = run_poc_02()
        self.assertEqual(result['verdict'], 'PASS', result)

    def test_poc_04_live(self):
        result = run_poc_04()
        self.assertEqual(result['verdict'], 'PASS', result)

    def test_doctor_conformance_and_policy_authorization_live(self):
        with tempfile.TemporaryDirectory(prefix='udh-live-conf-') as tmp:
            store_dir = Path(tmp) / 'store'
            db_path = Path(tmp) / 'v4.sqlite'
            svc = CoreService(SQLiteStore(db_path))
            svc.initialize(schema_version=4)
            run = svc.create('spec-live', '1', 'req-live-1')
            svc.start(run.run_id, 0, 'req-live-start')

            # Antes de rodar conformance: doctor -> READY_WITH_LIMITS, authorize -> DENY
            before = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=False)
            self.assertEqual(before['overall_status'], 'READY_WITH_LIMITS')
            auth_before = svc.authorize_agent_execution(run.run_id, store_dir=store_dir)
            self.assertEqual(auth_before['decision'], 'DENY')

            # Roda conformance real: doctor -> READY, cache salvo, authorize -> ALLOW
            conf = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=True)
            self.assertEqual(conf['overall_status'], 'READY', conf)
            after = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=False)
            self.assertEqual(after['overall_status'], 'READY', after)
            auth_after = svc.authorize_agent_execution(run.run_id, store_dir=store_dir)
            self.assertEqual(auth_after['decision'], 'ALLOW')
            self.assertEqual(auth_after['reason_code'], 'AGENT_EXECUTION_AUTHORIZED')


if __name__ == '__main__':
    unittest.main()
