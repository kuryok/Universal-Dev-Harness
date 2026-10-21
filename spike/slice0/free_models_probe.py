"""Exercita inferência gratuita real, mantendo chaves fora dos contêineres."""
import json
from pathlib import Path
import subprocess
import sys
import time

import agent_probe as profile
from free_gateway import UPSTREAM_MODEL

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'evidence/free-models-probe.json'
PHASE = profile.FIXTURES / 'real-phase.json'
FIRST_PART = profile.FIXTURES / 'real-first-part'
profile.OUTPUT = OUTPUT
profile.RECORDS['synthetic_only'] = False
profile.RECORDS['upstream'] = {'provider': 'openrouter', 'model': UPSTREAM_MODEL,
                               'prompt_scope': 'Somente prompt fixo do gateway, sem contexto do projeto'}
profile.base.extend(['--env=UDH_GATEWAY_TIMEOUT_MS=65000'])
settings = profile.CONFIG / 'settings.json'
configuration = json.loads(settings.read_text())
configuration['httpIdleTimeoutMs'] = 65000
configuration['retry']['provider']['timeoutMs'] = 65000
settings.write_text(json.dumps(configuration))
profile.RECORDS['config_hashes_before'] = {p.name: profile.digest(p) for p in profile.CONFIG.iterdir()}


def gateway():
    ready = profile.FIXTURES / 'real-ready.json'
    args = [sys.executable, str(ROOT / 'free_gateway.py'), '--socket', 'gateway.sock',
            '--audit', str(ROOT / 'evidence/free-gateway-audit.jsonl'),
            '--first-part', str(profile.FIXTURES / 'real-first-part'),
            '--phase-file', str(PHASE),
            '--ready', str(ready), '--run-id', profile.RUN_ID]
    process = subprocess.Popen(args, cwd=profile.IPC, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    end = time.monotonic() + 25
    while not ready.exists() and process.poll() is None and time.monotonic() < end:
        time.sleep(0.05)
    if not ready.exists():
        process.kill()
        process.communicate(timeout=5)
        raise RuntimeError('Gateway não iniciou; diagnóstico sensível não será exibido')
    state = json.loads(ready.read_text())
    profile.RECORDS['gateway_ready'] = state
    profile.save()
    if not state.get('ready'):
        process.wait(timeout=5)
        return None
    return process


def run_pi():
    command = profile.PI.copy()
    command[command.index('--system-prompt') + 1] = 'Responda exatamente UDH_REAL_PROBE_OK.'
    command[-1] = 'Responda exatamente UDH_REAL_PROBE_OK.'
    return profile.run('pi-openrouter-free', ['sh', '-c', profile.shell_command, 'real-probe'] + command,
                       timeout=80)


def run_opencode():
    binary = Path('/home/debian/.opencode/bin/opencode')
    dependencies = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, check=True)
    libraries = profile.re.findall(r'/[^\s()]+', dependencies.stdout)
    profile.base.extend(['--memory=768m', '--memory-swap=768m', '--volume', str(binary) + ':/opt/opencode:ro'])
    for library in libraries:
        if library not in profile.libraries:
            profile.base.extend(['--volume', library + ':' + library + ':ro'])
    config = {'autoupdate': False, 'share': 'disabled', 'plugin': [],
              'tools': {'webfetch': False, 'websearch': False, 'bash': False, 'edit': False, 'write': False},
              'provider': {'udh_free': {'npm': '@ai-sdk/openai-compatible',
                 'options': {'baseURL': 'http://127.0.0.1:43127/v1', 'apiKey': 'UDH_SYNTHETIC_CAPABILITY_ONLY'},
                 'models': {'udh-spike': {'name': 'OpenRouter gratuito via gateway'}}}}}
    environment = {'XDG_CONFIG_HOME': '/workspace/oc-config', 'XDG_DATA_HOME': '/workspace/oc-data',
                   'XDG_CACHE_HOME': '/workspace/oc-cache', 'XDG_STATE_HOME': '/workspace/oc-state',
                   'OPENCODE_CONFIG_CONTENT': json.dumps(config),
                   'OPENCODE_DISABLE_AUTOUPDATE': 'true', 'OPENCODE_DISABLE_MODELS_FETCH': 'true',
                   'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true', 'OPENCODE_DISABLE_LSP_DOWNLOAD': 'true',
                   'OPENCODE_DISABLE_EXTERNAL_SKILLS': 'true', 'OPENCODE_DISABLE_CLAUDE_CODE': 'true'}
    for key, value in environment.items():
        profile.base.extend(['--env', key + '=' + value])
    return profile.run('opencode-openrouter-free', ['sh', '-c', profile.shell_command, 'real-probe',
        '/opt/opencode', '--pure', 'run', '--format', 'json', '--model', 'udh_free/udh-spike',
        'Responda exatamente UDH_REAL_PROBE_OK.'], timeout=80)


