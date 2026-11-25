"""Orquestra descoberta e encaminhamento de um snapshot para SPEC (local ou OpenCode Zen gratuito)."""

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import resource
import shutil
import stat
import sys
import tempfile
import time
from uuid import uuid4

from .adapters.pi import PiAdapter
from .discovery_session import DiscoveryGap, DiscoverySession
from .domain import CoreError, require_canonical_uuid, require_text, strict_json_loads
from .gateway import (
    GatewayConfig,
    InferenceGateway,
    OPENCODE_ZEN_PUBLIC_TOKEN,
    list_opencode_zen_free_models,
)
from .policy import policy_v2_sha256
from .runtime import ResourceLimits
from .spec_handoff import snapshot_digest, validate_spec_snapshot
from .transitions import ConflictError
from .trust_store import TrustStore


_SKILL_SHA256 = '0013c3849856ad8f629b9b19a9260be26583a7b715b1ba713016739fa4de7f27'
_CHANGE_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_REASON_CODE_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
_CONTEXT_LIMIT = 8192
_SKILL_LIMIT = 65536
_SUMMARY_MAX_BYTES = 32768
_FORBIDDEN_SUMMARY_DIRS = ('udh', 'runtime', 'tests', 'bootstrap', '.git')
_DEFAULT_SKILL_REL = Path('openspec/changes/discover-plan-tickets/skills/grill-with-docs-udh/SKILL.md')


class DiscoveryFlowError(CoreError):
    """O fluxo não conseguiu produzir ou verificar uma proposta válida."""


@dataclass(frozen=True)
class DiscoveryConfig:
    skill_path: Path
    port: int | None
    model: str
    trust_store: TrustStore
    cli_path: Path
    node_dir: Path
    backend: str = 'local_discovery'
    secret_resolver: Callable[[], str | None] | None = None
    connection_factory: Callable[[], http.client.HTTPConnection] | None = None
    timeout_seconds: int = 30

    def __post_init__(self):
        if (self.backend not in ('local_discovery', 'opencode_zen_free')
                or type(self.model) is not str
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', self.model)
                or not isinstance(self.trust_store, TrustStore)
                or type(self.timeout_seconds) is not int):
            raise DiscoveryFlowError('Configuração de descoberta inválida.')
        if self.backend == 'local_discovery':
            if (type(self.port) is not int or not 1 <= self.port <= 65535
                    or self.secret_resolver is not None or self.connection_factory is not None
                    or not 1 <= self.timeout_seconds <= 60):
                raise DiscoveryFlowError('Configuração local de descoberta inválida.')
        else:
            if (self.port is not None or not self.model.endswith('-free')
                    or not callable(self.secret_resolver)
                    or (self.connection_factory is not None and not callable(self.connection_factory))
                    or not 1 <= self.timeout_seconds <= 90):
                raise DiscoveryFlowError('Configuração opencode_zen_free de descoberta inválida.')
        for name in ('skill_path', 'cli_path', 'node_dir'):
            path = Path(getattr(self, name))
            if not path.is_absolute():
                raise DiscoveryFlowError('Os caminhos confiáveis da descoberta devem ser absolutos.')
            object.__setattr__(self, name, path)

    @classmethod
    def for_opencode_zen_free(
        cls,
        *,
        skill_path: Path,
        model: str,
        trust_store: TrustStore,
        cli_path: Path,
        node_dir: Path,
        secret_resolver: Callable[[], str | None] | None = None,
        connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
        timeout_seconds: int = 60,
    ) -> 'DiscoveryConfig':
        """Construtor explícito para o backend endurecido opencode_zen_free (padrão: token 'public')."""
        resolver = secret_resolver if secret_resolver is not None else (lambda: OPENCODE_ZEN_PUBLIC_TOKEN)
        return cls(
            skill_path=skill_path,
            port=None,
            model=model,
            trust_store=trust_store,
            cli_path=cli_path,
            node_dir=node_dir,
            backend='opencode_zen_free',
            secret_resolver=resolver,
            connection_factory=connection_factory,
            timeout_seconds=timeout_seconds,
        )


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
        is_zen = config.backend == 'opencode_zen_free'
        max_tokens = 2048 if (is_zen or phase == 'draft') else 512
        call_timeout = config.timeout_seconds if is_zen else max(1, config.timeout_seconds - 1)
        container_timeout = (config.timeout_seconds + 15) if is_zen else config.timeout_seconds
        gateway_config = GatewayConfig(
            run_id=invocation_id, policy_sha256=policy_v2_sha256(),
            audit_path=config.trust_store.audit_path_for(invocation_id), backend=config.backend,
            allowed_models=(config.model,), local_discovery_port=config.port, max_calls=1, max_concurrency=1,
            call_timeout_seconds=call_timeout, max_response_bytes=262144,
            local_discovery_max_tokens=max_tokens,
            secret_resolver=config.secret_resolver,
            opencode_zen_connection_factory=config.connection_factory,
        )
        try:
            with tempfile.TemporaryDirectory(prefix='udh-discovery-') as workspace, InferenceGateway(gateway_config) as gw:
                response = PiAdapter().run(
                    workspace=Path(workspace), gateway=gw, prompt=prompt, system_prompt=skill,
                    model=config.model, allowed_tools=(), max_output_tokens=max_tokens,
                    limits=ResourceLimits(memory_mb=512, pids_limit=96, cpus=1, timeout_seconds=container_timeout),
                    forbidden_rw_roots=(config.trust_store.root,),
                )
        except (CoreError, OSError) as error:
            tag = type(error).__name__
            raise DiscoveryFlowError(f'Não foi possível executar o agente de descoberta ({tag}).') from None
        if not response.ok:
            code = getattr(response, 'reason_code', None)
            safe_code = code if isinstance(code, str) and _REASON_CODE_RE.fullmatch(code) else 'AGENT_INCOMPLETE'
            raise DiscoveryFlowError(f'Execução do agente de descoberta incompleta ({safe_code}).')
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


