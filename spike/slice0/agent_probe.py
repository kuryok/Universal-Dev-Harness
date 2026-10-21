"""Coordena Pi + ponte + mediador SINTÉTICO, sem autenticação do usuário."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

from gateway_fixture import MODEL, TOKEN

ROOT = Path(__file__).resolve().parent
IMAGE = 'docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'
NODE = Path('/home/debian/.local/share/pi-node/current/bin/node').resolve()
MODULES = Path('/home/debian/.pi/agent/install/releases/1.2.0/node_modules')
ENTRY = '/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
RUN_ID = uuid.uuid4().hex
FIXTURES = ROOT / 'host-fixtures' / ('agent-' + RUN_ID)
WORKSPACE = ROOT / 'workspace' / ('agent-' + RUN_ID)
IPC = FIXTURES / 'ipc'
CONFIG = FIXTURES / 'config'
for directory in (WORKSPACE, IPC, CONFIG, ROOT / 'evidence'):
    directory.mkdir(parents=True, exist_ok=True)
IPC.chmod(0o700)
(WORKSPACE / 'agent-config').mkdir()
RECORDS = {'run_id': RUN_ID, 'synthetic_only': True, 'experiments': []}
OUTPUT = ROOT / 'evidence/agent-probe.json'


def save():
    OUTPUT.write_text(json.dumps(RECORDS, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


ldd = subprocess.run(['ldd', str(NODE)], capture_output=True, text=True, check=True)
libraries = sorted(set(re.findall(r'(/[^\s()]+)', ldd.stdout)))
runtime = [(NODE, '/runtime/node')]
runtime.extend((Path(path), path) for path in libraries)
RECORDS['runtime_files'] = [{'source': str(src), 'destination': dst,
                            'sha256': digest(src)} for src, dst in runtime]
manifest = hashlib.sha256()
file_count = 0
for path in sorted(MODULES.rglob('*')):
    if path.is_file():
        resolved = path.resolve()
        if not resolved.is_relative_to(MODULES.resolve()):
            raise RuntimeError('Runtime instalado contém symlink externo; execução recusada')
        manifest.update(str(path.relative_to(MODULES)).encode() + b'\0')
        manifest.update(bytes.fromhex(digest(path)))
        file_count += 1
RECORDS['pi_runtime'] = {'version': '1.2.0', 'files': file_count,
                         'manifest_sha256': manifest.hexdigest()}

models = {'providers': {'udh': {'baseUrl': 'http://127.0.0.1:43127/v1',
          'api': 'openai-completions', 'apiKey': TOKEN,
          'models': [{'id': MODEL, 'name': 'Modelo SINTÉTICO do spike',
                      'contextWindow': 16000, 'maxTokens': 128,
                      'input': ['text'], 'reasoning': False}]}}}
(CONFIG / 'models.json').write_text(json.dumps(models))
(CONFIG / 'settings.json').write_text(json.dumps({
    'enableInstallTelemetry': False, 'enableAnalytics': False,
    'httpIdleTimeoutMs': 4000,
    'retry': {'enabled': False, 'provider': {'maxRetries': 0, 'timeoutMs': 4000}}}))
(CONFIG / 'auth.json').write_text('{}\n')
config_hashes = {p.name: digest(p) for p in CONFIG.iterdir()}

base = ['podman', 'run', '--pull=never', '--network=none', '--read-only',
        '--read-only-tmpfs=false', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--userns=keep-id', '--pids-limit=64', '--memory=384m', '--memory-swap=384m',
        '--env=HOME=/workspace/home', '--env=PI_CODING_AGENT_DIR=/workspace/agent-config',
        '--env=PI_OFFLINE=1', '--env=PI_TELEMETRY=0',
        '--env=PATH=/runtime:/usr/bin:/bin', '--env=TMPDIR=/workspace/tmp',
        '--workdir=/workspace', '--volume', str(WORKSPACE) + ':/workspace:rw',
        '--volume', str(WORKSPACE / 'agent-config') + ':/workspace/agent-config:rw',
        '--volume', str(IPC) + ':/ipc:ro',
        '--volume', str(MODULES) + ':/opt/pi/node_modules:ro',
        '--volume', str(ROOT / 'network_client.mjs') + ':/network_client.mjs:ro',
        '--volume', str(ROOT / 'bridge.mjs') + ':/bridge.mjs:ro']
for filename in ('models.json', 'settings.json', 'auth.json'):
    base.extend(['--volume', str(CONFIG / filename) + ':/workspace/agent-config/' + filename + ':ro'])
for source, destination in runtime:
    base.extend(['--volume', f'{source}:{destination}:ro'])


def launch(label, command):
    name = 'udh-slice0-agent-' + uuid.uuid4().hex
    cidfile = FIXTURES / (name + '.cid')
    args = base + ['--name', name, '--cidfile', str(cidfile), IMAGE] + command
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return process, cidfile, {'label': label, 'args': args}


def finish(process, cidfile, record, timeout=20):
    start = time.monotonic()
    try:
        record['stdout'], record['stderr'] = process.communicate(timeout=timeout)
        record['returncode'] = process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        record['stdout'], record['stderr'] = process.communicate(timeout=5)
        record['returncode'] = None
        record['timed_out'] = True
    finally:
        record['elapsed_seconds'] = time.monotonic() - start
        if cidfile.exists():
            container_id = cidfile.read_text().strip()
            if not re.fullmatch('[0-9a-f]{64}', container_id):
                raise RuntimeError('ID inválido; limpeza recusada')
            try:
                inspect = subprocess.run(['podman', 'inspect', container_id],
                                         capture_output=True, text=True, timeout=10)
                record['inspection'] = json.loads(inspect.stdout) if inspect.returncode == 0 else None
            finally:
                cleanup = subprocess.run(['podman', 'rm', '-f', container_id],
                                         capture_output=True, text=True, timeout=10)
                record['cleanup_returncode'] = cleanup.returncode
                if cleanup.returncode != 0:
                    raise RuntimeError('Limpeza falhou; cidfile preservado')
                cidfile.unlink(missing_ok=True)
        RECORDS['experiments'].append(record)
        save()
    print(record['label'], record['returncode'], flush=True)
    return record


def run(label, command, timeout=20):
    return finish(*launch(label, command), timeout=timeout)


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(4)
        directory_fd = os.open(IPC, os.O_PATH | os.O_DIRECTORY)
        try:
            self.sock.connect(f'/proc/self/fd/{directory_fd}/gateway.sock')
        finally:
            os.close(directory_fd)


def start_gateway(pause=False):
    sock = IPC / 'gateway.sock'
    sock.unlink(missing_ok=True)
    first_part = FIXTURES / 'first-part'
    first_part.unlink(missing_ok=True)
    audit = ROOT / 'evidence/gateway-audit.jsonl'
    args = [sys.executable, str(ROOT / 'gateway_fixture.py'), '--socket', 'gateway.sock',
            '--audit', str(audit), '--first-part', str(first_part), '--run-id', RUN_ID]
    if pause:
        args.append('--pause')
    process = subprocess.Popen(args, cwd=IPC, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 5
    while not sock.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if not sock.exists():
        process.kill()
        raise RuntimeError('Mediador não iniciou: ' + process.communicate(timeout=5)[1])
    return process, first_part


PI = ['/runtime/node', ENTRY, '--offline', '--no-mcp', '--no-extensions',
      '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-themes',
      '--no-approve', '--no-tools', '--no-session', '--provider', 'udh',
      '--model', MODEL, '--thinking', 'off', '--mode', 'json', '--print',
      '--system-prompt', 'Responda somente ao teste sintético local.', 'Teste sintético.']
# A ponte só fica pronta depois de publicar seu listener no namespace do contêiner.
shell_command = '''
set -eu
mkdir -p /workspace/home /workspace/tmp
rm -f /workspace/bridge-ready
/runtime/node /bridge.mjs > /workspace/bridge-ready 2>/workspace/bridge-error &
bridge=$!
trap 'kill "$bridge" 2>/dev/null || true' EXIT
i=0
while ! grep -q BRIDGE_READY /workspace/bridge-ready; do
 i=$((i+1)); test "$i" -lt 100; sleep 0.02
done
"$@"
'''


def main():
    run('runtime-version', ['/runtime/node', '--version'])
    run('pi-version', ['/runtime/node', ENTRY, '--version'])
    run('B4-E1-E2-network', ['/runtime/node', '/network_client.mjs'])
    run('E4-config-readonly', ['sh', '-c', '''
set -eu
for p in /workspace/agent-config/models.json /workspace/agent-config/settings.json /workspace/agent-config/auth.json; do
 if (echo synthetic > "$p") 2>/dev/null; then exit 1; fi
done
if rm /workspace/agent-config/settings.json 2>/dev/null; then exit 1; fi
if ln -sf /workspace/replacement /workspace/agent-config/models.json 2>/dev/null; then exit 1; fi
if mv /workspace/agent-config /workspace/shadowed-config 2>/dev/null; then
 echo CONFIG_SHADOWED; exit 1
fi
echo CONFIG_PROTECTED
'''])
    gateway, _first = start_gateway()
    try:
        body = json.dumps({'model': MODEL, 'stream': True, 'messages': []})
        controls = []
        for label, method, path, purpose, authorization, payload in [
            ('allowed', 'POST', '/v1/chat/completions', 'inference', TOKEN, body),
            ('route', 'POST', '/v1/arbitrary', 'inference', TOKEN, body),
            ('method', 'DELETE', '/v1/chat/completions', 'inference', TOKEN, body),
            ('purpose', 'POST', '/v1/chat/completions', 'publish', TOKEN, body),
            ('capability', 'POST', '/v1/chat/completions', 'inference', 'wrong-synthetic', body),
            ('model', 'POST', '/v1/chat/completions', 'inference', TOKEN,
             json.dumps({'model': 'forbidden', 'stream': True})),
            ('schema', 'POST', '/v1/chat/completions', 'inference', TOKEN,
             json.dumps({'model': MODEL, 'stream': True, 'messages': None})),
            ('size', 'POST', '/v1/chat/completions', 'inference', TOKEN, 'x' * 262145),
        ]:
            connection = UnixConnection('localhost', timeout=4)
            headers = {'Authorization': 'Bearer ' + authorization,
                       'X-UDH-Purpose': purpose}
            if label == 'size':
                # O tamanho é recusado antes de consumir o corpo, sem corrida de BrokenPipe.
                headers['Content-Length'] = str(len(payload))
                connection.request(method, path, body=None, headers=headers)
            else:
                connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            start = time.monotonic()
            chunks = []
            while True:
                line = response.readline()
                if not line:
                    break
                if line.startswith(b'data: '):
                    chunks.append({'line': line.decode().strip(),
                                   'after_seconds': time.monotonic() - start})
            controls.append({'label': label, 'status': response.status, 'events': chunks})
            RECORDS['gateway_controls'] = controls
            save()
            connection.close()
        RECORDS['gateway_controls'] = controls
        bridge_checks = '''
const http = require('node:http');
const cases = [['purpose','POST','/v1/chat/completions','publish'],
               ['route','POST','/v1/forbidden','inference'],
               ['method','DELETE','/v1/chat/completions','inference']];
(async () => {
 const results=[];
 for (const [label,method,path,purpose] of cases) {
  const result = await new Promise((resolve,reject) => {
   const body=JSON.stringify({model:'udh-spike',stream:true,messages:[]});
   const req=http.request({host:'127.0.0.1',port:43127,method,path,
     headers:{'Authorization':'Bearer UDH_SYNTHETIC_CAPABILITY_ONLY',
              'X-UDH-Purpose':purpose,'Content-Length':Buffer.byteLength(body)}}, res => {
      res.resume();res.on('end',()=>resolve({label,status:res.statusCode}));
   });req.on('error',reject);req.end(body);
  }); results.push(result);
 } console.log(JSON.stringify(results));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
'''
        run('C2-through-bridge', ['sh', '-c', shell_command, 'bridge-run',
                                  '/runtime/node', '-e', bridge_checks])
        connection = UnixConnection('localhost', timeout=4)
        connection.putrequest('POST', '/v1/chat/completions')
        connection.putheader('Authorization', 'Bearer ' + TOKEN)
        connection.putheader('X-UDH-Purpose', 'inference')
        connection.putheader('Content-Length', '1')
        connection.putheader('Content-Length', '2')
        connection.endheaders()
        response = connection.getresponse()
        RECORDS['duplicate_content_length_status'] = response.status
        response.read()
        connection.close()
        connection = UnixConnection('localhost', timeout=5)
        connection.connect()
        connection.sock.settimeout(5)
        start = time.monotonic()
        connection.sock.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nX-Incomplete: ')
        RECORDS['incomplete_headers_closed'] = connection.sock.recv(1) == b''
        RECORDS['incomplete_headers_seconds'] = time.monotonic() - start
        connection.close()
        run('D1-D2-E3-pi-synthetic', ['sh', '-c', shell_command, 'bridge-run'] + PI)
    finally:
        gateway.terminate()
        gateway.communicate(timeout=5)
    gateway, first = start_gateway(pause=True)
    process, cidfile, record = launch('C4-mediator-killed', ['sh', '-c', shell_command, 'bridge-run'] + PI)
    try:
        deadline = time.monotonic() + 10
        while not first.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        record['mediator_sent_first_part'] = first.exists()
        gateway.kill()
        gateway.communicate(timeout=5)
        record['mediator_returncode'] = gateway.returncode
        finish(process, cidfile, record, timeout=12)
    finally:
        if gateway.poll() is None:
            gateway.kill()
            gateway.communicate(timeout=5)
    RECORDS['config_hashes_before'] = config_hashes
    RECORDS['config_hashes_after'] = {p.name: digest(p) for p in CONFIG.iterdir()}
    audit_text = (ROOT / 'evidence/gateway-audit.jsonl').read_text()
    RECORDS['C5_synthetic_capability_absent_from_audit'] = TOKEN not in audit_text
    RECORDS['ipc_socket_mode'] = oct((IPC / 'gateway.sock').stat().st_mode & 0o777)
    normal = next(x for x in RECORDS['experiments'] if x['label'] == 'D1-D2-E3-pi-synthetic')
    cut = next(x for x in RECORDS['experiments'] if x['label'] == 'C4-mediator-killed')
    events = [json.loads(line) for line in normal['stdout'].splitlines() if line.startswith('{')]
    cut_events = [json.loads(line) for line in cut['stdout'].splitlines() if line.startswith('{')]
    deltas = [x['assistantMessageEvent']['delta'] for x in events
              if x.get('assistantMessageEvent', {}).get('type') == 'text_delta']
    bridge = next(x for x in RECORDS['experiments'] if x['label'] == 'C2-through-bridge')
    network = next(x for x in RECORDS['experiments'] if x['label'] == 'B4-E1-E2-network')
    network_rows = json.loads(network['stdout'])
    blocked = [x for x in network_rows if x['case'] in ('tcp', 'udp4', 'udp6')]
    RECORDS['checks'] = {
        'gateway_policy_controls': {x['label']: x['status'] for x in RECORDS['gateway_controls']} ==
            {'allowed': 200, 'route': 403, 'method': 403, 'purpose': 403,
             'capability': 401, 'model': 403, 'schema': 400, 'size': 413},
        'bridge_policy_controls': bridge['returncode'] == 0 and
            {x['label']: x['status'] for x in json.loads(bridge['stdout'])} ==
            {'purpose': 403, 'route': 403, 'method': 403},
        'duplicate_framing_rejected': RECORDS['duplicate_content_length_status'] == 400,
        'incomplete_headers_bounded': RECORDS['incomplete_headers_closed'] and
            RECORDS['incomplete_headers_seconds'] < 4.5,
        'tcp_udp_egress_unreachable': len(blocked) == 10 and
            all(x['result'] == 'ENETUNREACH' for x in blocked),
        'network_clients_positive_controls': sum(x.get('received') == 'UDH_CONTROL'
            for x in network_rows) == 2,
        'config_mutation_denied': next(x for x in RECORDS['experiments']
            if x['label'] == 'E4-config-readonly')['returncode'] == 0,
        'pi_synthetic_response': normal['returncode'] == 0 and ''.join(deltas) == 'SPIKE_SYNTHETIC_OK',
        'pi_stream_has_multiple_deltas': len(deltas) >= 2,
        'cut_reports_structured_error': any(x.get('type') == 'message_end' and
            x.get('message', {}).get('stopReason') == 'error' for x in cut_events),
        'cut_did_not_timeout': not cut.get('timed_out') and cut['mediator_sent_first_part'] and
            cut['mediator_returncode'] == -9,
        'configuration_unchanged': RECORDS['config_hashes_after'] == config_hashes,
        'capability_absent_from_audit': RECORDS['C5_synthetic_capability_absent_from_audit'],
    }
    save()
    if not all(RECORDS['checks'].values()):
        raise RuntimeError('Experimento possui critério não confirmado; ver agent-probe.json')


if __name__ == '__main__':
    main()
