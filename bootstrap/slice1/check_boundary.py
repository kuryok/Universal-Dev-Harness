"""Prova transitória da fronteira; não executa aceite oficial de produto."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from isolated_process import IsolatedProcess, OUTPUT_LIMIT
from trust_snapshot import (
    ALLOWED, REFERENCE, SnapshotError, create_snapshot, digest,
    reference_files, verify_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'
REVIEW = ROOT / 'openspec/changes/slice1-core-state/bootstrap-review.json'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def compact(result):
    """Registra campos relevantes sem repetir saída sintética de inundação."""
    value = dict(result)
    info = value.pop('inspect', None)
    if info:
        config = info.get('HostConfig', {})
        value['inspection'] = {
            'Id': info['Id'], 'State': info['State'], 'Mounts': info['Mounts'],
            'HostConfig': {key: config.get(key) for key in (
                'NetworkMode', 'PidMode', 'ReadonlyRootfs', 'Privileged',
                'CapDrop', 'SecurityOpt', 'UsernsMode', 'Memory', 'PidsLimit',
                'CpuPeriod', 'CpuQuota',
            )},
        }
    for stream in ('stdout', 'stderr'):
        output = value.get(stream, '')
        if len(output) > 1024:
            value[stream] = output[:1024]
            value[stream + '_captured_characters'] = len(output)
            value[stream + '_sha256'] = digest(output.encode())
            value[stream + '_excerpt_only'] = True
    return value


def reviewed_components():
    paths = [BASE / name for name in (
        'trust_snapshot.py', 'isolated_process.py', 'check_boundary.py',
    )] + sorted((BASE / 'fixtures').glob('*.py'))
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}


def independent_review():
    if not REVIEW.is_file() or REVIEW.is_symlink():
        return False, 'Revisão independente ainda não registrada'
    review = json.loads(REVIEW.read_text())
    valid = (review.get('status') == 'PASS'
             and review.get('unresolved_findings') == []
             and bool(review.get('reviewer_context'))
             and review.get('files') == reviewed_components()
             and review.get('host_does_not_import_candidate') is True)
    return valid, review


def main():
    run_id = uuid.uuid4().hex
    report = {
        'classification': 'bootstrap-boundary-not-official-acceptance',
        'run_id': run_id, 'created_at': datetime.now(timezone.utc).isoformat(),
        'source_reference': REFERENCE, 'criteria': {}, 'runs': {},
        'limitations': [
            'Proteção contra o candidato contido; não contra esta sessão com escrita no host.',
            'Snapshot/manifesto transitórios, sem at_version oficial e sem Core de produto.',
            'Runtime usa bibliotecas existentes RO; não certifica uma imagem completa de produto.',
        ],
    }

    def check(name, action):
        try:
            evidence = action()
            report['criteria'][name] = {'status': 'PASS', 'evidence': evidence}
        except AssertionError as error:
            report['criteria'][name] = {'status': 'FAIL', 'reason': str(error)}
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            report['criteria'][name] = {'status': 'INFRA_ERROR', 'reason': str(error)}

    destination = BASE / 'evidence/boundary.json'
    if destination.is_file():
        previous = json.loads(destination.read_text())
        report['previous_attempts'] = previous.get('previous_attempts', []) + [{
            'run_id': previous['run_id'], 'created_at': previous['created_at'],
            'criteria': previous['criteria'],
            'failed_runs': {name: value for name, value in previous.get('runs', {}).items()
                            if value.get('outcome') != 'EXIT' or not value.get('removed')},
            'controller_hashes': previous.get('snapshot_manifest', {}).get('controller', {}),
        }]
    with tempfile.TemporaryDirectory(prefix='boundary-', dir=BASE) as temporary:
        temporary = Path(temporary)
        store, candidate, work = (temporary / name for name in ('store', 'candidate', 'work'))
        candidate.mkdir()
        work.mkdir()
        expected_prior = reference_files(ROOT)
        for name in ('bootstrap/slice1/evidence/baseline.json',
                     'bootstrap/slice1/evidence/baseline-initial.json',
                     'bootstrap/slice1/run_baseline.py'):
            reference = subprocess.run(
                ['git', '-C', str(ROOT), 'show', f'{REFERENCE}:{name}'],
                check=True, capture_output=True, timeout=10,
            ).stdout
            expected_prior[name] = digest(reference)
        manifest, pin = create_snapshot(ROOT, store, run_id)
        report['snapshot_manifest'] = manifest
        report['manifest_sha256'] = pin
        for fixture in sorted((BASE / 'fixtures').glob('*.py')):
            require(not fixture.is_symlink(), 'Fixture não pode ser symlink')
            shutil.copyfile(fixture, candidate / fixture.name)
        require(not (work / 'import-effect').exists(), 'Importação ocorreu durante cópia')

        def execute(label, entrypoint, args=(), timeout=5, on_started=None):
            verify_snapshot(store, pin)
            result = IsolatedProcess(candidate, work).run(
                entrypoint, args=args, timeout=timeout, on_started=on_started,
            )
            report['runs'][label] = compact(result)
            verify_snapshot(store, pin)
            if result['outcome'] == 'INFRA_ERROR' or not result['removed']:
                raise RuntimeError('Falha de infraestrutura ou limpeza: ' + label)
            return result

        shared = {}

        def t1():
            sentinel = temporary / 'trusted-evidence.json'
            sentinel.write_text('{"trusted":true}\n')
            target = store / 'files' / ALLOWED[0]
            targets = [str(target), str(store / 'manifest.json'), str(sentinel)]
            before = {name: digest(Path(name).read_bytes()) for name in targets}
            result = execute('adversary', 'adversary.py', targets)
            shared['adversary'] = result
            payload = json.loads(result['stdout'].splitlines()[0])
            attempts = payload['attempts']
            require(len(attempts) == 6, 'Tentativas absoluta/symlink incompletas')
            require(all(not item['success'] for item in attempts), 'Escrita externa conseguiu acesso')
            require(all(item.get('errno') in (2, 13, 30) for item in attempts), 'Recusa inesperada')
            require(before == {name: digest(Path(name).read_bytes()) for name in targets},
                    'Sentinela externa alterada')
            mounts = result['inspect']['Mounts']
            for mount in mounts:
                source = Path(mount['Source'])
                require(not store.is_relative_to(source) and not sentinel.is_relative_to(source),
                        'Store/evidência acessível em montagem do candidato')
            return {'attempts': attempts, 'hashes_before_after': before,
                    'store_not_mounted': True}

        def t2():
            result = shared.get('adversary') or execute(
                'fake-success', 'adversary.py', [str(temporary / name) for name in ('a', 'b', 'c')],
            )
            fake = json.loads(result['stdout'].splitlines()[-1])
            require(fake.get('accepted') is True and fake.get('status') == 'PASS', 'Fixture não simulou sucesso')
            require(result['returncode'] == 23 and result['outcome'] == 'EXIT',
                    'Veredito confundiu saída inventada com resultado real')
            return {'actual_returncode': 23, 'functional_result': 'FAIL', 'fake_output_ignored': fake}

        def t3():
            marker = work / 'import-effect'
            require(not marker.exists(), 'Efeito de importação apareceu no host antes de executar')
            result = execute('explicit-entrypoint', 'import_effect.py')
            require(result['outcome'] == 'EXIT' and result['returncode'] == 0,
                    'Entrypoint não foi executado no contêiner')
            require(marker.is_file() and result['stdout'].strip() == 'IMPORT_EFFECT_EXECUTED',
                    'Efeito esperado da fixture ausente')
            return {'host_copy_did_not_execute': True, 'container_entrypoint_executed': True}

        def t4():
            outcomes = {}
            for variant in ('bytes', 'missing', 'extra', 'symlink', 'manifest'):
                altered = temporary / ('tampered-' + variant)
                shutil.copytree(store, altered)
                path = altered / 'files' / ALLOWED[0]
                if variant == 'bytes':
                    path.write_bytes(b'fixture alterada\n')
                elif variant == 'missing':
                    path.unlink()
                elif variant == 'extra':
                    (altered / 'files' / 'unexpected.py').write_text('fixture\n')
                elif variant == 'symlink':
                    path.unlink()
                    path.symlink_to(store / 'files' / ALLOWED[0])
                else:
                    (altered / 'manifest.json').write_text('{}')
                blocked_work = temporary / ('blocked-' + variant)
                blocked_work.mkdir()
                launched = False
                try:
                    verify_snapshot(altered, pin)
                    launched = True
                    IsolatedProcess(candidate, blocked_work).run('import_effect.py')
                except SnapshotError:
                    outcomes[variant] = 'POLICY_DENIED_BEFORE_LAUNCH'
                require(not launched and not (blocked_work / 'import-effect').exists(),
                        'Snapshot adulterado alcançou execução')
            return outcomes

        def t5():
            result = execute('timeout', 'hang.py', timeout=2)
            require((work / 'hang-started').is_file(), 'Prazo terminou antes da fixture iniciar')
            require(result['outcome'] == 'TIMEOUT' and result['removed'],
                    'Timeout não removeu execução')
            flooded = execute('output-limit', 'output_flood.py')
            require(flooded['outcome'] == 'OUTPUT_LIMIT' and flooded['removed'],
                    'Limite de saída não encerrou execução')
            require(len(flooded['stdout'].encode()) <= OUTPUT_LIMIT,
                    'Saída acumulada excedeu limite')
            signal_request = {}

            def terminate_contained(info):
                response = subprocess.run(
                    ['podman', 'kill', '--signal=KILL', info['Id']],
                    capture_output=True, text=True, timeout=3,
                )
                require(response.returncode == 0, 'Runtime recusou sinal ao contêiner próprio')
                signal_request.update(container_id=info['Id'], signal='KILL',
                                      runtime_returncode=response.returncode)

            signaled = execute('contained-signal', 'hang.py', on_started=terminate_contained)
            explicit = execute('explicit-high-exit', 'termination.py')
            for observed in (signaled, explicit):
                require(observed['returncode'] == 137
                        and observed['outcome'] == 'TERMINATION_UNKNOWN',
                        'Código ambíguo foi chamado de saída funcional ou sinal comprovado')
            return {'timeout_container_absent': result['cleanup']['exists'],
                    'flood_container_absent': flooded['cleanup']['exists'],
                    'stdout_limit_bytes': OUTPUT_LIMIT,
                    'signal_request': signal_request,
                    'ambiguous_codes': [signaled['returncode'], explicit['returncode']],
                    'ambiguous_outcomes': [signaled['outcome'], explicit['outcome']]}

        def t6():
            marker = 'udh-host-sentinel-' + run_id
            (work / 'host-marker').write_text(marker)
            namespaces = {}
            with subprocess.Popen(
                [sys.executable, '-I', '-c', 'import time;time.sleep(30)', marker],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ) as sentinel:
                try:
                    require(marker.encode() in Path(f'/proc/{sentinel.pid}/cmdline').read_bytes(),
                            'Sentinela host não foi identificada')

                    def inspect_running(info):
                        pid = info['State']['Pid']
                        require(pid > 0 and info['State']['Running'], 'Contêiner não está ativo')
                        for kind in ('pid', 'mnt'):
                            host = os.readlink('/proc/self/ns/' + kind)
                            contained = os.readlink(f'/proc/{pid}/ns/{kind}')
                            namespaces[kind] = {'host': host, 'candidate': contained}
                            require(host != contained, 'Namespace compartilhado: ' + kind)
                        (work / 'release').touch()

                    result = execute('namespaces', 'process_visibility.py', on_started=inspect_running)
                    payload = json.loads(result['stdout'])
                    require(result['outcome'] == 'EXIT' and result['returncode'] == 0,
                            'Fixture de visibilidade falhou')
                    require(payload['matching_pids'] == [] and not payload['read_errors'],
                            'Visibilidade de processos inconclusiva ou host visível')
                    require(sentinel.poll() is None, 'Sentinela host encerrou antes da prova')
                    config = result['inspect']['HostConfig']
                    require(config['NetworkMode'] == 'none' and config['ReadonlyRootfs']
                            and not config['Privileged'], 'Configuração efetiva incompatível')
                    return {'namespaces': namespaces, 'host_sentinel_pid': sentinel.pid,
                            'visibility': payload, 'no_signal_sent_to_verifier': True}
                finally:
                    sentinel.terminate()
                    sentinel.wait(timeout=3)

        def t7():
            result = execute('positive', 'positive.py')
            require(result['outcome'] == 'EXIT' and result['returncode'] == 0,
                    'Controle positivo não terminou com sucesso')
            require(json.loads(result['stdout']) == {'fixture': 'positive', 'status': 'ok'}
                    and result['stderr'] == '', 'Saída do controle positivo divergente')
            return {'returncode': 0, 'payload': json.loads(result['stdout'])}

        def t8():
            for name, expected in expected_prior.items():
                require(not (ROOT / name).is_symlink() and digest((ROOT / name).read_bytes()) == expected,
                        'Arquivo anterior alterado: ' + name)
            for file in [BASE / name for name in ('trust_snapshot.py', 'isolated_process.py',
                                                  'check_boundary.py')] + list((BASE / 'fixtures').glob('*.py')):
                ast.parse(file.read_text(), filename=str(file))
            diff = subprocess.run(['git', '-C', str(ROOT), 'diff', '--check'],
                                  capture_output=True, timeout=10)
            require(diff.returncode == 0, 'git diff --check falhou')
            verify_snapshot(store, pin)
            return {'preserved_files': expected_prior, 'syntax': 'PASS', 'diff_check': 'PASS'}

        for index, action in enumerate((t1, t2, t3, t4, t5, t6, t7, t8), 1):
            check(f'T{index}', action)
        if report['criteria']['T3']['status'] == 'PASS':
            reviewed, record = independent_review()
            report['criteria']['T3']['review'] = record
            if not reviewed:
                report['criteria']['T3']['status'] = 'NOT_RUN'
                report['criteria']['T3']['reason'] = 'Parte executável passou; falta revisão identificada válida'
    report['temporary_store_removed'] = not temporary.exists()
    passed = all(value['status'] == 'PASS' for value in report['criteria'].values())
    report['result'] = 'PASS' if passed else 'INCOMPLETE_OR_FAILED'
    destination = BASE / 'evidence/boundary.json'
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    for name, value in report['criteria'].items():
        print(name, value['status'], value.get('reason', ''))
    print('Evidência:', destination)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
