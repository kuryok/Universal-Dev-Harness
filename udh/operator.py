"""Entrada administrativa do mesmo Core; autoridade depende do isolamento do banco."""
import json
import re
import sys
from collections.abc import Mapping

from .__main__ import Parser
from .domain import CoreError
from .service import CoreService
from .store import SQLiteStore


def main(argv=None, *, context=None):
    # Estes dados são contexto de configuração, nunca uma credencial portátil.
    # Só o runtime administrativo recebe o volume do banco operacional.
    if (not isinstance(context, Mapping) or context.get('schema') != 1
            or context.get('db_path') != '/state/udh.sqlite'
            or context.get('controller_route') != 'OPERATOR_LOCAL'
            or context.get('authorization_rule_version') != 1
            or context.get('allowed_operations') != ('MIGRATE_V2', 'BLOCK_PENDING')
            or any(not isinstance(context.get(key), str) or not re.fullmatch('[0-9a-f]{64}', context[key])
                   for key in ('core_manifest_sha256', 'control_sha256'))):
        print(json.dumps(dict(decision='DENY', reason_code='AUTHORIZATION_REQUIRED',
            message='Operação exige a entrada administrativa protegida.',
            next_action='Use o controlador local autorizado.'), ensure_ascii=False))
        return 3
    parser = Parser(prog='python3 -m udh.operator', add_help=False)
    parser.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    commands = parser.add_subparsers(dest='command', required=True, title='comandos')
    migrate = commands.add_parser('migrate', add_help=False, help='migrar banco explicitamente para v2')
    migrate.add_argument('--to', choices=('2',), required=True, help='versão de destino')
    parser.parse_args(argv)
    try:
        result = CoreService(SQLiteStore(context['db_path'])).migrate()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CoreError, OSError) as error:
        print('Não foi possível migrar o banco: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
