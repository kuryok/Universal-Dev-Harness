"""Transporte de AT para o runtime administrativo sintético e privado."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_bridge import candidate_hashes, safe_data_tree
from operator_process import OperatorProcess
from isolated_process import IsolatedProcess
from block_seal import verify_seal


def adversary_probe(core, state, control, config):
    with tempfile.TemporaryDirectory(prefix='block-adversary-', dir=core.parent) as name:
        folder = Path(name)
        candidate, work = folder / 'candidate', folder / 'work'
        shutil.copytree(core, candidate)
        work.mkdir()
        source = Path(__file__).parent / 'block_fixtures/adversary_probe.py'
        (candidate / 'probe.py').write_bytes(source.read_bytes())
        runtime = IsolatedProcess(candidate, work).run('probe.py')
        if runtime['outcome'] != 'EXIT' or runtime['returncode'] != 0 or not runtime['removed']:
            raise ValueError('Sonda adversária não executou com integridade')
        data = json.loads(runtime['stdout'])
        mounts = (runtime.get('inspect') or {}).get('Mounts', [])
        private = (state, control, Path(config['audit']))
        if any(any(path == Path(m['Source']) or path.is_relative_to(Path(m['Source']))
                   for path in private) for m in mounts):
            raise ValueError('Montagem privada exposta ao adversário')
        rejected = {}
        for variant in ('snapshot', 'symlink'):
            base = folder / variant
            base.mkdir()
            changed_core, changed_control, changed_state = (base / part for part in ('core','control','state'))
            shutil.copytree(core, changed_core)
            shutil.copytree(control, changed_control)
            changed_state.mkdir()
            target = changed_core / 'udh/__init__.py'
            if variant == 'snapshot':
                target.write_bytes(target.read_bytes() + b'\n# fixture alterada\n')
            else:
                target.unlink()
                target.symlink_to(core / 'udh/__init__.py')
            try:
                OperatorProcess(changed_core, changed_state, changed_control,
                    core_manifest_sha256=config['core_manifest_sha256'],
                    control_sha256=config['control_sha256'], launcher_sha256=config['launcher_sha256'])
            except ValueError:
                rejected[variant] = True
            else:
                raise ValueError('Adulteração administrativa não foi recusada')
        checks = dict(state_mount_absent=data['absent']['/state'],
            control_mount_absent=data['absent']['/control'] and data['absent']['/observe'],
            runtime_socket_absent=data['absent']['/run/podman/podman.sock'] and data['absent']['/var/run/docker.sock'],
            inherited_admin_fds_absent=not data['extra_fds'] and data['admin_environment_absent'],
            authority_claims_rejected=data['authority_claims_rejected'],
            snapshot_substitution_rejected=rejected['snapshot'], symlink_rejected=rejected['symlink'])
        if not all(checks.values()):
            raise ValueError('Isolamento administrativo não comprovado')
        return dict(checks=checks, runtime_removed=True), runtime


def main():
    record = {'classification': 'INFRA_ERROR', 'bridge_id': uuid.uuid4().hex}
    audit = None
    try:
        config_path = Path(os.environ['UDH_BLOCK_BRIDGE_CONFIG'])
        if config_path.is_symlink():
            raise ValueError('Configuração redirecionada')
        raw = config_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != os.environ['UDH_BLOCK_BRIDGE_CONFIG_SHA256']:
            raise ValueError('Configuração divergente')
        config = json.loads(raw)
        core, state, control, seal, audit = (Path(config[name]) for name in
                                           ('core', 'state', 'control', 'seal', 'audit'))
        paths = [core, state, control, seal, audit]
        for path in paths:
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise ValueError('Diretório inválido')
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for i, a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError('Diretórios de controle sobrepostos')
        verify_seal(seal, config['at_version'])
        if candidate_hashes(core) != config['core_hashes']:
            raise ValueError('Snapshot alterado')
        safe_data_tree(state)
        args = sys.argv[1:]
        seed_core = bool(args and args[0] == '--core')
        if seed_core:
            args = args[1:]
            if not args or args.count('--db') != 1:
                raise ValueError('Rota de fixture inválida')
            index = args.index('--db') + 1
            if index >= len(args) or args[index] != str(state / 'udh.sqlite'):
                raise ValueError('Banco fora da fixture privada')
            del args[index-1:index+1]
        operator = OperatorProcess(core, state, control,
                core_manifest_sha256=config['core_manifest_sha256'],
                control_sha256=config['control_sha256'], launcher_sha256=config['launcher_sha256'])
        presentation = None
        classification = 'FUNCTIONAL_EXIT'
        if args[:1] == ['--probe-adversary']:
            if len(args) != 1 or seed_core:
                raise ValueError('Sonda inválida')
            presentation, result = adversary_probe(core, state, control, config)
            classification = 'ADVERSARY_CHECK'
        elif args[:1] == ['--observe']:
            if seed_core or len(args) < 3:
                raise ValueError('Observação inválida')
            checkpoint = None if args[1] == 'list' else args[1]
            result = operator.run_fault(args[2:], checkpoint=checkpoint, timeout=15)
            record['raw_observation'] = result
            instrumentation = dict(launcher_sha256=result['fault_template_sha256'],
                core_manifest_sha256=config['core_manifest_sha256'], core_bytes_modified=False)
            if checkpoint is None and result['outcome'] == 'EXIT':
                presentation = dict(outcome='OBSERVED_COMPLETE',
                    checkpoints=[item['checkpoint'] for item in result.get('checkpoints', [])],
                    result={key: result[key] for key in ('returncode','stdout','stderr')},
                    instrumentation=instrumentation)
            elif checkpoint is not None and result['outcome'] == 'OBSERVED_INTERRUPT':
                presentation = dict(outcome='OBSERVED_INTERRUPT', checkpoint=checkpoint,
                    marker_received=result.get('marker_received'), process_killed=result.get('process_killed'),
                    runtime_removed=result['removed'], instrumentation=instrumentation,
                    proof=result.get('observation_proof'))
            else:
                raise ValueError('Interrupção sem checkpoint/limpeza comprovados')
            classification = 'OBSERVATION'
        else:
            result = operator.run(args, seed_core=seed_core)
        record.update({key: result.get(key) for key in
                       ('outcome', 'returncode', 'container_name', 'removed', 'command', 'cleanup')})
        record['mounts'] = (result.get('inspect') or {}).get('Mounts', [])
        verify_seal(seal, config['at_version'])
        if candidate_hashes(core) != config['core_hashes']:
            raise ValueError('Snapshot mudou durante execução')
        safe_data_tree(state)
        if result['outcome'] not in ('EXIT', 'OBSERVED_INTERRUPT') or not result['removed']:
            raise ValueError('Execução sem resultado funcional verificável')
        record['classification'] = classification
        if presentation is not None:
            record['evidence'] = presentation
        if presentation is not None:
            print(json.dumps(presentation, ensure_ascii=False))
            return 0
        sys.stdout.write(result['stdout'])
        sys.stderr.write(result['stderr'])
        return result['returncode']
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        record.update(error_type=type(error).__name__, reason=str(error))
        print('Infraestrutura administrativa: ' + str(error), file=sys.stderr)
        return 125
    finally:
        if audit is not None:
            with (audit / (record['bridge_id'] + '.json')).open('x') as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.write('\n')


if __name__ == '__main__':
    raise SystemExit(main())
