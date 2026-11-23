"""Entrada CLI autoritativa do ciclo de vida SQLite v4 no mesmo Core."""
import json
import sys

from .__main__ import Parser
from .domain import (
    CoreError,
    VALID_ACTOR_ROUTES,
    VALID_BLOCK_CATEGORIES,
    VALID_COLLECTOR_ROUTES,
    VALID_EVIDENCE_KINDS,
    VALID_EVIDENCE_VERDICTS,
)
from .service import CoreService
from .store import SQLiteStore
from .transitions import ConflictError


def _parse_json_list_arg(raw: str, field: str) -> tuple:
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise CoreError(f'{field} deve conter uma lista JSON válida.') from error
    if not isinstance(parsed, list):
        raise CoreError(f'{field} deve ser uma lista JSON.')
    return tuple(parsed)


def parser() -> Parser:
    root = Parser(prog='python3 -m udh.lifecycle', add_help=False,
                  description='Migre para SQLite v4 e execute transições, evidências e checkpoints do ciclo de vida.')
    root.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    commands = root.add_subparsers(dest='command', required=True, title='comandos')

    migrate = commands.add_parser('migrate', add_help=False, help='migrar banco v3 explicitamente para v4')
    migrate.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    migrate.add_argument('--db', required=True, help='caminho do banco SQLite')
    migrate.add_argument('--to', choices=('4',), required=True, help='versão de destino (4)')

    start = commands.add_parser('start', add_help=False, help='iniciar execução (PENDING → IN_PROGRESS)')
    start.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    start.add_argument('--db', required=True, help='caminho do banco SQLite')
    start.add_argument('--run-id', required=True, help='UUID canônico da execução')
    start.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    start.add_argument('--request-id', required=True, help='chave idempotente da transição')
    start.add_argument('--actor-route', choices=VALID_ACTOR_ROUTES, default='CORE_LOCAL')

    block = commands.add_parser('block', add_help=False, help='bloquear execução com código ADR-025')
    block.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    block.add_argument('--db', required=True, help='caminho do banco SQLite')
    block.add_argument('--run-id', required=True, help='UUID canônico da execução')
    block.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    block.add_argument('--request-id', required=True, help='chave idempotente do bloqueio')
    block.add_argument('--category', choices=VALID_BLOCK_CATEGORIES, required=True, help='categoria ADR-025 do bloqueio')
    block.add_argument('--reason-code', required=True, help='código estruturado do bloqueio')
    block.add_argument('--reason', required=True, help='descrição do motivo')
    block.add_argument('--next-action', required=True, help='próxima ação segura')
    block.add_argument('--actor-route', choices=VALID_ACTOR_ROUTES, default='OPERATOR_LOCAL')

    unblock = commands.add_parser('unblock', add_help=False, help='desbloquear execução (BLOCKED → PENDING)')
    unblock.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    unblock.add_argument('--db', required=True, help='caminho do banco SQLite')
    unblock.add_argument('--run-id', required=True, help='UUID canônico da execução')
    unblock.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    unblock.add_argument('--request-id', required=True, help='chave idempotente do desbloqueio')
    unblock.add_argument('--reason', required=True, help='motivo do desbloqueio')
    unblock.add_argument('--actor-route', choices=VALID_ACTOR_ROUTES, default='OPERATOR_LOCAL')

    evidence = commands.add_parser('record-evidence', add_help=False, help='registrar referência de evidência oficial')
    evidence.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    evidence.add_argument('--db', required=True, help='caminho do banco SQLite')
    evidence.add_argument('--run-id', required=True, help='UUID canônico da execução')
    evidence.add_argument('--request-id', required=True, help='chave idempotente da evidência')
    evidence.add_argument('--kind', choices=VALID_EVIDENCE_KINDS, required=True)
    evidence.add_argument('--verdict', choices=VALID_EVIDENCE_VERDICTS, required=True)
    evidence.add_argument('--artifact-digest', required=True)
    evidence.add_argument('--evidence-sha256', required=True)
    evidence.add_argument('--collector-route', choices=VALID_COLLECTOR_ROUTES, required=True)
    evidence.add_argument('--summary-ref', required=True)
    evidence.add_argument('--at-version', default=None)
    evidence.add_argument('--commit-sha', default=None)

    complete = commands.add_parser('complete', add_help=False, help='concluir execução com evidência oficial PASS (IN_PROGRESS → DONE)')
    complete.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    complete.add_argument('--db', required=True, help='caminho do banco SQLite')
    complete.add_argument('--run-id', required=True, help='UUID canônico da execução')
    complete.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    complete.add_argument('--request-id', required=True, help='chave idempotente da conclusão')
    complete.add_argument('--evidence-id', required=True, help='UUID da evidência oficial PASS')
    complete.add_argument('--actor-route', choices=VALID_ACTOR_ROUTES, default='TRUSTED_EXECUTOR')

    checkpoint = commands.add_parser('checkpoint', add_help=False, help='registrar checkpoint de recuperação F-03')
    checkpoint.add_argument('-h', '--help', action='help', help='mostrar esta ajuda e sair')
    checkpoint.add_argument('--db', required=True, help='caminho do banco SQLite')
    checkpoint.add_argument('--run-id', required=True, help='UUID canônico da execução')
    checkpoint.add_argument('--expected-revision', required=True, type=int, help='revisão observada')
    checkpoint.add_argument('--request-id', required=True, help='chave idempotente do checkpoint')
    checkpoint.add_argument('--last-step', required=True, help='último passo confirmado')
    checkpoint.add_argument('--base-commit', default=None)
    checkpoint.add_argument('--head-commit', default=None)
    checkpoint.add_argument('--worktree-sha256', default=None)
    checkpoint.add_argument('--evidence-hashes-json', default='[]')
    checkpoint.add_argument('--external-keys-json', default='[]')
    checkpoint.add_argument('--unknown-pendencies-json', default='[]')

    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        core = CoreService(SQLiteStore(args.db))
        if args.command == 'migrate':
            result = core.migrate(int(args.to))
        elif args.command == 'start':
            result = core.start(args.run_id, args.expected_revision, args.request_id, actor_route=args.actor_route)
        elif args.command == 'block':
            result = core.block_v4(
                args.run_id, args.expected_revision, args.request_id,
                category=args.category, reason_code=args.reason_code,
                reason=args.reason, next_action=args.next_action, actor_route=args.actor_route,
            )
        elif args.command == 'unblock':
            result = core.unblock_v4(
                args.run_id, args.expected_revision, args.request_id,
                reason=args.reason, actor_route=args.actor_route,
            )
        elif args.command == 'record-evidence':
            result = core.record_evidence(
                args.run_id, args.request_id, kind=args.kind, verdict=args.verdict,
                artifact_digest=args.artifact_digest, evidence_sha256=args.evidence_sha256,
                collector_route=args.collector_route, summary_ref=args.summary_ref,
                at_version=args.at_version, commit_sha=args.commit_sha,
            )
        elif args.command == 'complete':
            result = core.complete(
                args.run_id, args.expected_revision, args.request_id,
                args.evidence_id, actor_route=args.actor_route,
            )
        else:
            result = core.record_checkpoint(
                args.run_id, args.expected_revision, args.request_id, args.last_step,
                base_commit_sha=args.base_commit, head_commit_sha=args.head_commit,
                worktree_snapshot_sha256=args.worktree_sha256,
                evidence_hashes=_parse_json_list_arg(args.evidence_hashes_json, '--evidence-hashes-json'),
                external_idempotency_keys=_parse_json_list_arg(args.external_keys_json, '--external-keys-json'),
                unknown_pendencies=_parse_json_list_arg(args.unknown_pendencies_json, '--unknown-pendencies-json'),
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ConflictError as error:
        print('Conflito na transição do ciclo de vida: ' + str(error), file=sys.stderr)
        return 4
    except (CoreError, OSError) as error:
        print('Não foi possível concluir a operação do ciclo de vida: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
