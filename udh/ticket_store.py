"""Aprovação e persistência dos planos e vínculos com execuções gerenciadas."""

from contextlib import contextmanager
import hashlib
import json

from .domain import CoreError, VALID_MODES, require_canonical_uuid, require_sha256, require_text, strict_json_loads
from .ticket_contract import normalize_contract, verify_contract_seal
from .ticket_plan import (
    TicketPlanError, _IDENTITY_FIELDS, _approved_pairs, _identity, _normalize_ticket,
    _require_identifier, _require_list, _require_object, _topological_order,
    validate_ticket_plan,
)
from .transitions import ConflictError


def _reference(ref):
    _require_object(ref, 'identidade da especificação', *_IDENTITY_FIELDS)
    return _identity(ref)


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise CoreError('Revisão de aprovação inválida.')
    return value


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _encode(data):
    text = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return text, _digest(text)


def _decode(text, digest, key):
    if _digest(text) != digest:
        raise CoreError('Hash do registro do PLAN diverge do conteúdo persistido.')
    data = strict_json_loads(text, 'Registro do PLAN')
    if type(data) is not dict or tuple(data.get(field) for field in _IDENTITY_FIELDS) != key:
        raise CoreError('Identidade do registro persistido do PLAN incompatível.')
    return data


def _checked_ticket_ids(text, digest, key):
    plan = _decode(text, digest, key)
    tickets = plan.get('tickets')
    if type(tickets) is not list or not 1 <= len(tickets) <= 128:
        raise CoreError('Lista de tickets persistida inválida.')
    ids = set()
    for ticket in tickets:
        if type(ticket) is not dict or type(ticket.get('id')) is not str or ticket['id'] in ids:
            raise CoreError('Identificador de ticket persistido ausente ou repetido.')
        ids.add(ticket['id'])
    return ids


def check_managed_integrity(db):
    rows = db.execute('''SELECT m.*, r.spec_id AS run_spec, r.spec_version AS run_version,
                            r.ticket_id AS run_ticket, r.state, h.revision AS head_revision,
                            h.spec_version AS head_version, h.spec_sha256 AS head_sha256,
                            p.plan_json,
                            EXISTS(SELECT 1 FROM transitions AS t WHERE t.run_id = m.run_id
                                   AND t.operation = 'START_RUN') AS started
                         FROM managed_ticket_runs AS m
                         JOIN runs AS r ON r.id = m.run_id
                         JOIN ticket_plans AS p USING (change_id, spec_version, spec_sha256, plan_sha256)
                         LEFT JOIN ticket_plan_heads AS h ON h.change_id = m.change_id''')
    for row in rows:
        key = (row['change_id'], row['spec_version'], row['spec_sha256'])
        if ((row['run_spec'], row['run_version'], row['run_ticket']) != (*key[:2], row['ticket_id'])
                or row['head_revision'] is None or row['approval_revision'] > row['head_revision']
                or (row['approval_revision'] == row['head_revision']
                    and key[1:] != (row['head_version'], row['head_sha256']))
                or row['state'] not in ('PENDING', 'BLOCKED') or row['started']
                or row['ticket_id'] not in _checked_ticket_ids(row['plan_json'], row['plan_sha256'], key)):
            raise CoreError('Vínculo de execução gerenciada incompatível com plano ou ledger.')


_BINDING_FIELDS = (*_IDENTITY_FIELDS, 'approval_revision', 'plan_sha256', 'ticket_id', 'run_id')


def _contract_result(row, binding):
    text, digest = row['contract_json'], row['contract_sha256']
    if _digest(text) != digest:
        raise CoreError('Hash do contrato gerenciado diverge do registro persistido.')
    contract = strict_json_loads(text, 'Contrato gerenciado')
    if type(contract) is not dict or any(contract.get(field) != binding[field] for field in _BINDING_FIELDS):
        raise CoreError('Contrato persistido incompatível com vínculo gerenciado.')
    return {'contract': contract, 'contract_sha256': digest}


