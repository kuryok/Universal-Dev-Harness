"""Adaptador de agente OpenCode 1.18.35 em imagem OCI dedicada, E3 honesto, E4 e parser JSONL estrito."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from ..domain import CoreError, strict_json_loads
from ..gateway import DEFAULT_STUB_MODEL, GatewayConfig, InferenceGateway
from ..runtime import BindMount, ResourceLimits, RuntimeManager
from ..trust_store import TrustStore
from . import AdapterCapabilities, AgentExecutionResult

DEFAULT_OPENCODE_LIMITS = ResourceLimits(
    memory_mb=768,
    pids_limit=128,
    cpus=1.0,
    timeout_seconds=60.0,
)

OPENCODE_CAPABILITIES = AdapterCapabilities(
    name='opencode',
    version='1.18.35',
    stack_profiles=('python3-stdlib-v1',),
    supports_network_none=True,
    supports_uds_bridge=True,
    profiles=('conformance', 'developer_python_stdlib'),
    supports_zero_tools_conformance=True,
)

OPENCODE_ALL_BUILTINS = (
    'webfetch',
    'websearch',
    'bash',
    'edit',
    'write',
    'read',
    'glob',
    'grep',
    'list',
    'patch',
    'task',
    'skill',
    'todowrite',
    'todoread',
    'question',
)

OPENCODE_DEFAULT_DEVELOPER_TOOLS = ('read', 'write', 'edit', 'glob', 'grep', 'list')
OPENCODE_FORBIDDEN_TOOLS = frozenset({'webfetch', 'websearch', 'task', 'skill'})
OPENCODE_READONLY_CONFORMANCE_CEILING = frozenset({'read', 'glob', 'grep', 'list', 'ls', 'todoread', 'question'})

_BRIDGE_BOOTSTRAP_SCRIPT = '''
set -eu
mkdir -p /run/udh-scratch/home /run/udh-scratch/config /run/udh-scratch/data /run/udh-scratch/cache /run/udh-scratch/state /run/udh-scratch/tmp
rm -f /run/udh-scratch/bridge-ready /run/udh-scratch/bridge-error
"$1" "$2" > /run/udh-scratch/bridge-ready 2>/run/udh-scratch/bridge-error &
bridge_pid=$!
trap 'kill "$bridge_pid" 2>/dev/null || true' EXIT
i=0
while ! grep -q BRIDGE_READY /run/udh-scratch/bridge-ready 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 150 ]; then
    echo "UDH_BRIDGE_START_FAILED" >&2
    cat /run/udh-scratch/bridge-error >&2 || true
    exit 91
  fi
  sleep 0.02
done
shift 2
"$@"
'''


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_opencode_jsonl(stdout: str) -> dict:
    """Parser determinístico do stream JSONL do OpenCode 1.18.35 (nunca confia apenas em exit 0)."""
    events: list[dict] = []
    malformed_lines = 0
    step_finish_reasons: list[str] = []
    error_events: list[dict] = []
    text_parts: list[str] = []
    tool_events: list[dict] = []

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = strict_json_loads(line, 'Evento JSONL do OpenCode')
        except CoreError:
            malformed_lines += 1
            continue
        if not isinstance(parsed, dict):
            malformed_lines += 1
            continue
        events.append(parsed)
        ev_type = parsed.get('type')
        part = parsed.get('part') if isinstance(parsed.get('part'), dict) else {}

        if ev_type == 'error' or parsed.get('error') or part.get('error'):
            error_events.append(parsed)
            continue

        if ev_type == 'text':
            text_val = part.get('text')
            if isinstance(text_val, str):
                text_parts.append(text_val)
        elif ev_type == 'tool_use':
            tool_events.append(parsed)
            state = part.get('state') if isinstance(part.get('state'), dict) else {}
            if state.get('status') == 'error':
                error_events.append(parsed)
        elif ev_type == 'step_finish':
            reason = part.get('reason')
            if isinstance(reason, str) and reason:
                step_finish_reasons.append(reason)
                if reason in ('error', 'aborted', 'unknown'):
                    error_events.append(parsed)
            else:
                error_events.append(parsed)

    last_stop_reason = step_finish_reasons[-1] if step_finish_reasons else None
    return {
        'events_count': len(events),
        'malformed_lines': malformed_lines,
        'step_finishes_count': len(step_finish_reasons),
        'tool_events_count': len(tool_events),
        'last_stop_reason': last_stop_reason,
        'has_error_event': bool(error_events),
        'error_count': len(error_events),
        'assistant_text': ''.join(text_parts),
    }


def _build_opencode_config(
    *,
    profile: str,
    model: str,
    capability_token: str,
    allowed_tools: tuple[str, ...] | None = None,
) -> tuple[dict, tuple[str, ...]]:
    if profile == 'conformance':
        tools_cfg = {'*': False, **{name: False for name in OPENCODE_ALL_BUILTINS}}
        perm_cfg = {'*': 'deny'}
        effective_tools: tuple[str, ...] = ()
    elif profile == 'developer_python_stdlib':
        requested = tuple(allowed_tools) if allowed_tools is not None else OPENCODE_DEFAULT_DEVELOPER_TOOLS
        for tool in requested:
            if not isinstance(tool, str) or not tool or tool.strip() != tool:
                raise CoreError(f'Ferramenta inválida solicitada para OpenCode: {tool!r}.')
            if tool in OPENCODE_FORBIDDEN_TOOLS or tool.startswith('mcp_'):
                raise CoreError(f'Ferramenta proibida no perfil developer do OpenCode: {tool}.')
        requested_set = set(requested)
        tools_cfg = {'*': False, **{name: (name in requested_set) for name in OPENCODE_ALL_BUILTINS}}
        for name in requested:
            tools_cfg[name] = True
        perm_cfg = {'*': 'deny', **{name: 'allow' for name in requested}}
        effective_tools = requested
    else:
        raise CoreError(f'Perfil não suportado pelo adaptador OpenCode: {profile}.')

    payload = {
        'autoupdate': False,
        'share': 'disabled',
        'plugin': [],
        'tools': tools_cfg,
        'permission': perm_cfg,
        'provider': {
            'udh': {
                'npm': '@ai-sdk/openai-compatible',
                'options': {
                    'baseURL': 'http://127.0.0.1:43127/v1',
                    'apiKey': capability_token,
                },
                'models': {
                    model: {'name': 'UDH Mediated Model'},
                },
            }
        },
    }
    return payload, effective_tools


def _base_opencode_env() -> dict[str, str]:
    return {
        'HOME': '/run/udh-scratch/home',
        'XDG_CONFIG_HOME': '/run/udh-scratch/config',
        'XDG_DATA_HOME': '/run/udh-scratch/data',
        'XDG_CACHE_HOME': '/run/udh-scratch/cache',
        'XDG_STATE_HOME': '/run/udh-scratch/state',
        'TMPDIR': '/run/udh-scratch/tmp',
        'OPENCODE_CONFIG': '/udh/agent-config/opencode.json',
        'OPENCODE_DISABLE_PROJECT_CONFIG': 'true',
        'OPENCODE_DISABLE_AUTOUPDATE': 'true',
        'OPENCODE_DISABLE_MODELS_FETCH': 'true',
        'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true',
        'OPENCODE_DISABLE_LSP_DOWNLOAD': 'true',
        'OPENCODE_DISABLE_EXTERNAL_SKILLS': 'true',
        'OPENCODE_DISABLE_CLAUDE_CODE': 'true',
        'UDH_GATEWAY_TIMEOUT_MS': '15000',
    }


def _read_audit_records(audit_path: Path) -> list[dict]:
    if not audit_path.is_file():
        return []
    records: list[dict] = []
    for raw_line in audit_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = strict_json_loads(line, 'Auditoria JSONL do gateway')
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


class OpenCodeAdapter:
    """Adaptador oficial OpenCode 1.18.35 integrado ao RuntimeManager (imagem dedicada) e InferenceGateway."""

    capabilities = OPENCODE_CAPABILITIES

    def __init__(self, *, runtime_manager: RuntimeManager | None = None):
        self.runtime_manager = runtime_manager or RuntimeManager()

    def inspect_effective_config(
        self,
        *,
        workspace: Path,
        profile: str = 'conformance',
        model: str = DEFAULT_STUB_MODEL,
        allowed_tools: tuple[str, ...] | None = None,
        limits: ResourceLimits | None = None,
        forbidden_rw_roots: tuple[Path, ...] = (),
    ) -> dict:
        """Executa `opencode --pure debug config` no sandbox para verificar proteção E4 (inclusive config hostil)."""
        manifest = self.runtime_manager.load_opencode_manifest()
        ws_resolved = Path(workspace).resolve(strict=True)
        tmp_root = Path(tempfile.mkdtemp(prefix='udh-opencode-cfg-'))
        try:
            config_dir = tmp_root / 'agent-config-ro'
            scratch_dir = tmp_root / 'scratch'
            config_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(config_dir, 0o700)
            for sub in ('home', 'config', 'data', 'cache', 'state', 'tmp'):
                d = scratch_dir / sub
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(d, 0o700)

            cfg_payload, _ = _build_opencode_config(
                profile=profile,
                model=model,
                capability_token='UDH_CONFIG_INSPECT_ONLY',
                allowed_tools=allowed_tools,
            )
            cfg_file = config_dir / 'opencode.json'
            cfg_file.write_text(json.dumps(cfg_payload), encoding='utf-8')
            os.chmod(cfg_file, 0o444)
            before_hash = _sha256_file(cfg_file)

            mounts = (
                BindMount(ws_resolved, '/workspace', readonly=False),
                BindMount(config_dir, '/udh/agent-config', readonly=True),
                BindMount(scratch_dir, '/run/udh-scratch', readonly=False),
            )
            res = self.runtime_manager.run_developer(
                command=[manifest.opencode_bin, '--pure', 'debug', 'config'],
                mounts=mounts,
                env=_base_opencode_env(),
                workdir='/workspace',
                limits=limits or DEFAULT_OPENCODE_LIMITS,
                image_ref=manifest.image_ref,
                forbidden_rw_roots=forbidden_rw_roots,
            )
            after_hash = _sha256_file(cfg_file)
            if res.outcome is not None or res.returncode != 0:
                raise CoreError(f'Falha ao inspecionar configuração efetiva do OpenCode (rc={res.returncode}).')
            parsed = strict_json_loads(res.stdout, 'Saída de opencode debug config')
            if not isinstance(parsed, dict):
                raise CoreError('Saída de opencode debug config deve ser objeto JSON.')
            return {
                'config': parsed,
                'config_integrity_ok': before_hash == after_hash,
                'config_sha256': after_hash,
            }
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def run(
        self,
        *,
        workspace: Path,
        gateway: InferenceGateway,
        profile: str = 'conformance',
        prompt: str,
        system_prompt: str = 'Siga exatamente as instruções dentro do sandbox isolado.',
        model: str = DEFAULT_STUB_MODEL,
        limits: ResourceLimits | None = None,
        forbidden_rw_roots: tuple[Path, ...] = (),
        allowed_tools: tuple[str, ...] | None = None,
        on_started=None,
    ) -> AgentExecutionResult:
        if profile not in self.capabilities.profiles:
            raise CoreError(f'Perfil não suportado pelo adaptador OpenCode: {profile}.')
        if gateway.ipc_dir is None or gateway.socket_path is None:
            raise CoreError('Gateway de inferência deve estar iniciado antes de executar o OpenCodeAdapter.')

        manifest = self.runtime_manager.load_opencode_manifest()
        ws_resolved = Path(workspace).resolve(strict=True)

        tmp_root = Path(tempfile.mkdtemp(prefix='udh-opencode-run-'))
        try:
            config_dir = tmp_root / 'agent-config-ro'
            scratch_dir = tmp_root / 'scratch'
            config_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(config_dir, 0o700)
            for sub in ('home', 'config', 'data', 'cache', 'state', 'tmp'):
                d = scratch_dir / sub
                d.mkdir(parents=True, exist_ok=True)
                os.chmod(d, 0o700)

            cfg_payload, effective_tools = _build_opencode_config(
                profile=profile,
                model=model,
                capability_token=gateway.capability_token,
                allowed_tools=allowed_tools,
            )
            cfg_file = config_dir / 'opencode.json'
            cfg_file.write_text(json.dumps(cfg_payload), encoding='utf-8')
            os.chmod(cfg_file, 0o444)
            before_hash = _sha256_file(cfg_file)

            combined_prompt = f'{system_prompt}\n\n{prompt}' if system_prompt else prompt
            oc_cmd = [
                manifest.opencode_bin,
                '--pure',
                'run',
                '--format', 'json',
                '--model', f'udh/{model}',
                combined_prompt,
            ]

            mounts = (
                BindMount(ws_resolved, '/workspace', readonly=False),
                BindMount(config_dir, '/udh/agent-config', readonly=True),
                BindMount(scratch_dir, '/run/udh-scratch', readonly=False),
                BindMount(gateway.ipc_dir, '/ipc', readonly=True),
            )
            container_res = self.runtime_manager.run_developer(
                command=[
                    'sh', '-c', _BRIDGE_BOOTSTRAP_SCRIPT, 'udh-opencode-bootstrap',
                    manifest.node_bin, manifest.bridge_path, *oc_cmd,
                ],
                mounts=mounts,
                env=_base_opencode_env(),
                workdir='/workspace',
                limits=limits or DEFAULT_OPENCODE_LIMITS,
                image_ref=manifest.image_ref,
                on_started=on_started,
                forbidden_rw_roots=forbidden_rw_roots,
            )

            after_hash = _sha256_file(cfg_file)
            config_integrity_ok = (before_hash == after_hash)
            parsed = parse_opencode_jsonl(container_res.stdout)
            bridge_error = 'UDH_BRIDGE_START_FAILED' in container_res.stderr

            ok = (
                container_res.outcome is None
                and container_res.returncode == 0
                and config_integrity_ok
                and not bridge_error
                and parsed['malformed_lines'] == 0
                and parsed['step_finishes_count'] > 0
                and parsed['last_stop_reason'] == 'stop'
                and not parsed['has_error_event']
            )

            if not config_integrity_ok:
                reason_code = 'CONFIG_INTEGRITY_VIOLATION'
            elif bridge_error:
                reason_code = 'BRIDGE_START_FAILED'
            elif container_res.outcome is not None:
                reason_code = f'CONTAINER_{container_res.outcome}'
            elif parsed['malformed_lines'] > 0:
                reason_code = 'AGENT_MALFORMED_STREAM'
            elif parsed['has_error_event'] or parsed['last_stop_reason'] in ('error', 'aborted', 'unknown'):
                reason_code = 'AGENT_STREAM_INTERRUPTED'
            elif container_res.returncode != 0:
                reason_code = 'AGENT_NONZERO_EXIT'
            elif parsed['last_stop_reason'] != 'stop':
                reason_code = 'AGENT_INCOMPLETE_STOP_REASON'
            else:
                reason_code = 'AGENT_RUN_OK'

            return AgentExecutionResult(
                adapter_name=self.capabilities.name,
                adapter_version=manifest.opencode_version,
                profile=profile,
                ok=ok,
                reason_code=reason_code,
                assistant_text=parsed['assistant_text'],
                stop_reason=parsed['last_stop_reason'],
                events_count=parsed['events_count'],
                config_integrity_ok=config_integrity_ok,
                container_result=container_res,
                details={
                    'parsed_stream': parsed,
                    'config_hashes': {'opencode.json': after_hash},
                    'bridge_error': bridge_error,
                    'effective_tools': list(effective_tools),
                    'image_digest': manifest.image_digest,
                },
            )
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def run_conformance(
        self,
        *,
        trust_store: TrustStore,
        policy_sha256: str,
        expected_marker: str = 'UDH_CONFORMANCE_OK',
    ) -> AgentExecutionResult:
        """Executa prova oficial de conformance B1.3/E3 offline via gateway stub (allow_tools=False)."""
        run_id = f'conformance-opencode-{uuid.uuid4().hex[:12]}'
        audit_path = trust_store.audit_path_for(run_id)
        with tempfile.TemporaryDirectory(prefix='udh-opencode-conformance-ws-') as ws_tmp:
            gw_config = GatewayConfig(
                run_id=run_id,
                policy_sha256=policy_sha256,
                audit_path=audit_path,
                backend='stub',
                allowed_models=(DEFAULT_STUB_MODEL,),
                allow_tools=False,
                stub_response_text=expected_marker,
            )
            with InferenceGateway(gw_config) as gw:
                result = self.run(
                    workspace=Path(ws_tmp),
                    gateway=gw,
                    profile='conformance',
                    prompt='Responda ao teste de conformidade.',
                    system_prompt='Teste de conformidade local UDH.',
                    model=DEFAULT_STUB_MODEL,
                    limits=DEFAULT_OPENCODE_LIMITS,
                    forbidden_rw_roots=(trust_store.root,),
                )

        audit_records = _read_audit_records(audit_path)
        observed_tools = sorted({
            name
            for rec in audit_records
            for name in (rec.get('tool_names') or ())
            if isinstance(name, str)
        })
        any_tools_seen = any(bool(rec.get('has_tools')) for rec in audit_records)
        any_tools_denied = any(rec.get('verdict') == 'tools_denied' for rec in audit_records)
        marker_ok = expected_marker in result.assistant_text

        extra_details = {
            **result.details,
            'expected_marker': expected_marker,
            'audit_path': str(audit_path),
            'audit_records_count': len(audit_records),
            'observed_tool_names': observed_tools,
            'conformance_tool_mode': 'zero_tools' if not any_tools_seen else 'e3_tools_leaked',
        }

        if any_tools_seen or any_tools_denied:
            return AgentExecutionResult(
                adapter_name=result.adapter_name,
                adapter_version=result.adapter_version,
                profile=result.profile,
                ok=False,
                reason_code='E3_ZERO_TOOLS_VIOLATION',
                assistant_text=result.assistant_text,
                stop_reason=result.stop_reason,
                events_count=result.events_count,
                config_integrity_ok=result.config_integrity_ok,
                container_result=result.container_result,
                details=extra_details,
            )

        if result.ok and not marker_ok:
            return AgentExecutionResult(
                adapter_name=result.adapter_name,
                adapter_version=result.adapter_version,
                profile=result.profile,
                ok=False,
                reason_code='CONFORMANCE_MARKER_MISMATCH',
                assistant_text=result.assistant_text,
                stop_reason=result.stop_reason,
                events_count=result.events_count,
                config_integrity_ok=result.config_integrity_ok,
                container_result=result.container_result,
                details=extra_details,
            )

        if result.ok and not audit_records:
            return AgentExecutionResult(
                adapter_name=result.adapter_name,
                adapter_version=result.adapter_version,
                profile=result.profile,
                ok=False,
                reason_code='CONFORMANCE_NO_GATEWAY_CALL',
                assistant_text=result.assistant_text,
                stop_reason=result.stop_reason,
                events_count=result.events_count,
                config_integrity_ok=result.config_integrity_ok,
                container_result=result.container_result,
                details=extra_details,
            )

        return AgentExecutionResult(
            adapter_name=result.adapter_name,
            adapter_version=result.adapter_version,
            profile=result.profile,
            ok=result.ok and marker_ok and bool(audit_records),
            reason_code=result.reason_code,
            assistant_text=result.assistant_text,
            stop_reason=result.stop_reason,
            events_count=result.events_count,
            config_integrity_ok=result.config_integrity_ok,
            container_result=result.container_result,
            details=extra_details,
        )
