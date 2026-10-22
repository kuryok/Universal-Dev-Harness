"""Preparo e baseline transitório do bloqueio; nunca importa o Core no host."""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from integrate_acceptance import environment
from integrate_policy import Results
from block_seal import UNBLOCK_BASELINE_COMMIT, V6_PIN, create_seal, verify_seal

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False, allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(folder, ref, template, profile=1):
    if type(profile) is not int or profile not in (1, 2):
        raise ValueError('Perfil administrativo inválido')
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
    settings = prepare_control(control, hashes, template, profile)
    return dict(core=str(core), state=str(state), audit=str(audit),
                core_hashes=hashes, **settings)


def prepare_control(control, hashes, template, profile):
    """Provisiona um perfil fixo; o candidato não escolhe sua configuração."""
    if type(profile) is not int or profile not in (1, 2):
        raise ValueError('Perfil administrativo inválido')
    operations = ['MIGRATE_V2', 'BLOCK_PENDING']
    if profile == 2:
        operations += ['MIGRATE_V3', 'UNBLOCK_RUN']
    manifest_raw = canonical({'schema': 1, 'files': hashes})
    manifest_pin = digest(manifest_raw)
    config_raw = canonical(dict(schema=profile, db_path='/state/udh.sqlite',
        allowed_operations=operations, core_manifest_sha256=manifest_pin))
    config_pin = digest(config_raw)
    launcher = template.read_bytes().replace(b'__CORE_MANIFEST_SHA256__', manifest_pin.encode()).replace(
                                           b'__CONTROL_SHA256__', config_pin.encode())
    (control / 'manifest.json').write_bytes(manifest_raw)
    (control / 'config.json').write_bytes(config_raw)
    (control / 'operator_launcher.py').write_bytes(launcher)
    return dict(control=str(control), core_manifest_sha256=manifest_pin,
                control_sha256=config_pin, launcher_sha256=digest(launcher))


def run_suite(seal, manifest, ref):
    with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as name:
        temporary = Path(name)
        generation = manifest['body']['generation']
        verify_seal(seal, manifest['at_version'])
        runtime_seal = BASE / 'seals/v6' if generation == 7 else seal
        runtime_manifest = verify_seal(runtime_seal, V6_PIN if generation == 7 else manifest['at_version'])
        if generation == 7:
            for name in ('test_unblock.py', 'coverage.json'):
                if (seal / 'tests' / name).read_bytes() != (runtime_seal / 'tests' / name).read_bytes():
                    raise ValueError('AT funcionais divergentes do executor6 fixado.')
        template = runtime_seal / 'controller/block_fixtures/operator_launcher.py'
        config = prepare(temporary, ref, template, profile=2 if generation in (6, 7) else 1)
        if generation in (6, 7):
            legacy_control = temporary / 'legacy-control'
            legacy_control.mkdir()
            config['legacy_operator'] = prepare_control(
                legacy_control, config['core_hashes'], template, 1)
        config.update(seal=str(runtime_seal), at_version=runtime_manifest['at_version'])
        raw = canonical(config)
        config_path = temporary / 'bridge-config.json'
        config_path.write_bytes(raw)
        prefix = [sys.executable, '-I', '-B', str(runtime_seal / 'controller/operator_bridge.py')]
        settings = dict(UDH_OPERATOR_PREFIX_JSON=json.dumps(prefix),
                        UDH_CORE_PREFIX_JSON=json.dumps(prefix + ['--core']),
                        UDH_OPERATOR_STATE_ROOT=config['state'],
                        UDH_BLOCK_BRIDGE_CONFIG=str(config_path),
                        UDH_BLOCK_BRIDGE_CONFIG_SHA256=digest(raw))
        if generation in (6, 7):
            settings['UDH_LEGACY_OPERATOR_PREFIX_JSON'] = json.dumps(prefix + ['--legacy'])
            settings['UDH_OPERATOR_CORE_MANIFEST_SHA256'] = config['core_manifest_sha256']
            settings['UDH_OPERATOR_CONTROL_SHA256'] = config['control_sha256']
        test_dir = seal / 'tests'
        stream = io.StringIO()
        with environment(settings):
            suite = unittest.defaultTestLoader.discover(str(test_dir), pattern='test_*.py')
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(suite)
        audit = [json.loads(path.read_text()) for path in sorted(Path(config['audit']).glob('*.json'))]
        coverage = json.loads((test_dir / 'coverage.json').read_text())
        verify_seal(seal, manifest['at_version'])
        report = dict(candidate_ref=ref, at_version=manifest['at_version'],
                      execution_at_version=runtime_manifest['at_version'],
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


def main(argv=None):
    from evidence_store import save_run, validate_output
    parser = argparse.ArgumentParser(description='Coleta nova do baseline funcional com referências.')
    parser.add_argument('--baseline', action='store_true', required=True)
    parser.add_argument('--generation', choices=('7',), help='geração explícita de coleta')
    parser.add_argument('--output', help='Resumo novo sob evidence/runs/UUID/.')
    args = parser.parse_args(argv)
    if args.generation is None:
        parser.error('Selecione --generation 7 para uma coleta nova; o modo antigo não grava evidências.')
    try:
        destination = validate_output(args.output)
        with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as folder:
            seal = Path(folder) / 'seal'
            manifest = create_seal(ROOT, seal, generation=7)
            report = run_suite(seal, manifest, UNBLOCK_BASELINE_COMMIT)
            expected = {name: ('FAIL' if value['baseline_expected'] == 'NEW_FAILURE' else 'PASS')
                        for name, value in report['coverage']['methods'].items()}
            infra = (not report['audit'] or any(row.get('classification') not in
                     ('FUNCTIONAL_EXIT', 'OBSERVATION', 'ADVERSARY_CHECK') or not row.get('removed')
                     or row.get('outcome') not in ('EXIT', 'OBSERVED_INTERRUPT')
                     for row in report['audit']))
            report['mode'] = 'BASELINE'
            report['baseline_discrimination'] = ('PASS' if report['outcomes'] == expected
                and not report['errors'] and not report['skipped'] and not infra else 'FAIL')
            report['external_checks'] = {'R1': 'NOT_RUN'}
            report['result'] = 'INCOMPLETE'
            report['limits'] = ['Coleta funcional com executor6; regressões novas, AT de transporte, '
                                'exportação e publicação7 pendentes. Não concede aceite.']
        path, summary = save_run(report, destination)
        print(report['result'], report['baseline_discrimination'], report['tests_run'], path)
        return 1
    except (ValueError, OSError, MemoryError, RecursionError) as error:
        print('Infraestrutura de coleta: ' + str(error), file=sys.stderr)
        return 125


if __name__ == '__main__':
    raise SystemExit(main())
