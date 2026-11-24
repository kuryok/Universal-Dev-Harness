"""Adaptador de agente Pi 1.2.0 sob contrato declarativo, E4 e parser JSONL estrito."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from ..domain import CoreError, strict_json_loads
from ..gateway import DEFAULT_STUB_MODEL, GatewayConfig, InferenceGateway
from ..runtime import BindMount, DEFAULT_DEVELOPER_LIMITS, ResourceLimits, RuntimeManager
from ..trust_store import TrustStore
from . import AdapterCapabilities, AgentExecutionResult

PI_CAPABILITIES = AdapterCapabilities(
    name='pi',
    version='1.2.0',
    stack_profiles=('python3-stdlib-v1',),
    supports_network_none=True,
    supports_uds_bridge=True,
    profiles=('conformance', 'developer_python_stdlib'),
)

_BRIDGE_BOOTSTRAP_SCRIPT = '''
set -eu
mkdir -p /run/udh-scratch/home /run/udh-scratch/tmp
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


def parse_pi_jsonl(stdout: str) -> dict:
    """Parser determinístico do stream JSONL do Pi 1.2.0 (nunca confia apenas em exit 0)."""
    events: list[dict] = []
    malformed_lines = 0
    assistant_finals: list[dict] = []
    error_events: list[dict] = []
    text_parts: list[str] = []

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = strict_json_loads(line, 'Evento JSONL do Pi')
        except CoreError:
            malformed_lines += 1
            continue
        if not isinstance(parsed, dict):
            malformed_lines += 1
            continue
        events.append(parsed)
        ev_type = parsed.get('type')
        if ev_type == 'error':
            error_events.append(parsed)
            continue

        msg = parsed.get('message')
        if isinstance(msg, dict) and msg.get('role') == 'assistant' and ev_type in ('message_end', 'turn_end'):
            stop_reason = msg.get('stopReason')
            if ev_type == 'message_end':
                assistant_finals.append(msg)
                if stop_reason in ('error', 'aborted') or msg.get('errorMessage'):
                    error_events.append(msg)
                content = msg.get('content')
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str):
                            text_parts.append(part['text'])
            elif stop_reason in ('error', 'aborted') or msg.get('errorMessage'):
                error_events.append(msg)

        if ev_type == 'agent_end' and isinstance(parsed.get('messages'), list):
            for candidate in parsed['messages']:
                if isinstance(candidate, dict) and candidate.get('role') == 'assistant':
                    if candidate.get('stopReason') in ('error', 'aborted') or candidate.get('errorMessage'):
                        error_events.append(candidate)

    last_stop_reason = assistant_finals[-1].get('stopReason') if assistant_finals else None
    return {
        'events_count': len(events),
        'malformed_lines': malformed_lines,
        'assistant_finals_count': len(assistant_finals),
        'last_stop_reason': last_stop_reason,
        'has_error_event': bool(error_events),
        'error_count': len(error_events),
        'assistant_text': ''.join(text_parts),
    }


class PiAdapter:
    """Adaptador oficial Pi 1.2.0 integrado ao RuntimeManager e InferenceGateway."""

    capabilities = PI_CAPABILITIES

    def __init__(self, *, runtime_manager: RuntimeManager | None = None):
        self.runtime_manager = runtime_manager or RuntimeManager()

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
            raise CoreError(f'Perfil não suportado pelo adaptador Pi: {profile}.')
        if gateway.ipc_dir is None or gateway.socket_path is None:
            raise CoreError('Gateway de inferência deve estar iniciado antes de executar o PiAdapter.')

        manifest = self.runtime_manager.load_manifest()
        ws_resolved = Path(workspace).resolve(strict=True)

        tmp_root = Path(tempfile.mkdtemp(prefix='udh-pi-run-'))
        try:
            config_files_dir = tmp_root / 'config-ro'
            config_mount_dir = tmp_root / 'agent-config-mount'
            scratch_dir = tmp_root / 'scratch'
            for directory in (config_files_dir, config_mount_dir, scratch_dir, scratch_dir / 'home', scratch_dir / 'tmp'):
                directory.mkdir(parents=True, exist_ok=True)
                os.chmod(directory, 0o700)

            models_payload = {
                'providers': {
                    'udh': {
                        'baseUrl': 'http://127.0.0.1:43127/v1',
                        'api': 'openai-completions',
                        'apiKey': gateway.capability_token,
                        'models': [{
                            'id': model,
                            'name': 'UDH Mediated Model',
                            'contextWindow': 16000,
                            'maxTokens': 512,
                            'input': ['text'],
                            'reasoning': False,
                        }],
                    }
                }
            }
            settings_payload = {
                'enableInstallTelemetry': False,
                'enableAnalytics': False,
                'httpIdleTimeoutMs': 8000,
                'retry': {'enabled': False, 'provider': {'maxRetries': 0, 'timeoutMs': 8000}},
            }
            (config_files_dir / 'models.json').write_text(json.dumps(models_payload), encoding='utf-8')
            (config_files_dir / 'settings.json').write_text(json.dumps(settings_payload), encoding='utf-8')
            (config_files_dir / 'auth.json').write_text('{}\n', encoding='utf-8')

            before_hashes = {
                name: _sha256_file(config_files_dir / name)
                for name in ('models.json', 'settings.json', 'auth.json')
            }

            pi_cmd = [
                manifest.node_bin,
                manifest.pi_entrypoint,
                '--offline',
                '--no-mcp',
                '--no-extensions',
                '--no-skills',
                '--no-prompt-templates',
                '--no-context-files',
                '--no-themes',
                '--no-approve',
                '--no-session',
                '--provider', 'udh',
                '--model', model,
                '--thinking', 'off',
                '--mode', 'json',
                '--print',
            ]
            if profile == 'conformance':
                pi_cmd.append('--no-tools')
            else:
                tools_list = allowed_tools if allowed_tools is not None else ('read', 'write', 'edit', 'bash')
                if not tools_list:
                    pi_cmd.append('--no-tools')
                else:
                    pi_cmd.extend(['--tools', ','.join(tools_list)])
            pi_cmd.extend(['--system-prompt', system_prompt, prompt])

            mounts = (
                BindMount(ws_resolved, '/workspace', readonly=False),
                BindMount(config_mount_dir, '/udh/agent-config', readonly=False),
                BindMount(config_files_dir / 'models.json', '/udh/agent-config/models.json', readonly=True),
                BindMount(config_files_dir / 'settings.json', '/udh/agent-config/settings.json', readonly=True),
                BindMount(config_files_dir / 'auth.json', '/udh/agent-config/auth.json', readonly=True),
                BindMount(scratch_dir, '/run/udh-scratch', readonly=False),
                BindMount(gateway.ipc_dir, '/ipc', readonly=True),
            )
            env = {
                'HOME': '/run/udh-scratch/home',
                'TMPDIR': '/run/udh-scratch/tmp',
                'PI_CODING_AGENT_DIR': '/udh/agent-config',
                'PI_OFFLINE': '1',
                'PI_TELEMETRY': '0',
                'UDH_GATEWAY_TIMEOUT_MS': '15000',
            }

            container_res = self.runtime_manager.run_developer(
                command=['sh', '-c', _BRIDGE_BOOTSTRAP_SCRIPT, 'udh-pi-bootstrap', manifest.node_bin, manifest.bridge_path, *pi_cmd],
                mounts=mounts,
                env=env,
                workdir='/workspace',
                limits=limits or DEFAULT_DEVELOPER_LIMITS,
                image_ref=manifest.image_ref,
                on_started=on_started,
                forbidden_rw_roots=forbidden_rw_roots,
            )

            after_hashes = {
                name: _sha256_file(config_files_dir / name)
                for name in ('models.json', 'settings.json', 'auth.json')
            }
            config_integrity_ok = (before_hashes == after_hashes)
            parsed = parse_pi_jsonl(container_res.stdout)

            bridge_error = 'UDH_BRIDGE_START_FAILED' in container_res.stderr
            ok = (
                container_res.outcome is None
                and container_res.returncode == 0
                and config_integrity_ok
                and not bridge_error
                and parsed['malformed_lines'] == 0
                and parsed['assistant_finals_count'] > 0
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
            elif parsed['has_error_event'] or parsed['last_stop_reason'] in ('error', 'aborted'):
                reason_code = 'AGENT_STREAM_INTERRUPTED'
            elif container_res.returncode != 0:
                reason_code = 'AGENT_NONZERO_EXIT'
            elif parsed['last_stop_reason'] != 'stop':
                reason_code = 'AGENT_INCOMPLETE_STOP_REASON'
            else:
                reason_code = 'AGENT_RUN_OK'

            return AgentExecutionResult(
                adapter_name=self.capabilities.name,
                adapter_version=self.capabilities.version,
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
                    'config_hashes': after_hashes,
                    'bridge_error': bridge_error,
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
        """Executa prova oficial de conformance B1.3 offline via gateway stub."""
        run_id = f'conformance-{uuid.uuid4().hex[:12]}'
        audit_path = trust_store.audit_path_for(run_id)
        with tempfile.TemporaryDirectory(prefix='udh-conformance-ws-') as ws_tmp:
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
                    limits=ResourceLimits(memory_mb=512, pids_limit=96, cpus=1.0, timeout_seconds=30.0),
                    forbidden_rw_roots=(trust_store.root,),
                )
        marker_ok = expected_marker in result.assistant_text
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
                details={
                    **result.details,
                    'expected_marker': expected_marker,
                    'audit_path': str(audit_path),
                    'conformance_tool_mode': 'zero_tools',
                },
            )
        return AgentExecutionResult(
            adapter_name=result.adapter_name,
            adapter_version=result.adapter_version,
            profile=result.profile,
            ok=result.ok and marker_ok,
            reason_code=result.reason_code,
            assistant_text=result.assistant_text,
            stop_reason=result.stop_reason,
            events_count=result.events_count,
            config_integrity_ok=result.config_integrity_ok,
            container_result=result.container_result,
            details={
                **result.details,
                'expected_marker': expected_marker,
                'audit_path': str(audit_path),
                'conformance_tool_mode': 'zero_tools',
            },
        )
