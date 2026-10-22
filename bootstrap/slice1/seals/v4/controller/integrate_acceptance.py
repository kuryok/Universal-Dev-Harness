"""Integra AT confiáveis ao candidato isolado e publica o selo do bootstrap."""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from cli_bridge import candidate_hashes, safe_data_tree
from seal_version import SOURCE_FILES, SealError, create_seal, verify_seal

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'
REVIEW_PATH = ROOT / 'openspec/changes/slice1-core-state/integration-review.json'
EVIDENCE = BASE / 'evidence/integration.json'
LAUNCHER = '''import runpy, sys
sys.path.insert(0, '/candidate')
sys.argv[0] = 'udh'
runpy.run_module('udh', run_name='__main__')
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


@contextmanager
def environment(values):
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def module_from_seal(seal, version):
    verify_seal(seal, version)
    name = 'trusted_at_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, seal / 'tests/test_core_state.py')
    module = importlib.util.module_from_spec(spec)
    # Somente o teste independente e verificado é importado, nunca o candidato.
    spec.loader.exec_module(module)
    return module


def prepare_candidate(candidate):
    candidate.mkdir()
    hashes = {}
    for name in ('udh/__init__.py', 'udh/__main__.py'):
        source = 'bootstrap/slice1/baseline/' + name
        data = subprocess.run(['git', '-C', str(ROOT), 'show', 'dc848ba:' + source],
                              check=True, capture_output=True, timeout=10).stdout
        require((ROOT / source).read_bytes() == data, 'Stub original mudou')
        destination = candidate / name
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(data)
        hashes[name] = sha(data)
    (candidate / '_udh_cli.py').write_text(LAUNCHER)
    hashes['_udh_cli.py'] = sha(LAUNCHER.encode())
    return hashes


def run_suite(seal, manifest, temporary):
    candidate, work, control = (temporary / name for name in ('candidate', 'work', 'control'))
    hashes = prepare_candidate(candidate)
    work.mkdir()
    control.mkdir()
    audit = control / 'audit'
    audit.mkdir()
    config = {'candidate': str(candidate), 'work': str(work), 'seal': str(seal),
              'at_version': manifest['at_version'], 'audit': str(audit),
              'candidate_hashes': hashes}
    raw = json.dumps(config, sort_keys=True).encode()
    config_path = control / 'config.json'
    config_path.write_bytes(raw)
    prefix = [sys.executable, '-I', '-B', str(seal / 'controller/cli_bridge.py')]
    settings = {'UDH_CANDIDATE_ROOT': str(candidate), 'UDH_TEST_WORK_ROOT': str(work),
                'UDH_CLI_PREFIX_JSON': json.dumps(prefix),
                'UDH_BRIDGE_CONFIG': str(config_path), 'UDH_BRIDGE_CONFIG_SHA256': sha(raw)}
    module = module_from_seal(seal, manifest['at_version'])
    with environment(settings):
        output = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    verify_seal(seal, manifest['at_version'])
    records = [json.loads(file.read_text()) for file in sorted(audit.glob('*.json'))]
    require(records, 'Nenhuma chamada foi auditada externamente')
    summary = {
        'tests_run': result.testsRun,
        'failures': [{'test': test.id(), 'traceback': text} for test, text in result.failures],
        'errors': [{'test': test.id(), 'traceback': text} for test, text in result.errors],
        'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
        'unexpected_successes': len(result.unexpectedSuccesses),
        'audit': records, 'candidate_hashes': hashes,
        'candidate_reference': 'dc848ba', 'text_report': output.getvalue(),
    }
    return summary, module, settings, candidate, work, control


def main():
    sys.dont_write_bytecode = True
    report = {'classification': 'bootstrap-at-integration-not-product-acceptance',
              'run_id': uuid.uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(),
              'criteria': {}, 'limits': [
                  'A8 e idioma pt-BR exigem revisão humana; Core inexistente não foi aceito.',
                  'Selo protegido do candidato contido, não desta sessão/proprietário no host.',
                  'Somente versão inicial; atualização/revogação de versões não implementadas.',
              ]}
    if EVIDENCE.is_file():
        previous_raw = EVIDENCE.read_bytes()
        previous = json.loads(previous_raw)
        report['previous_attempts'] = previous.pop('previous_attempts', []) + [{
            'sha256': sha(previous_raw), 'report': previous,
        }]

    def check(name, action):
        try:
            report['criteria'][name] = {'status': 'PASS', 'evidence': action()}
        except AssertionError as error:
            report['criteria'][name] = {'status': 'FAIL', 'reason': str(error)}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            report['criteria'][name] = {'status': 'INFRA_ERROR', 'reason': str(error)}

    review = json.loads(REVIEW_PATH.read_text()) if REVIEW_PATH.is_file() else None
    with tempfile.TemporaryDirectory(prefix='integration-', dir=BASE) as directory:
        temporary = Path(directory)
        if review is None:
            seal = temporary / 'draft-seal'
        else:
            (BASE / 'seals').mkdir(exist_ok=True)
            seal = BASE / 'seals/v1'
        manifest = create_seal(ROOT, seal, review)
        report.update(at_version=manifest['at_version'], seal_status=manifest['body']['status'],
                      manifest=manifest)
        baseline, module, settings, candidate, work, control = run_suite(seal, manifest, temporary)
        report['baseline'] = baseline

        def i1():
            original = ast.parse((BASE / 'acceptance/test_core_state.py').read_text())
            adapted = ast.parse((seal / 'tests/test_core_state.py').read_text())
            def methods(tree):
                cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                           and node.name == 'CoreStateAcceptance')
                return {node.name: ast.dump(node) for node in cls.body
                        if isinstance(node, ast.FunctionDef)}
            before, after = methods(original), methods(adapted)
            require(set(before) == set(after), 'Métodos dos AT mudaram')
            allowed = {'setUpClass', 'cli'}
            require(all(before[name] == after[name] for name in before if name not in allowed),
                    'Assertiva ou cenário fora do transporte mudou')
            count = sum(name.startswith('test_') for name in after)
            require(count == 10, 'Quantidade de métodos divergente')
            return {'test_methods': count, 'changed_methods': sorted(allowed),
                    'unchanged_assertions_outside_transport': True}

        def i2():
            failed_methods = {item['test'].split(' (')[0].split('.')[-1]
                              for item in baseline['failures']}
            require(baseline['tests_run'] == 10 and len(baseline['failures']) == 18
                    and len(failed_methods) == 10 and not baseline['errors'],
                    'Baseline não reproduziu dez métodos/18 falhas comportamentais sem erros')
            require(not baseline['skipped'] and not baseline['expected_failures']
                    and not baseline['unexpected_successes'], 'Houve exclusão ou resultado inesperado')
            require(all(record['classification'] == 'FUNCTIONAL_EXIT'
                        and record.get('outcome') == 'EXIT' and record.get('removed')
                        for record in baseline['audit']), 'Infraestrutura contou como falha esperada')
            return {'tests_run': 10, 'assertion_failures': 18, 'infrastructure_errors': 0,
                    'all_methods_failed_behaviorally': True, 'product_accepted': False}

        def i3():
            protected = [seal, control]
            for record in baseline['audit']:
                mounts = record['mounts']
                require(len(mounts) == 5, 'Montagens inesperadas')
                for mount in mounts:
                    source = Path(mount['Source'])
                    require(all(not path.is_relative_to(source) for path in protected),
                            'Store ou controle montado no candidato')
                    require(mount['RW'] == (mount['Destination'] == '/work'),
                            'Escrita fora dos dados autorizados')
            safe_data_tree(work)
            link = work / 'synthetic-link'
            link.symlink_to(control / 'config.json')
            try:
                try:
                    safe_data_tree(work)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Symlink nos dados foi aceito')
            finally:
                link.unlink()
            return {'calls_audited': len(baseline['audit']), 'only_work_writable': True,
                    'symlink_data_rejected': True}

        def i4():
            for value in (None, '{}', '[]', '[1]', 'not-json'):
                with environment({**settings, 'UDH_CLI_PREFIX_JSON': value}):
                    try:
                        module.CoreStateAcceptance.setUpClass()
                    except module.InfrastructureError:
                        pass
                    else:
                        raise AssertionError('Prefixo inválido não virou erro de infraestrutura')
            # Mesma CLI de transporte; pin incorreto gera 125, não uma falha funcional.
            with environment({**settings, 'UDH_BRIDGE_CONFIG_SHA256': '0' * 64}):
                module.CoreStateAcceptance.setUpClass()
                case = module.CoreStateAcceptance()
                case.db = work / 'infra-probe.sqlite'
                try:
                    case.cli('show', '--run-id', 'absent')
                except module.InfrastructureError:
                    pass
                else:
                    raise AssertionError('Bridge125 foi aceito como falha comportamental')
            return {'invalid_prefixes_rejected': 5, 'bridge125_is_infrastructure': True}

        def i5():
            require(create_seal(ROOT, seal, review) == manifest, 'Repetição alterou a identidade')
            outcomes = []
            for variant in ('bytes', 'missing', 'symlink', 'extra', 'manifest', 'outside-path'):
                copied = temporary / ('corrupted-' + variant)
                shutil.copytree(seal, copied)
                file = copied / 'tests/test_core_state.py'
                expected = manifest['at_version']
                if variant == 'bytes':
                    file.write_text('cópia sintética alterada\n')
                elif variant == 'missing':
                    file.unlink()
                elif variant == 'symlink':
                    file.unlink()
                    file.symlink_to(seal / 'tests/test_core_state.py')
                elif variant == 'extra':
                    (copied / 'extra.py').write_text('fixture\n')
                elif variant == 'manifest':
                    (copied / 'manifest.json').write_text('{}')
                else:
                    bad = json.loads((copied / 'manifest.json').read_text())
                    bad['body']['files']['../outside'] = '0' * 64
                    canonical = lambda obj: json.dumps(obj, sort_keys=True, separators=(',', ':'),
                                                      ensure_ascii=False).encode()
                    bad['at_version'] = 'sha256:' + sha(canonical(bad['body']))
                    expected = bad['at_version']
                    (copied / 'manifest.json').write_bytes(canonical(bad) + b'\n')
                try:
                    verify_seal(copied, expected)
                except SealError:
                    outcomes.append(variant)
                else:
                    raise AssertionError('Selo adulterado aceito: ' + variant)
            return {'same_bytes_same_identity': True, 'rejected': outcomes}

        def i6():
            body = verify_seal(seal, manifest['at_version'])['body']
            require(body['generation'] == 1 and body['previous_at_version'] is None,
                    'Cadeia inicial inválida')
            return {'generation': 1, 'previous_at_version': None,
                    'published': body['status'] == 'PUBLISHED', 'approval': body['approval']}

        def i7():
            result = subprocess.run([sys.executable, '-B', str(BASE / 'check_boundary.py')],
                                    capture_output=True, text=True, timeout=60)
            boundary = json.loads((BASE / 'evidence/boundary.json').read_text())
            require(result.returncode == 0 and boundary['result'] == 'PASS',
                    'Regressão T1–T8 não passou')
            for source in list(SOURCE_FILES.values()):
                if source.endswith('.py'):
                    ast.parse((ROOT / source).read_text(), filename=source)
            diff = subprocess.run(['git', '-C', str(ROOT), 'diff', '--check'],
                                  capture_output=True, timeout=10)
            require(diff.returncode == 0, 'Diff contém erros')
            return {'boundary_run_id': boundary['run_id'], 'T1_T8': 'PASS',
                    'original_files_preserved': True, 'syntax_diff': 'PASS'}

        for index, action in enumerate((i1, i2, i3, i4, i5, i6, i7), 1):
            check(f'I{index}', action)
        if review is None:
            for name in ('I1', 'I6'):
                if report['criteria'][name]['status'] == 'PASS':
                    report['criteria'][name]['status'] = 'NOT_RUN'
                    report['criteria'][name]['reason'] = 'Verificação local passou; falta revisão/publicação'
        verify_seal(seal, manifest['at_version'])
    report['temporary_removed'] = not temporary.exists()
    report['result'] = 'PASS' if all(item['status'] == 'PASS' for item in report['criteria'].values()) else 'INCOMPLETE_OR_FAILED'
    EVIDENCE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for name, value in report['criteria'].items():
        print(name, value['status'], value.get('reason', ''))
    print('Selo:', report['at_version'], report['seal_status'])
    print('Evidência:', EVIDENCE)
    return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