def validate_results(payload):
    """Confere texto, eventos finais, código real e auditoria do provedor juntos."""
    for row in payload['experiments']:
        events = []
        for line in row['stdout'].splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        if row['label'].startswith('pi-'):
            finals = [event['message'] for event in events
                      if event.get('type') == 'message_end' and
                      event.get('message', {}).get('role') == 'assistant']
            normal = bool(finals) and finals[-1].get('stopReason') == 'stop'
            error = any(message.get('stopReason') in ('error', 'aborted') for message in finals)
            texts = [part.get('text', '') for message in finals for part in message.get('content', [])]
        else:
            finishes = [event for event in events if event.get('type') == 'step_finish']
            normal = bool(finishes) and finishes[-1].get('part', {}).get('reason') == 'stop'
            error = any(event.get('type') == 'error' for event in events)
            texts = [event.get('part', {}).get('text', '') for event in events if event.get('type') == 'text']
        row['real_marker_received'] = any('UDH_REAL_PROBE_OK' in text for text in texts)
        row['normal_completion'] = normal and not error and not row.get('timed_out')
        if row.get('expected_mediator_cut'):
            row['received_partial_text'] = any(
                event.get('assistantMessageEvent', {}).get('type') == 'text_delta' and
                bool(event['assistantMessageEvent'].get('delta')) for event in events)
            row['validated_success'] = (error and not row.get('timed_out') and
                row['received_partial_text'] and
                row.get('mediator_sent_text') is True and row.get('mediator_returncode') == -9 and
                row.get('cleanup_returncode') == 0)
        else:
            row['validated_success'] = (row['returncode'] == 0 and row['normal_completion'] and
                                        row['real_marker_received'] and row.get('cleanup_returncode') == 0)
    audit = [json.loads(line) for line in (ROOT / 'evidence/free-gateway-audit.jsonl').read_text().splitlines()]
    calls = [row for row in audit if row['run_id'] == payload['run_id'] and 'upstream_host' in row]
    payload['provider_checks'] = {
        'complete_calls_for_both_agents': all(any(row.get('agent') == agent and
            row['verdict'] == 'real_stream_complete' for row in calls) for agent in ('pi', 'opencode')),
        'all_reported_costs_zero': bool(calls) and all(row.get('usage', {}).get('cost') == 0 for row in calls),
        'only_selected_free_model': bool(calls) and all(row['requested_model'] == UPSTREAM_MODEL for row in calls),
    }
    payload['protocol_checks'] = {
        'both_agents_and_real_cut_present': {row['label'] for row in payload['experiments']} ==
            {'pi-openrouter-free', 'opencode-openrouter-free', 'pi-free-mediatorkilled'},
        'protected_config_unchanged': payload.get('config_hashes_before') == payload.get('config_hashes_after'),
        'gateway_stopped': payload.get('gateway_cleanup_returncode') == -9,
    }
    return (all(row['validated_success'] for row in payload['experiments']) and
            all(payload['provider_checks'].values()) and all(payload['protocol_checks'].values()))


def main():
    if '--verify-only' in sys.argv:
        payload = json.loads(OUTPUT.read_text())
        valid = validate_results(payload)
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'validated_success': valid, 'provider_checks': payload['provider_checks']}))
        return 0 if valid and payload.get('secret_scan', {}).get('secret_absent') else 1
    process = gateway()
    if process is None:
        return 1
    try:
        for runner in (run_pi, run_opencode):
            PHASE.write_text(json.dumps({'agent': 'pi' if runner == run_pi else 'opencode'}))
            result = runner()
            # O corpo vem do provedor real; sucesso exige texto no evento, não só exit0.
            events = []
            for line in result['stdout'].splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
            if runner == run_pi:
                texts = [part.get('text', '') for event in events if event.get('type') == 'message_end'
                         and event.get('message', {}).get('role') == 'assistant'
                         and event['message'].get('stopReason') == 'stop'
                         for part in event['message'].get('content', [])]
            else:
                texts = [event.get('part', {}).get('text', '') for event in events if event.get('type') == 'text']
            result['real_marker_received'] = any('UDH_REAL_PROBE_OK' in text for text in texts)
            result['final_text_present'] = any(bool(text.strip()) for text in texts)
            profile.save()
        PHASE.write_text(json.dumps({'agent': 'pi-cut', 'pause': True}))
        FIRST_PART.unlink(missing_ok=True)
        command = profile.PI.copy()
        command[command.index('--system-prompt') + 1] = 'Responda exatamente UDH_REAL_PROBE_OK.'
        command[-1] = 'Responda exatamente UDH_REAL_PROBE_OK.'
        child, cidfile, record = profile.launch('pi-free-mediatorkilled',
            ['sh', '-c', profile.shell_command, 'real-cut'] + command)
        end = time.monotonic() + 35
        while not FIRST_PART.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(0.02)
        record['expected_mediator_cut'] = True
        record['mediator_sent_text'] = FIRST_PART.exists()
        process.kill()
        process.communicate(timeout=5)
        record['mediator_returncode'] = process.returncode
        profile.finish(child, cidfile, record, timeout=12)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
        profile.RECORDS['gateway_cleanup_returncode'] = process.returncode
        profile.RECORDS['config_hashes_after'] = {p.name: profile.digest(p) for p in profile.CONFIG.iterdir()}
        profile.save()
    scan = subprocess.run([sys.executable, str(ROOT / 'free_gateway.py'), '--scan', str(OUTPUT),
                           str(ROOT / 'evidence/free-gateway-audit.jsonl')],
                          capture_output=True, text=True, timeout=20)
    profile.RECORDS['secret_scan'] = json.loads(scan.stdout) if scan.returncode == 0 else {'secret_absent': False}
    valid = validate_results(profile.RECORDS)
    profile.save()
    print(json.dumps({'secret_absent': profile.RECORDS['secret_scan'].get('secret_absent'),
                      'results': [{'agent': row['label'], 'marker_received': row.get('real_marker_received'),
                                   'text_present': row.get('final_text_present'), 'returncode': row['returncode']}
                                  for row in profile.RECORDS['experiments']]}))
    return 0 if profile.RECORDS['secret_scan'].get('secret_absent') and valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
