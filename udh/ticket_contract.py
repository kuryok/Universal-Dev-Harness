"""Validação de cobertura aprovada para execuções gerenciadas."""

import ast
import os
from pathlib import Path
import stat

from .domain import require_sha256
from .ticket_plan import TicketPlanError, _require_identifier, _require_list, _require_object
from .trust_store import AT_VERSION_RE, TrustStore, TrustStoreError, _read_regular_nofollow


def _is_testcase_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id.endswith('TestCase')
    if isinstance(node, ast.Attribute):
        return node.attr.endswith('TestCase')
    return False


def discover_at_tests(at_dir: Path) -> list[str]:
    """Lê o inventário AST dos ATs selados sem importar os módulos."""
    root = at_dir.resolve()
    expected = set()
    for path in sorted(root.rglob('test_*.py')):
        if not stat.S_ISREG(os.lstat(path).st_mode):
            continue
        rel = path.relative_to(root)
        module = '.'.join(rel.with_suffix('').parts)
        tree = ast.parse(_read_regular_nofollow(path).decode('utf-8'), filename=str(rel))
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and any(_is_testcase_base(base) for base in item.bases):
                for member in item.body:
                    if isinstance(member, ast.FunctionDef) and member.name.startswith('test'):
                        expected.add(f'{module}.{item.name}.{member.name}')
    return sorted(expected)


def _context_ids(values, label):
    _require_list(values, label, 0, 32)
    for value in values:
        _require_identifier(value, label)
    if len(values) != len(set(values)):
        raise TicketPlanError(f'{label} contém identificadores repetidos.')
    return sorted(values)


def normalize_contract(payload, expected_pairs):
    _require_object(payload, 'contrato gerenciado', 'approval_ref', 'at_version', 'at_manifest_sha256',
                    'coverage', 'context_ids', 'allowed_context_ids', 'unresolved_gaps')
    _require_identifier(payload['approval_ref'], 'approval_ref')
    version = payload['at_version']
    if type(version) is not str or not AT_VERSION_RE.fullmatch(version):
        raise TicketPlanError('at_version inválido no contrato.')
    require_sha256(payload['at_manifest_sha256'], 'Hash do manifesto de AT aprovado')
    if type(payload['unresolved_gaps']) is not list or payload['unresolved_gaps']:
        raise TicketPlanError('O contrato exige confirmação de que não há lacunas pendentes.')
    allowed = _context_ids(payload['allowed_context_ids'], 'allowed_context_ids')
    context = _context_ids(payload['context_ids'], 'context_ids')
    if not set(context).issubset(allowed):
        raise TicketPlanError('Contexto fora da lista aprovada.')

    coverage = {}
    _require_list(payload['coverage'], 'coverage', 1, 128)
    for item in payload['coverage']:
        _require_object(item, 'item de cobertura', 'task_id', 'criterion_id', 'test_ids')
        _require_identifier(item['task_id'], 'task_id')
        _require_identifier(item['criterion_id'], 'criterion_id')
        pair = (item['task_id'], item['criterion_id'])
        if pair in coverage or pair not in expected_pairs:
            raise TicketPlanError('Par de cobertura repetido ou não aprovado para o ticket.')
        ids = item['test_ids']
        _require_list(ids, 'test_ids', 1, 16)
        for test_id in ids:
            if (type(test_id) is not str or not 1 <= len(test_id) <= 256
                    or any(not part.isidentifier() for part in test_id.split('.'))
                    or test_id.count('.') < 2 or not test_id.rsplit('.', 1)[1].startswith('test_')):
                raise TicketPlanError('Identificador de teste inválido.')
        if len(ids) != len(set(ids)):
            raise TicketPlanError('Identificador de teste repetido no mesmo par.')
        coverage[pair] = sorted(ids)
    if coverage.keys() != expected_pairs:
        raise TicketPlanError('Cobertura não corresponde a todos os critérios do ticket.')
    return {
        'approval_ref': payload['approval_ref'], 'at_version': version,
        'at_manifest_sha256': payload['at_manifest_sha256'],
        'coverage': [{'task_id': task, 'criterion_id': criterion, 'test_ids': ids}
                     for (task, criterion), ids in sorted(coverage.items())],
        'context_ids': context, 'allowed_context_ids': allowed, 'unresolved_gaps': [],
    }


def verify_contract_seal(contract, trust_store: TrustStore) -> tuple[Path, list[str]]:
    if not isinstance(trust_store, TrustStore):
        raise TrustStoreError('TrustStore confiável é obrigatória para verificar o contrato.')
    try:
        at_dir, _ = trust_store.verify_sealed_at(
            contract['at_version'], expected_manifest_sha256=contract['at_manifest_sha256'],
        )
        tests = discover_at_tests(at_dir)
    except (OSError, SyntaxError, UnicodeError) as error:
        raise TrustStoreError('Não foi possível ler o inventário dos ATs selados.') from error
    inventory = set(tests)
    if any(test not in inventory for item in contract['coverage'] for test in item['test_ids']):
        raise TrustStoreError('Teste indicado na cobertura não consta no inventário selado.')
    return at_dir, tests
