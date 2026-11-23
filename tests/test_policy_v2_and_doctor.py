"""Testes complementares da Política v2 (e invariância de v1) e do preflight doctor (B1.3)."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from udh import __main__ as main_cli
from udh.doctor import run_doctor
from udh.service import CoreService
from udh.store import SQLiteStore


HASH64_A = 'a' * 64
HASH64_B = 'b' * 64


def _healthy_probes(*, is_wsl2=False):
    return {
        'system': 'Linux',
        'machine': 'x86_64',
        'release': '6.12.111+deb13-amd64',
        'is_wsl2': is_wsl2,
        'cgroup_v2_controllers': ['cpu', 'memory', 'pids'],
        'unprivileged_userns_clone': '1',
        'uid': 1000,
        'username': 'debian',
        'has_subuid': True,
        'has_subgid': True,
        'podman': {
            'available': True,
            'binary': '/usr/bin/podman',
            'rootless': True,
            'cgroup_version': 'v2',
            'podman_version': '5.4.2',
        },
    }


class PolicyV2AndDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_v1 = Path(self.tmp.name) / 'v1.sqlite'
        self.db_v4 = Path(self.tmp.name) / 'v4.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def test_policy_v1_preserved_on_v1_and_policy_v2_state_aware_on_v4(self):
        svc_v1 = CoreService(SQLiteStore(self.db_v1))
        svc_v1.initialize(schema_version=1)
        run_v1 = svc_v1.create('spec', '1', 'r-1')
        pol_v1_read = svc_v1.policy(run_v1.run_id, 'READ_RUN')
        pol_v1_tr = svc_v1.policy(run_v1.run_id, 'TRANSITION_RUN')
        self.assertEqual(pol_v1_read['policy_version'], 1)
        self.assertEqual(pol_v1_read['decision'], 'ALLOW')
        self.assertEqual(pol_v1_tr['decision'], 'DENY')
        self.assertEqual(pol_v1_tr['reason_code'], 'TRANSITIONS_NOT_ENABLED')
        self.assertNotIn('revision', pol_v1_read)

        svc_v4 = CoreService(SQLiteStore(self.db_v4))
        svc_v4.initialize(schema_version=4)
        run_v4 = svc_v4.create('spec', '1', 'r-4')

        # PENDING: READ_RUN=ALLOW, TRANSITION_RUN=ALLOW, EXECUTE_AGENT=DENY, ACCEPT_ARTIFACT=DENY, DELIVER_ARTIFACT=DENY
        p_read = svc_v4.policy(run_v4.run_id, 'READ_RUN')
        p_tr = svc_v4.policy(run_v4.run_id, 'TRANSITION_RUN')
        p_exec = svc_v4.policy(run_v4.run_id, 'EXECUTE_AGENT')
        p_acc = svc_v4.policy(run_v4.run_id, 'ACCEPT_ARTIFACT')
        p_del = svc_v4.policy(run_v4.run_id, 'DELIVER_ARTIFACT')
        p_unk = svc_v4.policy(run_v4.run_id, 'EXPLODE_EVERYTHING')

        self.assertEqual(p_read['policy_version'], 2)
        self.assertEqual(p_read['decision'], 'ALLOW')
        self.assertEqual(p_tr['decision'], 'ALLOW')
        self.assertEqual((p_exec['decision'], p_exec['reason_code']), ('DENY', 'RUN_NOT_IN_PROGRESS'))
        self.assertEqual((p_acc['decision'], p_acc['reason_code']), ('DENY', 'RUN_NOT_IN_PROGRESS'))
        self.assertEqual((p_del['decision'], p_del['reason_code']), ('DENY', 'DELIVERY_GATE_UNAVAILABLE'))
        self.assertEqual((p_unk['decision'], p_unk['reason_code']), ('DENY', 'UNKNOWN_ACTION'))
        self.assertNotEqual(p_read['policy_sha256'], pol_v1_read['policy_sha256'])

        # IN_PROGRESS sem evidência: EXECUTE_AGENT continua DENY (Change 2), ACCEPT_ARTIFACT=DENY (falta evidência)
        svc_v4.start(run_v4.run_id, 0, 'st-1')
        p_exec_prog = svc_v4.policy(run_v4.run_id, 'EXECUTE_AGENT')
        self.assertEqual((p_exec_prog['decision'], p_exec_prog['reason_code']), ('DENY', 'GATEWAY_AND_ADAPTER_NOT_READY'))
        p_exec_not_ready = svc_v4.policy(run_v4.run_id, 'EXECUTE_AGENT', preflight_status='NOT_READY')
        self.assertEqual((p_exec_not_ready['decision'], p_exec_not_ready['reason_code']), ('DENY', 'PREFLIGHT_NOT_READY'))
        p_acc_missing = svc_v4.policy(run_v4.run_id, 'ACCEPT_ARTIFACT')
        self.assertEqual((p_acc_missing['decision'], p_acc_missing['reason_code']), ('DENY', 'MISSING_OFFICIAL_EVIDENCE'))

        # Evidência PASS habilita ACCEPT_ARTIFACT
        ev = svc_v4.record_evidence(
            run_v4.run_id, 'ev-1', kind='AT_RESULT', verdict='PASS',
            artifact_digest=HASH64_A, evidence_sha256=HASH64_B,
            collector_route='TRUSTED_EXECUTOR', summary_ref='at.json',
        )
        p_acc_ok = svc_v4.policy(run_v4.run_id, 'ACCEPT_ARTIFACT')
        self.assertEqual((p_acc_ok['decision'], p_acc_ok['reason_code']), ('ALLOW', 'OFFICIAL_EVIDENCE_VERIFIED'))

        # Pendência UNKNOWN ativa bloqueia TRANSITION_RUN, EXECUTE_AGENT e ACCEPT_ARTIFACT
        svc_v4.record_checkpoint(run_v4.run_id, 1, 'cp-unk', 'PUBLISH', unknown_pendencies=('pending-pr',))
        for action in ('TRANSITION_RUN', 'EXECUTE_AGENT', 'ACCEPT_ARTIFACT'):
            p_blocked = svc_v4.policy(run_v4.run_id, action)
            self.assertEqual((p_blocked['decision'], p_blocked['reason_code']), ('DENY', 'UNKNOWN_PENDENCY_ACTIVE'))

        # Reconcilia e conclui -> DONE bloqueia TRANSITION_RUN
        svc_v4.record_checkpoint(run_v4.run_id, 1, 'cp-ok', 'RECONCILED', unknown_pendencies=())
        svc_v4.complete(run_v4.run_id, 1, 'done-1', ev['evidence_id'])
        p_done_tr = svc_v4.policy(run_v4.run_id, 'TRANSITION_RUN')
        self.assertEqual((p_done_tr['decision'], p_done_tr['reason_code']), ('DENY', 'RUN_TERMINAL_DONE'))

    def test_doctor_reports_ready_with_limits_on_healthy_host_and_db(self):
        svc = CoreService(SQLiteStore(self.db_v4))
        svc.initialize(schema_version=4)
        svc.create('spec', '1', 'r-1')

        report = run_doctor(db_path=self.db_v4, env_probes=_healthy_probes())
        self.assertEqual(report['overall_status'], 'READY_WITH_LIMITS')
        by_id = {c['id']: c for c in report['checks']}
        self.assertEqual(by_id['platform']['status'], 'READY')
        self.assertEqual(by_id['cgroups']['status'], 'READY')
        self.assertEqual(by_id['userns']['status'], 'READY')
        self.assertEqual(by_id['podman']['status'], 'READY')
        self.assertEqual(by_id['database']['status'], 'READY')
        self.assertEqual(by_id['database']['details']['runs_checked'], 1)
        self.assertEqual(by_id['runtime_components']['status'], 'LIMITED')

    def test_doctor_reports_not_ready_on_missing_prereqs_or_corrupt_db(self):
        # Falha de plataforma / cgroups / userns / podman
        bad = _healthy_probes()
        bad['cgroup_v2_controllers'] = []
        bad['has_subuid'] = False
        bad['podman'] = {'available': False, 'reason': 'binary_missing'}
        report = run_doctor(env_probes=bad)
        self.assertEqual(report['overall_status'], 'NOT_READY')
        by_id = {c['id']: c for c in report['checks']}
        self.assertEqual(by_id['cgroups']['reason_code'], 'CGROUP_V2_UNAVAILABLE')
        self.assertEqual(by_id['userns']['reason_code'], 'USERNS_OR_SUBID_MISSING')
        self.assertEqual(by_id['podman']['reason_code'], 'PODMAN_ROOTLESS_UNAVAILABLE')

        # Banco corrompido detectado sem criar/modificar o arquivo
        svc = CoreService(SQLiteStore(self.db_v4))
        svc.initialize(schema_version=4)
        run = svc.create('spec', '1', 'r-corrupt')
        con = sqlite3.connect(self.db_v4)
        try:
            con.execute('UPDATE "runs" SET revision = 9 WHERE id = ?', (run.run_id,))
            con.commit()
        finally:
            con.close()
        report_db = run_doctor(db_path=self.db_v4, env_probes=_healthy_probes())
        self.assertEqual(report_db['overall_status'], 'NOT_READY')
        by_id_db = {c['id']: c for c in report_db['checks']}
        self.assertEqual(by_id_db['database']['reason_code'], 'DATABASE_INTEGRITY_FAILED')

        # Banco inexistente não é criado por doctor
        missing = Path(self.tmp.name) / 'missing.sqlite'
        report_missing = run_doctor(db_path=missing, env_probes=_healthy_probes())
        self.assertEqual(report_missing['overall_status'], 'NOT_READY')
        self.assertFalse(missing.exists())

    def test_doctor_podman_probe_handles_null_json_fields_without_traceback(self):
        from unittest.mock import patch
        from udh.doctor import _default_podman_probe

        class DummyCompleted:
            returncode = 0
            stderr = ''

            def __init__(self, stdout):
                self.stdout = stdout

        for raw in ('{"host": null, "version": null}', '{"host": {"security": null}, "version": null}', 'null'):
            with patch('udh.doctor.shutil.which', return_value='/usr/bin/podman'), \
                    patch('udh.doctor.subprocess.run', return_value=DummyCompleted(raw)):
                probe = _default_podman_probe()
            self.assertEqual(probe['available'], True)
            self.assertEqual(probe['rootless'], False)
            probes = _healthy_probes()
            probes['podman'] = probe
            report = run_doctor(env_probes=probes)
            self.assertEqual(report['overall_status'], 'NOT_READY')
            by_id = {c['id']: c for c in report['checks']}
            self.assertEqual(by_id['podman']['reason_code'], 'PODMAN_ROOTLESS_UNAVAILABLE')

    def test_doctor_cli_returns_valid_json_without_traceback(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main_cli.main(['doctor'])
        self.assertIn(code, (0, 3))
        self.assertEqual(err.getvalue(), '')
        parsed = json.loads(out.getvalue())
        self.assertIn(parsed['overall_status'], ('READY_WITH_LIMITS', 'NOT_READY'))
        self.assertTrue(any(c['id'] == 'runtime_components' for c in parsed['checks']))


if __name__ == '__main__':
    unittest.main()
