"""Validação estrutural de propostas de tickets do PLAN, sem efeitos externos."""

import re
from heapq import heapify, heappop, heappush


_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_IDENTITY_FIELDS = ('change_id', 'spec_version', 'spec_sha256')


class TicketPlanError(ValueError):
    """A proposta de plano ou a referência aprovada não satisfaz o contrato."""


def _require_object(value, label, *fields):
    if (type(value) is not dict or len(value) != len(fields)
            or any(type(key) is not str or key not in fields for key in value)):
        raise TicketPlanError(f'Campos inválidos em {label}.')


def _require_list(value, label, minimum, maximum):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise TicketPlanError(f'{label} deve ser uma lista com {minimum} a {maximum} itens.')


def _require_identifier(value, label):
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise TicketPlanError(f'Identificador inválido em {label}.')


def _require_text(value, label):
    if type(value) is not str or not 1 <= len(value) <= 512 or value != value.strip():
        raise TicketPlanError(f'Texto inválido em {label}.')


def _identity(value):
    _require_identifier(value['change_id'], 'change_id')
    _require_identifier(value['spec_version'], 'spec_version')
    digest = value['spec_sha256']
    if type(digest) is not str or not _SHA256.fullmatch(digest):
        raise TicketPlanError('SHA-256 inválido em spec_sha256.')
    return tuple(value[field] for field in _IDENTITY_FIELDS)


def _approved_pairs(tasks):
    if type(tasks) is not dict or not 1 <= len(tasks) <= 128:
        raise TicketPlanError('tasks deve ser um mapa com 1 a 128 tarefas.')
    pairs = set()
    for task_id, criteria in tasks.items():
        _require_identifier(task_id, 'tasks')
        _require_list(criteria, 'critérios de tasks', 1, 32)
        for criterion_id in criteria:
            _require_identifier(criterion_id, 'critérios de tasks')
            pair = (task_id, criterion_id)
            if pair in pairs:
                raise TicketPlanError('Critério repetido na mesma tarefa aprovada.')
            pairs.add(pair)
    return pairs


def _normalize_ticket(ticket, approved_pairs):
    _require_object(ticket, 'ticket', 'id', 'objective', 'coverage', 'depends_on', 'checks')
    _require_identifier(ticket['id'], 'id do ticket')
    _require_text(ticket['objective'], 'objective')
    _require_list(ticket['coverage'], 'coverage', 1, 128)
    _require_list(ticket['depends_on'], 'depends_on', 0, 127)
    _require_list(ticket['checks'], 'checks', 1, 16)

    pairs = set()
    for link in ticket['coverage']:
        _require_object(link, 'item de coverage', 'task_id', 'criterion_id')
        _require_identifier(link['task_id'], 'task_id')
        _require_identifier(link['criterion_id'], 'criterion_id')
        pair = (link['task_id'], link['criterion_id'])
        if pair not in approved_pairs or pair in pairs:
            raise TicketPlanError('Vínculo tarefa–critério não aprovado ou repetido no ticket.')
        pairs.add(pair)

    for dependency in ticket['depends_on']:
        _require_identifier(dependency, 'depends_on')
    if len(set(ticket['depends_on'])) != len(ticket['depends_on']):
        raise TicketPlanError('Dependência repetida no ticket.')
    for check in ticket['checks']:
        _require_text(check, 'checks')
    if len(set(ticket['checks'])) != len(ticket['checks']):
        raise TicketPlanError('Verificação repetida no ticket.')

    return {
        'id': ticket['id'],
        'objective': ticket['objective'],
        'coverage': [{'task_id': task_id, 'criterion_id': criterion_id}
                     for task_id, criterion_id in sorted(pairs)],
        'depends_on': sorted(ticket['depends_on']),
        'checks': list(ticket['checks']),
    }, pairs


def _topological_order(tickets):
    dependents = {ticket_id: [] for ticket_id in tickets}
    remaining = {}
    for ticket_id, ticket in tickets.items():
        remaining[ticket_id] = len(ticket['depends_on'])
        for dependency in ticket['depends_on']:
            if dependency not in tickets or dependency == ticket_id:
                raise TicketPlanError('Dependência desconhecida ou autorreferência.')
            dependents[dependency].append(ticket_id)

    eligible = [ticket_id for ticket_id in tickets if remaining[ticket_id] == 0]
    heapify(eligible)
    ordered = []
    while eligible:
        ticket_id = heappop(eligible)
        ordered.append(tickets[ticket_id])
        for dependent in dependents[ticket_id]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heappush(eligible, dependent)
    if len(ordered) != len(tickets):
        raise TicketPlanError('O plano contém ciclo de dependências.')
    return ordered


def validate_ticket_plan(approved_spec: dict, proposal: dict) -> dict:
    """Valida e normaliza o plano usando a referência fornecida pelo chamador confiável."""
    _require_object(approved_spec, 'approved_spec', *_IDENTITY_FIELDS, 'tasks')
    _require_object(proposal, 'proposal', *_IDENTITY_FIELDS, 'tickets')
    approved_identity = _identity(approved_spec)
    proposed_identity = _identity(proposal)
    approved_pairs = _approved_pairs(approved_spec['tasks'])
    _require_list(proposal['tickets'], 'tickets', 1, 128)

    tickets = {}
    covered_pairs = set()
    for ticket in proposal['tickets']:
        normalized, pairs = _normalize_ticket(ticket, approved_pairs)
        ticket_id = normalized['id']
        if ticket_id in tickets:
            raise TicketPlanError('ID local de ticket repetido.')
        tickets[ticket_id] = normalized
        covered_pairs.update(pairs)

    if approved_identity != proposed_identity:
        raise TicketPlanError('A proposta não corresponde à identidade da especificação aprovada.')
    if covered_pairs != approved_pairs:
        raise TicketPlanError('A cobertura de tarefas e critérios aprovados está incompleta.')
    return {**dict(zip(_IDENTITY_FIELDS, proposed_identity)), 'tickets': _topological_order(tickets)}