def check_contract_integrity(db):
    for row in db.execute('SELECT m.*, c.contract_json, c.contract_sha256 FROM managed_ticket_contracts AS c '
                          'JOIN managed_ticket_runs AS m ON m.run_id = c.run_id'):
        _contract_result(row, row)


def _approved_input(spec):
    _require_object(spec, 'entrada aprovada', *_IDENTITY_FIELDS, 'tasks', 'task_details')
    key = _identity(spec)
    tasks = spec['tasks']
    pairs = _approved_pairs(tasks)
    _require_object(spec['task_details'], 'task_details', *tasks)
    details = {}
    for task, raw in spec['task_details'].items():
        if type(raw) is not dict or raw.get('mode') not in ('direct', 'split'):
            raise TicketPlanError('Modo da tarefa deve ser direct ou split.')
        mode = raw['mode']
        fields = ('objective', 'checks') if mode == 'direct' else ()
        _require_object(raw, 'detalhes da tarefa', 'mode', 'depends_on', *fields)
        _require_list(raw['depends_on'], 'dependências da tarefa', 0, 127)
        for dependency in raw['depends_on']:
            _require_identifier(dependency, 'dependência da tarefa')
        if len(set(raw['depends_on'])) != len(raw['depends_on']):
            raise TicketPlanError('Dependência repetida na tarefa.')
        item = {'mode': mode, 'depends_on': sorted(raw['depends_on'])}
        if mode == 'direct':
            normalized, _ = _normalize_ticket({
                'id': task, 'objective': raw['objective'], 'checks': raw['checks'],
                'coverage': [{'task_id': task, 'criterion_id': c} for c in tasks[task]],
                'depends_on': [],
            }, pairs)
            item.update(objective=normalized['objective'], checks=normalized['checks'])
        details[task] = item
    _topological_order(details)
    return key, {**dict(zip(_IDENTITY_FIELDS, key)), 'tasks': {t: sorted(cs) for t, cs in tasks.items()},
                 'task_details': details}


def _build_plan(spec, proposal):
    ref = {field: spec[field] for field in _IDENTITY_FIELDS}
    approved = {**ref, 'tasks': spec['tasks']}
    pairs = _approved_pairs(spec['tasks'])
    details = spec['task_details']
    groups = {task: [] for task in details}
    tickets = {}
    for task, item in details.items():
        if item['mode'] == 'direct':
            groups[task].append(task)
            tickets[task] = {
                'id': task, 'objective': item['objective'], 'checks': item['checks'], 'depends_on': [],
                'coverage': [{'task_id': task, 'criterion_id': c} for c in spec['tasks'][task]],
            }
    if proposal is not None:
        _require_object(proposal, 'proposta de subdivisão', *_IDENTITY_FIELDS, 'tickets')
        if _identity(proposal) != _identity(approved):
            raise TicketPlanError('Identidade da proposta de subdivisão divergente.')
        _require_list(proposal['tickets'], 'tickets propostos', 1, 128)
        for raw in proposal['tickets']:
            item, coverage = _normalize_ticket(raw, pairs)
            task_ids = {task for task, _ in coverage}
            if len(task_ids) != 1:
                raise TicketPlanError('Um ticket deve cobrir apenas uma tarefa.')
            task = next(iter(task_ids))
            if details[task]['mode'] != 'split' or item['id'] in tickets:
                raise TicketPlanError('Subdivisão não aprovada ou ID de ticket repetido.')
            groups[task].append(item['id'])
            tickets[item['id']] = item
    for task, ids in groups.items():
        internal = set(ids)
        external = {item for prior in details[task]['depends_on'] for item in groups[prior]}
        for ticket_id in ids:
            dependencies = tickets[ticket_id]['depends_on']
            if not internal.issuperset(dependencies):
                raise TicketPlanError('A proposta só pode declarar dependências internas da tarefa.')
            tickets[ticket_id]['depends_on'] = sorted(set(dependencies) | external)
    return validate_ticket_plan(approved, {**ref, 'tickets': list(tickets.values())})


