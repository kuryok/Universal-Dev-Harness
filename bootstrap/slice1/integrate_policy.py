"""Coordenador externo: candidato só executa no runtime isolado."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from cli_bridge import candidate_hashes
from integrate_acceptance import LAUNCHER, environment
from policy_seal import create_seal, verify_seal
from seal_version import verify_seal as verify_v1

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'
BASELINE_COMMIT = 'b26fd969e20a72b8494b815c57e984b279699714'


class Results(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.outcomes = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[type(test).__name__ + '.' + test._testMethodName] = 'PASS'

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[type(test).__name__ + '.' + test._testMethodName] = 'FAIL'

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[type(test).__name__ + '.' + test._testMethodName] = 'ERROR'

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            key = type(test).__name__ + '.' + test._testMethodName
            self.outcomes[key] = 'FAIL' if issubclass(err[0], test.failureException) else 'ERROR'


def run_candidate(seal, version, candidate_ref, *, legacy=False):
    verify = verify_v1 if legacy else verify_seal
    verify(seal, version)
    with tempfile.TemporaryDirectory(prefix='policy-integration-', dir=BASE) as folder:
        temporary = Path(folder)
        candidate, work, control = (temporary / name for name in ('candidate', 'work', 'control'))
        for path in (candidate, work, control):
            path.mkdir()
        names = subprocess.run(['git', 'ls-tree', '-r', '--name-only', candidate_ref, '--', 'udh'],
                               cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
        if not names or any(not name.startswith('udh/') or not name.endswith('.py') for name in names):
            raise ValueError('Snapshot de produto inválido')
        for name in names:
            data = subprocess.run(['git', 'show', candidate_ref + ':' + name], cwd=ROOT,
                                  check=True, capture_output=True).stdout
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (candidate / '_udh_cli.py').write_text(LAUNCHER)
        audit = control / 'audit'
        audit.mkdir()
        hashes = candidate_hashes(candidate)
        config = dict(candidate=str(candidate), work=str(work), seal=str(seal), audit=str(audit),
                      at_version=version, candidate_hashes=hashes)
        raw = json.dumps(config, sort_keys=True).encode()
        config_path = control / 'config.json'
        config_path.write_bytes(raw)
        test_path = seal / ('tests/test_core_state.py' if legacy else 'tests/test_policy.py')
        spec = importlib.util.spec_from_file_location('trusted_' + uuid.uuid4().hex, test_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        bridge = 'cli_bridge.py' if legacy else 'policy_bridge.py'
        settings = dict(UDH_TEST_WORK_ROOT=str(work), UDH_CANDIDATE_ROOT=str(candidate),
                        UDH_BRIDGE_CONFIG=str(config_path),
                        UDH_BRIDGE_CONFIG_SHA256=hashlib.sha256(raw).hexdigest(),
                        UDH_CLI_PREFIX_JSON=json.dumps([sys.executable, '-I', '-B', str(seal / 'controller' / bridge)]))
        stream = io.StringIO()
        with environment(settings):
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
                unittest.defaultTestLoader.loadTestsFromModule(module))
        records = [json.loads(path.read_text()) for path in sorted(audit.glob('*.json'))]
        if (not records or any(item.get('classification') != 'FUNCTIONAL_EXIT'
                               or not item.get('removed') for item in records)):
            raise ValueError('Auditoria contém falha de infraestrutura')
        verify(seal, version)
        report = dict(candidate_ref=candidate_ref, candidate_hashes=hashes, at_version=version,
                      tests_run=result.testsRun, outcomes=result.outcomes,
                      errors=[str(test) for test, _ in result.errors],
                      failures=[str(test) for test, _ in result.failures],
                      skipped=[str(test) for test, _ in result.skipped],
                      audit=records, transcript=stream.getvalue())
        if result.expectedFailures or result.unexpectedSuccesses:
            raise ValueError('Resultado excluído ou inesperado')
    report['temporary_removed'] = not temporary.exists()
    return report


def main():
    parser = argparse.ArgumentParser(description='Verificar candidato isolado contra AT de policy.')
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    commit = subprocess.run(['git', 'rev-parse', '--verify', args.candidate + '^{commit}'],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if args.baseline and commit != BASELINE_COMMIT:
        raise ValueError('Baseline exige o commit fixo do Core sem policy')
    with tempfile.TemporaryDirectory(prefix='policy-integration-', dir=BASE) as folder:
        seal = Path(folder) / 'seal'
        manifest = create_seal(ROOT, seal)
        report = run_candidate(seal, manifest['at_version'], commit)
        coverage = json.loads((seal / 'tests/coverage.json').read_text())
        report['coverage'] = coverage
        report['source_files'] = manifest['body']['files']
        report['specs'] = manifest['body']['specs']
        report['mode'] = 'BASELINE' if args.baseline else 'CANDIDATE'
        # A classificação do baseline usa o mapa independente, não a saída do candidato.
        expectations = coverage['tests']
        expected = {name: ('FAIL' if args.baseline and value['baseline_expected'] == 'NEW_FAILURE' else 'PASS')
                    for name, value in expectations.items()}
        report['result'] = 'PASS' if (report['outcomes'] == expected and not report['errors']
                                    and not report['skipped']) else 'FAIL'
        destination = BASE / 'evidence' / ('policy-baseline.json' if args.baseline else 'policy-candidate.json')
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(report['result'], report['tests_run'], destination)
        return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
