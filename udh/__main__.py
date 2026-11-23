"""Interface técnica do Core para inicialização, criação, consulta e diagnóstico."""
import argparse
import json
import sys

from .domain import CoreError, VALID_MODES
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
                    description='Crie, consulte e diagnostique o estado de uma execução local.')
    options = result.add_argument_group('opções')
    options.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    commands = result.add_subparsers(dest='command', required=True, title='comandos')
    descriptions = {
        'init': 'inicializar banco local',
        'create': 'criar execução PENDING',
        'show': 'consultar execução persistida',
        'policy': 'consultar política de uma execução',
        'doctor': 'verificar preflight Linux/Podman e integridade profunda do banco',
    }
    for command, description in descriptions.items():
        subcommand = commands.add_parser(command, add_help=False, help=description,
                                         description=description)
        group = subcommand.add_argument_group('opções')
        group.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
        if command == 'doctor':
            group.add_argument('--db', required=False, default=None, help='caminho opcional do banco SQLite para auditoria profunda')
        else:
            group.add_argument('--db', required=True, help='caminho do banco SQLite')
        if command == 'init':
            group.add_argument('--schema-version', choices=('1', '4'), default='1',
                               help='versão do esquema inicial (padrão: 1)')
        elif command == 'create':
            group.add_argument('--spec-id', required=True, help='identificador da especificação')
            group.add_argument('--spec-version', required=True, help='versão da especificação')
            group.add_argument('--request-id', required=True, help='identificador idempotente da requisição')
            group.add_argument('--ticket-id', required=False, default=None, help='identificador opcional do ticket (somente v4)')
            group.add_argument('--mode', choices=VALID_MODES, required=False, default=None,
                               help='modo adaptativo FAST, STANDARD ou DEEP (somente v4)')
        elif command in ('show', 'policy'):
            group.add_argument('--run-id', required=True, help='UUID da execução')
            if command == 'policy':
                group.add_argument('--action', required=True, help='identificador exato da ação')
    return result


def main(argv=None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.command == 'policy' and not args.action.strip():
        arguments.error('Ação da política vazia')
    try:
        if args.command == 'doctor':
            payload = CoreService.doctor(db_path=args.db)
            print(json.dumps(payload, ensure_ascii=False))
            return 3 if payload['overall_status'] == 'NOT_READY' else 0
        core = CoreService(SQLiteStore(args.db))
        if args.command == 'init':
            payload = {'schema_version': core.initialize(schema_version=int(args.schema_version))}
        elif args.command == 'create':
            payload = core.create(
                args.spec_id, args.spec_version, args.request_id,
                ticket_id=args.ticket_id, mode=args.mode,
            ).to_dict()
        elif args.command == 'policy':
            payload = core.policy(args.run_id, args.action)
        else:
            payload = core.show(args.run_id).to_dict()
        print(json.dumps(payload, ensure_ascii=False))
        return 3 if args.command == 'policy' and payload['decision'] == 'DENY' else 0
    except (CoreError, OSError) as error:
        print('Não foi possível concluir a operação: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