class TicketStore:
    """Mantém o histórico de entrada e plano e o ponteiro vigente no mesmo SQLite."""

    def __init__(self, store):
        self.store = store

    @contextmanager
    def _transaction(self, *, write=False, minimum_version=5):
        with self.store._connection('rw' if write else 'ro') as db:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            try:
                version = self.store._validate_schema(db)
                if version < minimum_version:
                    raise CoreError(f'Operação do PLAN exige banco SQLite v{minimum_version} ou posterior.')
                yield db
                db.commit()
            except TicketPlanError as error:
                db.rollback()
                raise CoreError(str(error)) from error
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _head(db, change_id):
        row = db.execute('SELECT * FROM ticket_plan_heads WHERE change_id = ?', (change_id,)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _require_current(head, key, revision):
        if head is None or _identity(head) != key or head['revision'] != revision:
            raise ConflictError('A identidade ou a revisão aprovada do PLAN mudou.')

    @staticmethod
    def _input(db, key):
        return db.execute('SELECT input_json, input_sha256 FROM ticket_plan_inputs '
                          'WHERE change_id = ? AND spec_version = ? AND spec_sha256 = ?', key).fetchone()

    @staticmethod
    def _plan(db, key):
        return db.execute('SELECT plan_json, plan_sha256 FROM ticket_plans '
                          'WHERE change_id = ? AND spec_version = ? AND spec_sha256 = ?', key).fetchone()

    def approve(self, spec, *, expected=None):
        with self._transaction(write=True) as db:
            key, normalized = _approved_input(spec)
            text, digest = _encode(normalized)
            head = self._head(db, key[0])
            if expected is None:
                if head is not None:
                    raise ConflictError('Já existe uma aprovação para este change.')
            else:
                if type(expected) is not tuple or len(expected) != 2:
                    raise CoreError('Expectativa de aprovação inválida.')
                self._require_current(head, _reference(expected[0]), _revision(expected[1]))
            saved = self._input(db, key)
            if saved is None:
                db.execute('INSERT INTO ticket_plan_inputs '
                           '(change_id, spec_version, spec_sha256, input_json, input_sha256) '
                           'VALUES (?, ?, ?, ?, ?)', (*key, text, digest))
            elif (saved['input_json'], saved['input_sha256']) != (text, digest):
                raise ConflictError('A entrada aprovada desta identidade já está fixada.')
            if head is None:
                db.execute('INSERT INTO ticket_plan_heads (change_id, spec_version, spec_sha256, revision) '
                           'VALUES (?, ?, ?, 1)', key)
            elif _identity(head) != key:
                db.execute('UPDATE ticket_plan_heads SET spec_version = ?, spec_sha256 = ?, '
                           'revision = revision + 1, active_plan_sha256 = NULL WHERE change_id = ?',
                           (key[1], key[2], key[0]))
            return self._head(db, key[0])

    def materialize(self, ref, *, expected_revision, proposal=None):
        with self._transaction(write=True) as db:
            key, revision = _reference(ref), _revision(expected_revision)
            saved = self._input(db, key)
            if saved is None:
                raise ConflictError('Não há entrada aprovada para esta identidade.')
            spec = _decode(saved['input_json'], saved['input_sha256'], key)
            plan = _build_plan(spec, proposal)
            text, digest = _encode(plan)
            existing = self._plan(db, key)
            if existing is None:
                self._require_current(self._head(db, key[0]), key, revision)
                db.execute('INSERT INTO ticket_plans (change_id, spec_version, spec_sha256, plan_json, plan_sha256) '
                           'VALUES (?, ?, ?, ?, ?)', (*key, text, digest))
            elif (existing['plan_json'], existing['plan_sha256']) != (text, digest):
                raise ConflictError('Já existe outro plano para a mesma identidade aprovada.')
            return {'plan': plan, 'plan_sha256': digest}

    def reconcile(self, ref, *, expected_revision, plan_sha256):
        with self._transaction(write=True) as db:
            key, revision = _reference(ref), _revision(expected_revision)
            self._require_current(self._head(db, key[0]), key, revision)
            row = self._plan(db, key)
            if row is None or type(plan_sha256) is not str or row['plan_sha256'] != plan_sha256:
                raise ConflictError('O digest não identifica o plano persistido vigente.')
            _decode(row['plan_json'], plan_sha256, key)
            db.execute('UPDATE ticket_plan_heads SET active_plan_sha256 = ? WHERE change_id = ?',
                       (plan_sha256, key[0]))
            return self._head(db, key[0])

    def create_managed_run(self, ref, *, approval_revision, plan_sha256, ticket_id, request_id, mode,
                           run_id, created_at):
        with self._transaction(write=True, minimum_version=6) as db:
            key, revision = _reference(ref), _revision(approval_revision)
            _require_identifier(ticket_id, 'ticket_id')
            require_sha256(plan_sha256, 'Digest do plano')
            require_text(request_id, 'Identificador da requisição')
            if type(mode) is not str or mode not in VALID_MODES:
                raise CoreError('Modo adaptativo inválido; use FAST, STANDARD ou DEEP.')

            existing = db.execute('''SELECT r.id, r.mode, m.change_id, m.spec_version, m.spec_sha256,
                                            m.approval_revision, m.ticket_id, m.plan_sha256
                                     FROM runs AS r LEFT JOIN managed_ticket_runs AS m ON m.run_id = r.id
                                     WHERE r.request_id = ?''', (request_id,)).fetchone()
            binding = (*key, revision, ticket_id, plan_sha256)
            if existing is not None:
                if tuple(existing)[1:] != (mode, *binding):
                    raise ConflictError('Requisição já utilizada com outro vínculo ou modo de execução.')
                return self.store._load_v4(db, existing['id'])

            head = self._head(db, key[0])
            self._require_current(head, key, revision)
            if head['active_plan_sha256'] != plan_sha256:
                raise ConflictError('O digest não identifica o plano ativo da revisão aprovada.')
            row = self._plan(db, key)
            if row is None or row['plan_sha256'] != plan_sha256:
                raise CoreError('Plano ativo não corresponde ao registro persistido.')
            if ticket_id not in _checked_ticket_ids(row['plan_json'], plan_sha256, key):
                raise ConflictError('Ticket não encontrado no plano ativo.')
            if db.execute('''SELECT 1 FROM managed_ticket_runs WHERE change_id = ? AND spec_version = ?
                             AND spec_sha256 = ? AND approval_revision = ? AND ticket_id = ?''',
                          binding[:-1]).fetchone() is not None:
                raise ConflictError('Já existe uma execução gerenciada para este ticket e revisão.')

            self.store._insert_ledger_run(db, run_id=run_id, request_id=request_id, spec_id=key[0],
                                          spec_version=key[1], created_at=created_at, ticket_id=ticket_id, mode=mode)
            db.execute('''INSERT INTO managed_ticket_runs (change_id, spec_version, spec_sha256,
                          approval_revision, ticket_id, plan_sha256, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (*binding, run_id))
            return self.store._load_v4(db, run_id)

    @staticmethod
    def _contract(db, run_id):
        return db.execute('SELECT contract_json, contract_sha256 FROM managed_ticket_contracts WHERE run_id = ?',
                          (run_id,)).fetchone()

    @staticmethod
    def _binding(db, run_id):
        return db.execute('SELECT * FROM managed_ticket_runs WHERE run_id = ?', (run_id,)).fetchone()

    @classmethod
    def bound_contract(cls, db, version, run, *, require_current):
        """Lê e confere o contrato dentro da transação do chamador."""
        binding = cls._binding(db, run.run_id) if version >= 6 else None
        if binding is None:
            return None
        saved = cls._contract(db, run.run_id) if version >= 7 else None
        if saved is None:
            raise ConflictError('Execução gerenciada sem contrato protegido.')
        key = tuple(binding[field] for field in _IDENTITY_FIELDS)
        if (run.spec_id, run.spec_version, run.ticket_id) != (*key[:2], binding['ticket_id']):
            raise CoreError('Vínculo gerenciado incompatível com a execução.')
        if require_current:
            head = cls._head(db, key[0])
            cls._require_current(head, key, binding['approval_revision'])
            if head['active_plan_sha256'] != binding['plan_sha256']:
                raise ConflictError('O contrato exige o plano ativo da revisão aprovada.')
        return _contract_result(saved, binding)

    def verification_contract(self, run_id, *, expected_revision):
        with self._transaction(minimum_version=4) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            run = self.store._load_v4(db, run_id)
            if run.state != 'IN_PROGRESS' or run.revision != expected_revision or run.has_unknown_pendencies:
                raise ConflictError('Execução mudou antes da verificação oficial.')
            return self.bound_contract(db, version, run, require_current=True)

    def register_contract(self, run_id, *, expected_revision, contract, trust_store):
        with self._transaction(write=True, minimum_version=7) as db:
            require_canonical_uuid(run_id, 'Identificador da execução')
            if type(expected_revision) is not int or not 0 <= expected_revision <= 2**63 - 1:
                raise CoreError('Revisão esperada da execução inválida.')
            binding = self._binding(db, run_id)
            if binding is None:
                raise ConflictError('A execução não possui vínculo gerenciado.')
            run = self.store._load_v4(db, run_id)
            if run.state != 'PENDING' or run.revision != expected_revision:
                raise ConflictError('Registro exige execução PENDING na revisão esperada.')
            key = tuple(binding[field] for field in _IDENTITY_FIELDS)
            head = self._head(db, key[0])
            self._require_current(head, key, binding['approval_revision'])
            if head['active_plan_sha256'] != binding['plan_sha256']:
                raise ConflictError('O contrato exige o plano ativo da revisão aprovada.')

            row = self._plan(db, key)
            source = self._input(db, key)
            if (row is None or row['plan_sha256'] != binding['plan_sha256'] or source is None
                    or (run.spec_id, run.spec_version, run.ticket_id) != (*key[:2], binding['ticket_id'])):
                raise CoreError('Plano, entrada aprovada ou vínculo inválido para o contrato.')
            spec = _decode(source['input_json'], source['input_sha256'], key)
            plan = _decode(row['plan_json'], row['plan_sha256'], key)
            validated = validate_ticket_plan({**dict(zip(_IDENTITY_FIELDS, key)), 'tasks': spec.get('tasks')}, plan)
            ticket = next((item for item in validated['tickets'] if item['id'] == binding['ticket_id']), None)
            if ticket is None:
                raise CoreError('Ticket ausente do plano aprovado.')
            pairs = {(item['task_id'], item['criterion_id']) for item in ticket['coverage']}
            content = {**{field: binding[field] for field in _BINDING_FIELDS}, **normalize_contract(contract, pairs)}
            text, digest = _encode(content)
            if len(text.encode('utf-8')) > 65536:
                raise CoreError('O contrato excede 64 KiB.')
            saved = self._contract(db, run_id)
            if saved is not None and (saved['contract_json'], saved['contract_sha256']) != (text, digest):
                raise ConflictError('Já existe outro contrato para esta execução gerenciada.')
            verify_contract_seal(content, trust_store)
            if saved is None:
                db.execute('INSERT INTO managed_ticket_contracts (run_id, contract_json, contract_sha256) '
                           'VALUES (?, ?, ?)', (run_id, text, digest))
            return {'contract': content, 'contract_sha256': digest}

    def get_contract(self, run_id):
        with self._transaction(minimum_version=7) as db:
            require_canonical_uuid(run_id, 'Identificador da execução')
            saved = self._contract(db, run_id)
            if saved is None:
                return None
            binding = self._binding(db, run_id)
            if binding is None:
                raise CoreError('Contrato sem vínculo gerenciado.')
            return _contract_result(saved, binding)

    def get_plan(self, ref):
        with self._transaction() as db:
            key = _reference(ref)
            row = self._plan(db, key)
            if row is None:
                return None
            digest = row['plan_sha256']
            return {'plan': _decode(row['plan_json'], digest, key), 'plan_sha256': digest}

    def get_head(self, change_id):
        with self._transaction() as db:
            _require_identifier(change_id, 'change_id')
            return self._head(db, change_id)
