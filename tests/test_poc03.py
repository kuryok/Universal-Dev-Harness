"""Testes ponta a ponta em Podman rootless para POC-03 e doctor --conformance multi-agente."""
from pathlib import Path
import tempfile
import unittest

from udh.doctor import run_doctor
from udh.poc import run_poc_03
from udh.runtime import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_OPENCODE_MANIFEST_PATH,
    RuntimeIsolationError,
    RuntimeManager,
)
from udh.service import CoreService
from udh.store import SQLiteStore


def _both_images_ready() -> bool:
    try:
        mgr = RuntimeManager(
            manifest_path=DEFAULT_MANIFEST_PATH,
            opencode_manifest_path=DEFAULT_OPENCODE_MANIFEST_PATH,
        )
        pi_m = mgr.load_manifest()
        oc_m = mgr.load_opencode_manifest()
        mgr.verify_image_present(pi_m.image_ref)
        mgr.verify_image_present(oc_m.image_ref)
        return True
    except (RuntimeIsolationError, OSError):
        return False


@unittest.skipUnless(_both_images_ready(), 'Imagens OCI Pi/OpenCode ou Podman rootless indisponíveis')
class TestPoc03AndMultiAgentDoctor(unittest.TestCase):
    def test_poc_03_end_to_end_pass(self):
        result = run_poc_03()
        self.assertEqual(result['verdict'], 'PASS', result)

    def test_doctor_conformance_multi_agent_and_authorize_both(self):
        with tempfile.TemporaryDirectory(prefix='udh-doc-multi-') as tmp:
            root = Path(tmp)
            db_path = root / 'core-v4.db'
            store_dir = root / 'trust-store'
            svc = CoreService(SQLiteStore(db_path))
            svc.initialize(schema_version=4)
            run = svc.create('spec-multi', '1', 'req-multi-1', ticket_id='T-MULTI', mode='STANDARD')
            svc.start(run.run_id, 0, 'req-multi-start')

            # Somente Pi -> conformidade parcial (READY_WITH_LIMITS), permite Pi e nega OpenCode
            pi_only = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=True, adapters=('pi',))
            self.assertEqual(pi_only['overall_status'], 'READY')
            partial = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=False)
            self.assertEqual(partial['overall_status'], 'READY_WITH_LIMITS')
            self.assertEqual(svc.authorize_agent_execution(run.run_id, store_dir=store_dir, adapter='pi')['decision'], 'ALLOW')
            self.assertEqual(svc.authorize_agent_execution(run.run_id, store_dir=store_dir, adapter='opencode')['decision'], 'DENY')

            # Conformance completa -> READY com ['pi', 'opencode']
            full = run_doctor(db_path=db_path, store_dir=store_dir, run_conformance=True)
            self.assertEqual(full['overall_status'], 'READY')
            rt = next(c for c in full['checks'] if c['id'] == 'runtime_components')
            self.assertEqual(rt['details']['conformant_adapters'], ['pi', 'opencode'])
            self.assertEqual(svc.authorize_agent_execution(run.run_id, store_dir=store_dir, adapter='opencode')['decision'], 'ALLOW')


if __name__ == '__main__':
    unittest.main()
