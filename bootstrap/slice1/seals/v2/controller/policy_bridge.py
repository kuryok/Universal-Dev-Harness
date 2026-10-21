"""Transporte confiável dos AT; o pacote candidato só executa no contêiner."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

# Este diretório pertence ao snapshot confiável, nunca ao candidato.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_process import IsolatedProcess
from policy_seal import verify_seal


class BridgeError(ValueError):
    """Contrato do controlador ou dados compartilhados inválidos."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_data_tree(root):
    """Dados do candidato não podem redirecionar os leitores SQLite do host."""
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise BridgeError('Raiz de dados inválida')
    count = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            count += 1
            if count > 4096:
                raise BridgeError('Dados excedem limite de entradas')
            mode = (Path(parent) / name).lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise BridgeError('Symlink ou arquivo especial nos dados compartilhados')


def candidate_hashes(root):
    safe_data_tree(root)
    return {str(file.relative_to(root)): digest(file.read_bytes())
            for file in sorted(root.rglob('*')) if file.is_file()}


def main():
    record = {'classification': 'INFRA_ERROR', 'bridge_id': uuid.uuid4().hex}
    audit = None
    try:
        config_path = Path(os.environ['UDH_BRIDGE_CONFIG'])
        if config_path.is_symlink():
            raise BridgeError('Configuração não pode ser symlink')
        raw = config_path.read_bytes()
        if digest(raw) != os.environ['UDH_BRIDGE_CONFIG_SHA256']:
            raise BridgeError('Configuração divergente')
        config = json.loads(raw)
        candidate, work, seal, audit = (Path(config[name]) for name in
                                        ('candidate', 'work', 'seal', 'audit'))
        for path in (candidate, work, seal, audit):
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise BridgeError('Diretório de controle inválido')
        for outside in (seal, audit, config_path):
            if outside.is_relative_to(candidate) or outside.is_relative_to(work):
                raise BridgeError('Controle exposto ao candidato')
        verify_seal(seal, config['at_version'])
        if candidate_hashes(candidate) != config['candidate_hashes']:
            raise BridgeError('Snapshot candidato divergente')
        safe_data_tree(work)
        args = sys.argv[1:]
        if args.count('--db') != 1:
            raise BridgeError('Banco obrigatório e único')
        index = args.index('--db') + 1
        if index >= len(args):
            raise BridgeError('Caminho de banco ausente')
        database = Path(args[index])
        if not database.is_absolute() or database != database.resolve():
            raise BridgeError('Banco precisa de caminho absoluto sem redirecionamento')
        try:
            relative = database.relative_to(work)
        except ValueError as error:
            raise BridgeError('Banco fora do diretório autorizado') from error
        args[index] = str(Path('/work') / relative)
        result = IsolatedProcess(candidate, work).run('_udh_cli.py', args=args, timeout=5)
        info = result.get('inspect') or {}
        record.update(outcome=result['outcome'], returncode=result['returncode'],
                      container_name=result['container_name'], removed=result['removed'],
                      mounts=info.get('Mounts', []),
                      command=result['command'], cleanup=result.get('cleanup'))
        verify_seal(seal, config['at_version'])
        if candidate_hashes(candidate) != config['candidate_hashes']:
            raise BridgeError('Candidato mudou durante execução')
        safe_data_tree(work)
        if result['outcome'] != 'EXIT' or not result['removed']:
            raise BridgeError('Runtime não produziu resultado funcional verificável')
        record['classification'] = 'FUNCTIONAL_EXIT'
        sys.stdout.write(result['stdout'])
        sys.stderr.write(result['stderr'])
        return result['returncode']
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        record.update(error_type=type(error).__name__, reason=str(error))
        print('Infraestrutura do transporte: ' + str(error), file=sys.stderr)
        return 125
    finally:
        if audit is not None:
            destination = audit / (record['bridge_id'] + '.json')
            with destination.open('x') as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.write('\n')


if __name__ == '__main__':
    raise SystemExit(main())
