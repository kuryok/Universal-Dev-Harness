"""Preparo e baseline transitório do bloqueio; nunca importa o Core no host."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from integrate_acceptance import environment
from integrate_policy import Results
from block_seal import BASELINE_COMMIT, create_seal

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False, allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(folder, ref, template):
    core, state, control, audit = (folder / name for name in ('core', 'state', 'control', 'audit'))
    for path in (core, state, control, audit):
        path.mkdir()
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, '--', 'udh'],
                                    cwd=ROOT, text=True).splitlines()
    if not names or any(not name.startswith('udh/') or not name.endswith('.py') for name in names):
        raise ValueError('Inventário do Core inválido')
    hashes = {}
    for name in names:
        data = subprocess.check_output(['git', 'show', ref + ':' + name], cwd=ROOT)
        target = core / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = digest(data)
    manifest_raw = canonical({'schema': 1, 'files': hashes})
    manifest_pin = digest(manifest_raw)
    config_raw = canonical(dict(schema=1, db_path='/state/udh.sqlite',
        allowed_operations=['MIGRATE_V2', 'BLOCK_PENDING'], core_manifest_sha256=manifest_pin))
    config_pin = digest(config_raw)
    launcher = template.read_bytes().replace(b'__CORE_MANIFEST_SHA256__', manifest_pin.encode()).replace(
                                           b'__CONTROL_SHA256__', config_pin.encode())
    (control / 'manifest.json').write_bytes(manifest_raw)
    (control / 'config.json').write_bytes(config_raw)
    (control / 'operator_launcher.py').write_bytes(launcher)
    return dict(core=str(core), state=str(state), control=str(control), audit=str(audit),
                core_hashes=hashes, core_manifest_sha256=manifest_pin,
                control_sha256=config_pin, launcher_sha256=digest(launcher))


def run_suite(seal, manifest, ref):
    with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as name:
        temporary = Path(name)
        config = prepare(temporary, ref, seal / 'controller/block_fixtures/operator_launcher.py')
        config.update(seal=str(seal), at_version=manifest['at_version'])
        raw = canonical(config)
        config_path = temporary / 'bridge-config.json'
        config_path.write_bytes(raw)
        prefix = [sys.executable, '-I', '-B', str(seal / 'controller/operator_bridge.py')]
        settings = dict(UDH_OPERATOR_PREFIX_JSON=json.dumps(prefix),
                        UDH_CORE_PREFIX_JSON=json.dumps(prefix + ['--core']),
                        UDH_OPERATOR_STATE_ROOT=config['state'],
                        UDH_BLOCK_BRIDGE_CONFIG=str(config_path),
                        UDH_BLOCK_BRIDGE_CONFIG_SHA256=digest(raw))
        test_dir = seal / 'tests'
        stream = io.StringIO()
        with environment(settings):
            suite = unittest.defaultTestLoader.discover(str(test_dir), pattern='test_*.py')
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(suite)
        audit = [json.loads(path.read_text()) for path in sorted(Path(config['audit']).glob('*.json'))]
        coverage = json.loads((test_dir / 'coverage.json').read_text())
        report = dict(candidate_ref=ref, at_version=manifest['at_version'],
                      source_files=manifest['body']['files'], specs=manifest['body']['specs'],
                      candidate_hashes=config['core_hashes'], coverage=coverage,
                      tests_run=result.testsRun, outcomes=result.outcomes,
                      errors=[str(test) for test, _ in result.errors],
                      failures=[str(test) for test, _ in result.failures],
                      skipped=[str(test) for test, _ in result.skipped], audit=audit,
                      failed_methods=sorted(set(type(getattr(test, 'test_case', test)).__name__ + '.' +
                           getattr(test, 'test_case', test)._testMethodName for test, _ in result.failures)),
                      transcript=stream.getvalue())
        if result.expectedFailures or result.unexpectedSuccesses:
            raise ValueError('Casos excluídos ou inesperados')
    report['temporary_removed'] = not temporary.exists()
    return report


def main():
    parser = argparse.ArgumentParser(description='Baseline independente do bloqueio.')
    parser.add_argument('--baseline', action='store_true', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as folder:
        seal = Path(folder) / 'seal'
        manifest = create_seal(ROOT, seal)
        report = run_suite(seal, manifest, BASELINE_COMMIT)
        expected = {name: ('FAIL' if value['baseline_expected'] == 'NEW_FAILURE' else 'PASS')
                    for name, value in report['coverage']['methods'].items()}
        infra = (not report['audit'] or any(row.get('classification') not in
                 ('FUNCTIONAL_EXIT', 'OBSERVATION', 'ADVERSARY_CHECK') or not row.get('removed')
                 for row in report['audit']))
        report['mode'] = 'BASELINE'
        report['baseline_discrimination'] = ('PASS' if report['outcomes'] == expected
            and not report['errors'] and not report['skipped'] and not infra else 'FAIL')
        regression = BASE / 'evidence/block-regression.json'
        external = json.loads(regression.read_text()) if regression.exists() else {}
        report['external_checks'] = {'R1': 'PASS' if external.get('result') == 'PASS'
            and external.get('candidate_ref') == BASELINE_COMMIT else 'NOT_RUN'}
        report['external_evidence'] = external
        report['external_evidence_sha256'] = digest(canonical(external)) if regression.exists() else None
        pending = report['coverage'].get('pending', {})
        functional_pending = any(item.get('blocks_functional_execution', True) for item in pending.values())
        report['result'] = ('PASS' if report['baseline_discrimination'] == 'PASS' and not functional_pending
                            and report['external_checks']['R1'] == 'PASS' else 'INCOMPLETE')
        destination = BASE / 'evidence/block-baseline.json'
        destination.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode() + b'\n')
        print(report['result'], report['baseline_discrimination'], report['tests_run'], destination)
        return 0 if report['baseline_discrimination'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
