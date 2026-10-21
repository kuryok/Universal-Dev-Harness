"""Interface técnica do Core para inicialização, criação e consulta."""
import argparse
import json
import sys

from .domain import CoreError
from .service import CoreService
from .store import SQLiteStore


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Argumentos inválidos. Use --help para consultar os comandos disponíveis.\n')

    def format_help(self):
        return super().format_help().replace('usage:', 'uso:', 1)

    def format_usage(self):
        return super().format_usage().replace('usage:', 'uso:', 1)


def parser() -> Parser:
    result = Parser(prog='python3 -m udh', add_help=False,
                    description='Crie e consulte o estado inicial de uma execução local.')
    options = result.add_argument_group('opções')
    options.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    commands = result.add_subparsers(dest='command', required=True, title='comandos')
    descriptions = {'init': 'inicializar banco local', 'create': 'criar execução PENDING',
                    'show': 'consultar execução persistida'}
    for command, description in descriptions.items():
        subcommand = commands.add_parser(command, add_help=False, help=description,
                                        description=description)
        group = subcommand.add_argument_group('opções')
        group.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
        group.add_argument('--db', required=True, help='caminho do banco SQLite')
        if command == 'create':
            group.add_argument('--spec-id', required=True, help='identificador da especificação')
            group.add_argument('--spec-version', required=True, help='versão da especificação')
            group.add_argument('--request-id', required=True, help='identificador idempotente da requisição')
        elif command == 'show':
            group.add_argument('--run-id', required=True, help='UUID da execução')
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        core = CoreService(SQLiteStore(args.db))
        if args.command == 'init':
            payload = {'schema_version': core.initialize()}
        elif args.command == 'create':
            payload = core.create(args.spec_id, args.spec_version, args.request_id).to_dict()
        else:
            payload = core.show(args.run_id).to_dict()
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except (CoreError, OSError) as error:
        print('Não foi possível concluir a operação: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
