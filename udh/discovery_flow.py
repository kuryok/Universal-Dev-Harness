"""Orquestra descoberta e encaminhamento local de um snapshot para SPEC."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from .adapters.pi import PiAdapter
from .discovery_session import DiscoveryGap, DiscoverySession
from .domain import CoreError, require_canonical_uuid, require_text, strict_json_loads
from .gateway import GatewayConfig, InferenceGateway
from .policy import policy_v2_sha256
from .runtime import ResourceLimits
from .spec_handoff import snapshot_digest, validate_spec_snapshot
from .transitions import ConflictError
from .trust_store import TrustStore


_SKILL_SHA256 = '0013c3849856ad8f629b9b19a9260be26583a7b715b1ba713016739fa4de7f27'
_CHANGE_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_CONTEXT_LIMIT = 8192
_SKILL_LIMIT = 65536


class DiscoveryFlowError(CoreError):
    """O fluxo não conseguiu produzir ou verificar uma proposta válida."""


@dataclass(frozen=True)
class DiscoveryConfig:
    skill_path: Path
    port: int
    model: str
    trust_store: TrustStore
    cli_path: Path
    node_dir: Path

    def __post_init__(self):
        if (type(self.port) is not int or not 1 <= self.port <= 65535 or type(self.model) is not str
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', self.model)
                or not isinstance(self.trust_store, TrustStore)):
            raise DiscoveryFlowError('Configuração local de descoberta inválida.')
        for name in ('skill_path', 'cli_path', 'node_dir'):
            path = Path(getattr(self, name))
            if not path.is_absolute():
                raise DiscoveryFlowError('Os caminhos confiáveis da descoberta devem ser absolutos.')
            object.__setattr__(self, name, path)


def _skill(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise DiscoveryFlowError('Skill de descoberta deve ser um arquivo regular.')
            raw = stream.read(_SKILL_LIMIT + 1)
    except OSError:
        raise DiscoveryFlowError('Skill de descoberta ausente ou inacessível.') from None
    if len(raw) > _SKILL_LIMIT or hashlib.sha256(raw).hexdigest() != _SKILL_SHA256:
        raise DiscoveryFlowError('Skill de descoberta difere da adaptação aprovada.')
    return raw.decode('utf-8')


def _unique_object(items):
    result = dict(items)
    if len(result) != len(items):
        raise DiscoveryFlowError('Proposta do agente contém campo repetido.')
    return result


def _invalid_json_value(_):
    raise DiscoveryFlowError('Proposta do agente contém constante inválida.')


def _proposal(text):
    try:
        if type(text) is not str or len(text.encode('utf-8')) > 262144:
            raise DiscoveryFlowError('Proposta do agente ausente ou excessiva.')
        obj = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_json_value)
        json.dumps(obj, ensure_ascii=False).encode('utf-8')
    except (ValueError, RecursionError):
        raise DiscoveryFlowError('Proposta do agente não é um objeto JSON válido.') from None
    if type(obj) is not dict:
        raise DiscoveryFlowError('Proposta do agente deve ser um objeto JSON.')
    return obj


def _artifacts(draft):
    if (not {'proposal', 'tasks', 'specs'} <= draft.keys() <= {'proposal', 'tasks', 'specs', 'design'}
            or type(draft['specs']) is not dict):
        raise DiscoveryFlowError('Campos inválidos no rascunho documental.')

    def markdown(text):
        if type(text) is not str:
            raise DiscoveryFlowError('Rascunho documental deve conter textos Markdown.')
        try:
            return text.encode('utf-8')
        except UnicodeError:
            raise DiscoveryFlowError('Rascunho documental deve ser UTF-8.') from None

    return {**{key: markdown(value) for key, value in draft.items() if key != 'specs'},
            'specs': {key: markdown(value) for key, value in draft['specs'].items()}}


class DiscoveryFlow:
    """Operações para o chamador local confiável; estado persistido em SQLite v9."""

    def __init__(self, store, config: DiscoveryConfig):
        if not isinstance(config, DiscoveryConfig):
            raise DiscoveryFlowError('Configuração local de descoberta obrigatória.')
        self._store, self._config = store, config

    @contextmanager
    def _transaction(self, *, write=False):
        with self._store._connection('rw' if write else 'ro') as db:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if self._store._validate_schema(db) != 9:
                raise DiscoveryFlowError('Sessões de descoberta requerem migração explícita para SQLite v9.')
            yield db
            db.commit()

    @staticmethod
    def _load(db, sid):
        require_canonical_uuid(sid, 'ID da sessão de descoberta')
        row = db.execute('SELECT * FROM discovery_sessions WHERE session_id = ?', (sid,)).fetchone()
        if row is None:
            raise DiscoveryFlowError('Sessão de descoberta não encontrada.')
        return row, strict_json_loads(row['payload_json'], 'Sessão de descoberta')

    @staticmethod
    def _state(data):
        return DiscoverySession(data['rounds'], tuple(DiscoveryGap(**gap) for gap in data['gaps']))

    @classmethod
    def _public(cls, row, data, revision=None):
        state = cls._state(data)
        return {**{key: data[key] for key in ('rounds', 'discovery_attempts', 'draft_attempts', 'draft',
                                              'snapshot_sha256', 'approval', 'handoff')},
                'session_id': row['session_id'], 'change_id': row['change_id'],
                'revision': row['revision'] if revision is None else revision,
                'open_gaps': [asdict(gap) for gap in state.open_gaps],
                'ready_for_document_review': state.ready_for_document_review,
                'status': 'SPEC_READY' if data['handoff'] else ('DRAFT' if data['draft'] else 'DISCOVERY')}

    @staticmethod
    def _expect_revision(row, revision):
        if type(revision) is not int or row['revision'] != revision:
            raise ConflictError('Revisão da sessão de descoberta obsoleta.')

    @classmethod
    def _editable(cls, row, data, revision):
        cls._expect_revision(row, revision)
        if data['handoff']:
            raise ConflictError('Sessão já encaminhada para SPEC.')

    @classmethod
    def _save(cls, db, row, data):
        rev = row['revision'] + 1
        db.execute('UPDATE discovery_sessions SET revision = ?, payload_json = ? WHERE session_id = ?',
                   (rev, json.dumps(data, ensure_ascii=False, separators=(',', ':')), row['session_id']))
        return cls._public(row, data, revision=rev)

    def create(self, change_id, idea, *, context=''):
        if type(change_id) is not str or len(change_id) > 64 or not _CHANGE_ID.fullmatch(change_id):
            raise DiscoveryFlowError('ID de change inválido para descoberta.')
        if type(idea) is not str or not idea.strip() or type(context) is not str:
            raise DiscoveryFlowError('Ideia e contexto devem ser textos válidos.')
        try:
            if len(idea.encode('utf-8')) + len(context.encode('utf-8')) > _CONTEXT_LIMIT:
                raise DiscoveryFlowError('Ideia e contexto excedem 8 KiB.')
        except UnicodeError:
            raise DiscoveryFlowError('Ideia e contexto devem ser UTF-8.') from None
        _skill(self._config.skill_path)
        sid = str(uuid4())
        data = dict(idea=idea, context=context, rounds=0, gaps=[], discovery_attempts=0, draft_attempts=0,
                    draft=None, snapshot_sha256=None, draft_revision=None, approval=None, handoff=None)
        with self._transaction(write=True) as db:
            db.execute('INSERT INTO discovery_sessions VALUES (?, ?, ?, ?)', (sid, change_id, 0, json.dumps(data)))
            return self._public(*self._load(db, sid))

    def show(self, sid):
        with self._transaction() as db:
            return self._public(*self._load(db, sid))

    @staticmethod
    def _clear_draft(data):
        data.update(draft=None, snapshot_sha256=None, draft_revision=None, approval=None)

    def _update_answer(self, sid, revision, operation):
        with self._transaction(write=True) as db:
            row, data = self._load(db, sid)
            self._editable(row, data, revision)
            before = self._state(data)
            try:
                after = operation(before)
            except ValueError as error:
                raise DiscoveryFlowError(str(error)) from error
            if after == before:
                return self._public(row, data)
            self._clear_draft(data)
            data['gaps'] = [asdict(gap) for gap in after.gaps]
            return self._save(db, row, data)

    def answer(self, sid, revision, gap_id, answer):
        return self._update_answer(sid, revision, lambda state: state.record_answer(gap_id, answer))

    def confirm(self, sid, revision, gap_id, digest):
        return self._update_answer(sid, revision, lambda state: state.confirm_resolution(gap_id, digest))

    def _invoke(self, sid, key, phase, prompt, skill):
        config = self._config
        invocation_id = f'discovery-{sid}-{key}'
        gateway_config = GatewayConfig(
            run_id=invocation_id, policy_sha256=policy_v2_sha256(),
            audit_path=config.trust_store.audit_path_for(invocation_id), backend='local_discovery',
            allowed_models=(config.model,), local_discovery_port=config.port, max_calls=1, max_concurrency=1,
            call_timeout_seconds=29, max_response_bytes=262144,
            local_discovery_max_tokens=2048 if phase == 'draft' else 512,
        )
        try:
            with tempfile.TemporaryDirectory(prefix='udh-discovery-') as workspace, InferenceGateway(gateway_config) as gw:
                response = PiAdapter().run(
                    workspace=Path(workspace), gateway=gw, prompt=prompt, system_prompt=skill,
                    model=config.model, allowed_tools=(), max_output_tokens=2048 if phase == 'draft' else 512,
                    limits=ResourceLimits(memory_mb=512, pids_limit=96, cpus=1, timeout_seconds=30),
                    forbidden_rw_roots=(config.trust_store.root,),
                )
        except (CoreError, OSError):
            raise DiscoveryFlowError('Não foi possível executar o agente de descoberta.') from None
        if not response.ok:
            raise DiscoveryFlowError('Execução do agente de descoberta incompleta.')
        return _proposal(response.assistant_text)

    def _generate(self, sid, revision, phase):
        skill = _skill(self._config.skill_path)
        is_draft = phase == 'draft'
        counter = 'draft_attempts' if is_draft else 'discovery_attempts'
        with self._transaction(write=True) as db:
            row, data = self._load(db, sid)
            self._editable(row, data, revision)
            state = self._state(data)
            if (data[counter] >= (1 if is_draft else 3) or (not is_draft and data['draft_attempts'])
                    or (is_draft and not state.ready_for_document_review)):
                raise ConflictError('A fase ou o limite da sessão não permite essa invocação.')
            data[counter] += 1
            attempt = self._save(db, row, data)['revision']
            prompt = json.dumps({'phase': phase, 'idea': data['idea'], 'context': data['context'],
                                 'gaps': data['gaps']}, ensure_ascii=False)
        output = self._invoke(sid, attempt, phase, prompt, skill)
        with self._transaction(write=True) as db:
            row, data = self._load(db, sid)
            self._editable(row, data, attempt)
            try:
                if is_draft:
                    digest = snapshot_digest(row['change_id'], _artifacts(output))
                    data.update(draft=output, snapshot_sha256=digest, draft_revision=f'r{attempt + 1}')
                else:
                    updated = self._state(data).apply_round(output)
                    data.update(rounds=updated.rounds, gaps=[asdict(gap) for gap in updated.gaps])
            except ValueError as error:
                raise DiscoveryFlowError('A proposta do agente não atende ao contrato da fase.') from error
            return self._save(db, row, data)

    def discover(self, sid, revision):
        return self._generate(sid, revision, 'discovery')

    def draft(self, sid, revision):
        return self._generate(sid, revision, 'draft')

    def approve(self, sid, revision, snapshot_sha256, approval_ref):
        require_text(approval_ref, 'Referência de aprovação humana')
        if len(approval_ref) > 256:
            raise DiscoveryFlowError('Referência de aprovação humana excede o limite.')
        with self._transaction(write=True) as db:
            row, data = self._load(db, sid)
            self._editable(row, data, revision)
            if (not data['draft'] or not self._state(data).ready_for_document_review
                    or data['snapshot_sha256'] != snapshot_sha256):
                raise ConflictError('Aprovação deve apontar para o rascunho vigente e sem lacunas.')
            data['approval'] = {'ref': approval_ref, 'revision': data['draft_revision'], 'sha256': snapshot_sha256}
            return self._save(db, row, data)

    def handoff(self, sid, revision, request_id):
        require_text(request_id, 'ID da requisição de encaminhamento')
        if type(revision) is not int or len(request_id) > 256:
            raise DiscoveryFlowError('Identidade de encaminhamento inválida.')
        with self._transaction() as db:
            row, data = self._load(db, sid)
            previous = data['handoff']
            if previous is not None:
                if (previous['request_id'], previous['requested_revision']) != (request_id, revision):
                    raise ConflictError('Encaminhamento já registrado para outra requisição.')
                return self._public(row, data)
            self._expect_revision(row, revision)
            approval = data['approval']
            if (not approval or not data['draft'] or not self._state(data).ready_for_document_review
                    or (approval['revision'], approval['sha256']) != (data['draft_revision'], data['snapshot_sha256'])):
                raise ConflictError('Encaminhamento exige um snapshot aprovado, vigente e sem lacunas.')
            identity = (row['change_id'], approval['revision'], approval['sha256'])
            artifacts = _artifacts(data['draft'])
        result = validate_spec_snapshot(identity[0], identity[1], artifacts, approval=identity,
                                        cli_path=self._config.cli_path, node_dir=self._config.node_dir)
        if result.code != 'VALIDATED' or (result.change_id, result.revision, result.snapshot_sha256) != identity:
            raise DiscoveryFlowError(f'Encaminhamento não validado pela CLI: {result.code}.')
        with self._transaction(write=True) as db:
            row, data = self._load(db, sid)
            if data['handoff'] and (data['handoff']['request_id'], data['handoff']['requested_revision']) == (request_id, revision):
                return self._public(row, data)
            self._editable(row, data, revision)
            if (data['approval'] != approval or not self._state(data).ready_for_document_review
                    or snapshot_digest(row['change_id'], _artifacts(data['draft'])) != identity[2]):
                raise ConflictError('Elegibilidade do encaminhamento mudou durante a validação.')
            data['handoff'] = dict(request_id=request_id, requested_revision=revision,
                                   snapshot_sha256=result.snapshot_sha256, cli_version=result.cli_version)
            return self._save(db, row, data)
