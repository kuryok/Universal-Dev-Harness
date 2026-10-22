"""Entrada administrativa do mesmo Core; autoridade depende do isolamento do banco."""
import json
import sys

from .__main__ import Parser
from .domain import CoreError
from .service import CoreService
from .store import SQLiteStore
from .transitions import ConflictError, operator_context_valid


def main(argv=None, *, context=None):
    # Estes dados são contexto de configuração, nunca uma credencial portátil.
    # Só o runtime administrativo recebe o volume do banco operacional.
    if not operator_context_valid(context):
        print(json.dumps(dict(decision='DENY', reason_code='AUTHORIZATION_REQUIRED',
            message='Operação exige a entrada administrativa protegida.',
            next_action='Use o controlador local autorizado.'), ensure_ascii=False))
        return 3
    parser = Parser(prog='python3 -m udh.operator', add_help=False)
    parser.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    commands = parser.add_subparsers(dest='command', required=True, title='comandos')
    migrate = commands.add_parser('migrate', add_help=False, help='migrar banco explicitamente para v2')
    migrate.add_argument('--to', choices=('2',), required=True, help='versão de destino')
    block = commands.add_parser('block', add_help=False, help='registrar pausa de uma execução pendente')
    block.add_argument('--run-id', required=True, help='UUID da execução')
    block.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    block.add_argument('--request-id', required=True, help='chave idempotente do bloqueio')
    block.add_argument('--reason', required=True, help='motivo da pausa')
    args = parser.parse_args(argv)
    try:
        core = CoreService(SQLiteStore(context['db_path']))
        if args.command == 'migrate':
            result = core.migrate()
        else:
            result = core.block(args.run_id, args.expected_revision, args.request_id, args.reason, context=context)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ConflictError as error:
        print('Bloqueio em conflito: ' + str(error), file=sys.stderr)
        return 4
    except (CoreError, OSError) as error:
        print('Não foi possível concluir a operação administrativa: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
