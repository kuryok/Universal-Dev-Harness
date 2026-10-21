"""Template confiável da entrada administrativa; não pertence ao candidato."""
import hashlib
import json
from pathlib import Path
import runpy
import stat
import sys
from types import MappingProxyType

CORE_MANIFEST_SHA256 = '__CORE_MANIFEST_SHA256__'
CONTROL_SHA256 = '__CONTROL_SHA256__'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def regular(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Caminho de módulo inválido')
    current = Path(root)
    if not stat.S_ISDIR(current.lstat().st_mode):
        raise ValueError('Raiz administrativa inválida')
    for part in path.parts:
        current /= part
        mode = current.lstat().st_mode
        if current == Path(root) / path:
            if not stat.S_ISREG(mode):
                raise ValueError('Módulo não regular')
        elif not stat.S_ISDIR(mode):
            raise ValueError('Diretório de módulo inválido')
    return current.read_bytes()


def validate(core, control, manifest_pin, config_pin):
    """Valida bytes canônicos e inventário exato sem importar o Core."""
    for root in (core, control):
        if not stat.S_ISDIR(Path(root).lstat().st_mode):
            raise ValueError('Raiz administrativa inválida')
    manifest_raw = regular(control, 'manifest.json')
    config_raw = regular(control, 'config.json')
    if digest(manifest_raw) != manifest_pin or digest(config_raw) != config_pin:
        raise ValueError('Pins administrativos divergentes')
    manifest, config = json.loads(manifest_raw), json.loads(config_raw)
    if canonical(manifest) != manifest_raw or canonical(config) != config_raw:
        raise ValueError('Configuração ou manifesto não canônico')
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1):
        raise ValueError('Manifesto administrativo inválido')
    if not isinstance(config, dict) or type(config.get('schema')) is not int:
        raise ValueError('Configuração administrativa inválida')
    if config != {'schema': 1, 'db_path': '/state/udh.sqlite',
                  'allowed_operations': ['MIGRATE_V2', 'BLOCK_PENDING'],
                  'core_manifest_sha256': manifest_pin}:
        raise ValueError('Configuração administrativa inválida')
    files = manifest['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('Inventário de módulos inválido')
    actual = set()
    for entry in Path(core).rglob('*'):
        mode = entry.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('Entrada especial no Core')
        actual.add(entry.relative_to(core).as_posix())
    if actual != set(files):
        raise ValueError('Inventário do Core divergente')
    for name, expected in files.items():
        if not name.startswith('udh/') or not name.endswith('.py'):
            raise ValueError('Arquivo fora dos módulos aprovados')
        if digest(regular(core, name)) != expected:
            raise ValueError('Módulo do Core divergente')
    return config


def main():
    config = validate('/core', '/control', CORE_MANIFEST_SHA256, CONTROL_SHA256)
    sys.dont_write_bytecode = True
    sys.path.insert(0, '/core')
    args = sys.argv[1:]
    if args[:1] == ['--seed-core']:
        sys.argv = ['udh', *args[1:], '--db', config['db_path']]
        runpy.run_module('udh', run_name='__main__')
        return 0
    if not Path('/core/udh/operator.py').is_file():
        print('Operação administrativa indisponível.', file=sys.stderr)
        return 2
    context = MappingProxyType({**config, 'allowed_operations': tuple(config['allowed_operations']),
                               'control_sha256': CONTROL_SHA256,
                               'controller_route': 'OPERATOR_LOCAL',
                               'authorization_rule_version': 1})
    entry = runpy.run_module('udh.operator', run_name='udh._operator_entry')
    return entry['main'](argv=args, context=context)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('Infraestrutura administrativa: ' + str(error), file=sys.stderr)
        raise SystemExit(125)