def _resolve_zen_secret(*, use_env_api_key: bool) -> tuple[Callable[[], str | None], str]:
    if not use_env_api_key:
        return (lambda: OPENCODE_ZEN_PUBLIC_TOKEN), 'public'
    token = os.environ.get('UDH_OPENCODE_ZEN_API_KEY') or os.environ.get('OPENCODE_API_KEY')
    if not isinstance(token, str) or not token:
        raise DiscoveryFlowError(
            'Variável de ambiente UDH_OPENCODE_ZEN_API_KEY ou OPENCODE_API_KEY ausente para --use-env-api-key.'
        )
    return (lambda: token), 'env_api_key'


def _validate_summary_out_path(raw_path: Path, *, repo_root: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_symlink():
        raise DiscoveryFlowError('Caminho de --summary-out não pode ser symlink.')
    parent = candidate.parent
    for ancestor in (parent, *parent.parents):
        if ancestor.is_symlink():
            raise DiscoveryFlowError('Diretório pai de --summary-out não pode conter symlink.')
    if not parent.exists() or not parent.is_dir():
        raise DiscoveryFlowError('Diretório pai de --summary-out deve existir.')
    resolved_target = parent.resolve(strict=True) / candidate.name
    root_resolved = repo_root.resolve(strict=True)
    for forbidden in _FORBIDDEN_SUMMARY_DIRS:
        blocked = (root_resolved / forbidden).resolve()
        if resolved_target == blocked or resolved_target.is_relative_to(blocked):
            raise DiscoveryFlowError(f'Caminho de --summary-out proibido sob {forbidden}/.')
    return resolved_target


def _write_summary_file(target: Path, payload: dict) -> int:
    raw = (json.dumps(payload, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    if len(raw) > _SUMMARY_MAX_BYTES:
        raise DiscoveryFlowError(f'Resumo excede o teto de {_SUMMARY_MAX_BYTES} bytes.')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return len(raw)


def run_opencode_zen_trial(
    *,
    model: str,
    use_env_api_key: bool = False,
    timeout_seconds: int = 60,
    summary_out: Path | None = None,
    cli_path: Path | None = None,
    skill_path: Path | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
) -> dict:
    """Executa interativamente os casos de C5-T3b.2b-03 via opencode_zen_free em ambiente temporário isolado."""
    from .service import CoreService
    from .store import SQLiteStore

    repo_root = Path(__file__).resolve().parent.parent
    resolved_skill = (skill_path or (repo_root / _DEFAULT_SKILL_REL)).resolve()
    resolved_cli = cli_path
    if resolved_cli is None:
        found = shutil.which('openspec')
        if not found:
            raise DiscoveryFlowError('Executável openspec não encontrado no PATH.')
        resolved_cli = Path(found).resolve()
    resolved_cli = Path(resolved_cli).resolve()
    secret_resolver, auth_mode = _resolve_zen_secret(use_env_api_key=use_env_api_key)
    validated_out = _validate_summary_out_path(summary_out, repo_root=repo_root) if summary_out is not None else None

    started = time.monotonic()
    cases: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix='udh-zen-trial-') as tmp:
        root = Path(tmp)
        core = CoreService(SQLiteStore(root / 'trial.db'))
        core.initialize(schema_version=4)
        for ver in range(5, 10):
            core.migrate(ver)
        trust = TrustStore(root / 'trust-store')
        config = DiscoveryConfig.for_opencode_zen_free(
            skill_path=resolved_skill,
            model=model,
            trust_store=trust,
            cli_path=resolved_cli,
            node_dir=resolved_cli.parent,
            secret_resolver=secret_resolver,
            connection_factory=connection_factory,
            timeout_seconds=timeout_seconds,
        )
        flow = core.discovery_flow(config)

        # Caso 1: pedido completo sem lacuna material esperada
        c1_idea = (
            'Criar um comando local em Python stdlib chamado tempconv que converte temperatura '
            'de Celsius para Fahrenheit recebendo um único número decimal como argumento posicional '
            '(ex.: tempconv 25), imprime o resultado com 2 casas decimais em stdout e termina com '
            'código 0; para entrada ausente ou não numérica, imprime mensagem de erro em stderr e '
            'termina com código 2, sem persistência, sem rede e sem arquivo de configuração.'
        )
        print_fn('\n=== Caso 1 (ideia completa) ===')
        s1 = flow.create('trial-complete-idea', c1_idea)
        s1 = flow.discover(s1['session_id'], s1['revision'])
        c1_gaps = s1['open_gaps']
        print_fn(f'Lacunas abertas no Caso 1: {len(c1_gaps)}')
        c1_ok = False
        c1_handoff = None
        if not c1_gaps and s1['ready_for_document_review']:
            s1 = flow.draft(s1['session_id'], s1['revision'])
            print_fn(f'Rascunho Caso 1 gerado (snapshot_sha256={s1["snapshot_sha256"]}).')
            print_fn('Specs geradas: ' + ', '.join(sorted(s1['draft']['specs'].keys())))
            confirm_c1 = input_fn('Aprovar rascunho do Caso 1? [s/N]: ').strip().lower()
            if confirm_c1 in ('s', 'sim', 'y', 'yes'):
                s1 = flow.approve(s1['session_id'], s1['revision'], s1['snapshot_sha256'], 'operator-trial-c1')
                s1 = flow.handoff(s1['session_id'], s1['revision'], 'req-trial-c1-handoff')
                c1_handoff = s1['handoff']
                c1_ok = s1['status'] == 'SPEC_READY'
        cases['complete_idea'] = {
            'ok': c1_ok,
            'open_gaps_count': len(c1_gaps),
            'status': s1['status'],
            'handoff': c1_handoff,
        }

        # Caso 2: ideia com lacuna material -> pergunta -> bloqueio sem confirmação -> resposta suficiente -> SPEC_READY
        c2_idea = (
            'Criar uma lista local de tarefas em linha de comando (Python stdlib) que salva e lista '
            'tarefas no computador, mas ainda não foi definido se o usuário pode apagar tarefas '
            'concluídas nem o que deve acontecer quando o arquivo de dados estiver corrompido.'
        )
        print_fn('\n=== Caso 2 (ideia com lacuna material) ===')
        s2 = flow.create('trial-material-gap', c2_idea)
        s2 = flow.discover(s2['session_id'], s2['revision'])
        c2_gaps = s2['open_gaps']
        print_fn(f'Lacunas abertas no Caso 2: {len(c2_gaps)}')
        insufficient_blocked = False
        c2_handoff = None
        c2_ok = False
        if c2_gaps:
            for gap in c2_gaps:
                print_fn(f"  - [{gap['id']}] ({gap['impact']}): {gap['question']} (motivo: {gap['reason']})")
            # Exercita bloqueio com resposta registrada mas não confirmada
            first_gap_id = c2_gaps[0]['id']
            s2 = flow.answer(s2['session_id'], s2['revision'], first_gap_id, 'Resposta provisória insuficiente.')
            try:
                flow.draft(s2['session_id'], s2['revision'])
            except ConflictError:
                insufficient_blocked = True
            for gap in c2_gaps:
                ans = input_fn(f"Resposta suficiente para [{gap['id']}]: ").strip()
                s2 = flow.answer(s2['session_id'], s2['revision'], gap['id'], ans)
                s2 = flow.confirm(
                    s2['session_id'], s2['revision'], gap['id'], hashlib.sha256(ans.encode('utf-8')).hexdigest(),
                )
            s2 = flow.draft(s2['session_id'], s2['revision'])
            print_fn(f'Rascunho Caso 2 gerado (snapshot_sha256={s2["snapshot_sha256"]}).')
            print_fn('Specs geradas: ' + ', '.join(sorted(s2['draft']['specs'].keys())))
            confirm_c2 = input_fn('Aprovar rascunho do Caso 2? [s/N]: ').strip().lower()
            if confirm_c2 in ('s', 'sim', 'y', 'yes'):
                s2 = flow.approve(s2['session_id'], s2['revision'], s2['snapshot_sha256'], 'operator-trial-c2')
                s2 = flow.handoff(s2['session_id'], s2['revision'], 'req-trial-c2-handoff')
                c2_handoff = s2['handoff']
                c2_ok = insufficient_blocked and s2['status'] == 'SPEC_READY'
        cases['material_gap'] = {
            'ok': c2_ok,
            'open_gaps_count': len(c2_gaps),
            'gap_ids': [g['id'] for g in c2_gaps],
            'insufficient_answer_blocked': insufficient_blocked,
            'status': s2['status'],
            'handoff': c2_handoff,
        }

    usage_self = resource.getrusage(resource.RUSAGE_SELF)
    summary = {
        'schema_version': 1,
        'profile': 'opencode_zen_free_gateway_mediated',
        'offline_local_ollama_status': 'PENDING_FUTURE_ENVIRONMENT',
        'backend': 'opencode_zen_free',
        'auth_mode': auth_mode,
        'model': model,
        'skill_sha256': _SKILL_SHA256,
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'peak_rss_kib': usage_self.ru_maxrss,
        'cases': cases,
        'verdict': 'PASS' if all(item['ok'] for item in cases.values()) else 'FAIL',
    }
    if validated_out is not None:
        _write_summary_file(validated_out, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python3 -m udh.discovery_flow',
        description='Ferramenta local do operador para ensaio real de DISC-01 via opencode_zen_free.',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list-free-models', action='store_true', help='Lista modelos -free em opencode.ai/zen/v1/models.')
    group.add_argument('--run-opencode-zen-trial', action='store_true', help='Executa ensaio interativo C5-T3b.2b-03.')
    parser.add_argument('--model', type=str, default=None, help='ID exato do modelo -free do catálogo OpenCode Zen.')
    parser.add_argument('--use-env-api-key', action='store_true',
                        help='Usa UDH_OPENCODE_ZEN_API_KEY ou OPENCODE_API_KEY (falha fechado se ausente; padrão: public).')
    parser.add_argument('--timeout', type=int, default=60, help='Timeout por chamada em segundos (1..90).')
    parser.add_argument('--summary-out', type=Path, default=None, help='Caminho seguro para gravar resumo JSON <= 32 KiB.')
    parser.add_argument('--openspec-cli', type=Path, default=None, help='Caminho absoluto da CLI openspec 2.1.1.')
    args = parser.parse_args(argv)

    try:
        if args.list_free_models:
            models = list_opencode_zen_free_models(timeout_seconds=min(15.0, float(args.timeout)))
            print(json.dumps({'models': models}, indent=2, ensure_ascii=False))
            return 0
        if not args.model:
            raise DiscoveryFlowError('--model <id-free> é obrigatório para --run-opencode-zen-trial.')
        summary = run_opencode_zen_trial(
            model=args.model,
            use_env_api_key=args.use_env_api_key,
            timeout_seconds=args.timeout,
            summary_out=args.summary_out,
            cli_path=args.openspec_cli,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0 if summary['verdict'] == 'PASS' else 1
    except CoreError as error:
        print(f'ERRO: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
