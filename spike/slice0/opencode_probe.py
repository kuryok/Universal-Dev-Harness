"""Inspeciona o CLI OpenCode isolado, sem rede e sem autenticação do host."""
import ast
import argparse
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'evidence/opencode-probe.json'
BINARY = Path('/home/debian/.opencode/bin/opencode')
records = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []


def collect(label, args, timeout=20, config_fields_only=False):
    """Persiste inclusive falhas e limites de tempo, sem inferir aprovação."""
    try:
        process = subprocess.run(args, capture_output=True, text=True,
                                 timeout=timeout)
        result = dict(label=label, args=args, returncode=process.returncode,
                      stdout=process.stdout, stderr=process.stderr)
        if config_fields_only:
            try:
                config = json.loads(process.stdout)
                selected = {key: config.get(key) for key in
                            ('tools', 'plugin', 'autoupdate', 'share', 'permission')}
                selected['synthetic_endpoint'] = config.get('provider', {}).get(
                    'udh_synthetic', {}).get('options', {}).get('baseURL')
                result['stdout'] = json.dumps(selected, ensure_ascii=False)
            except (ValueError, AttributeError):
                result['stdout'] = 'Saída não JSON omitida; configuração não comprovada'
    except subprocess.TimeoutExpired:
        result = dict(label=label, args=args, returncode=None,
                      stdout='', stderr='Tempo limite excedido')
    records.append(result)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    print(label, result['returncode'], flush=True)
    return result


def main():
    """Usa somente binário e bibliotecas explícitas em ambiente sintético."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--memory-mib', type=int, default=256)
    parser.add_argument('--synthetic-inference', action='store_true')
    options = parser.parse_args()
    if not 256 <= options.memory_mib <= 768:
        raise ValueError('Comparação limitada a 256–768 MiB')
    tree = ast.parse((ROOT / 'run.py').read_text())
    image = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'IMAGE'
                         for target in node.targets))
    dependencies = collect('ldd', ['ldd', str(BINARY)])
    if dependencies['returncode'] != 0:
        raise RuntimeError('Dependências do binário não identificadas')
    libraries = sorted(set(re.findall(r'/[^\s()]+', dependencies['stdout'])))
    if not libraries or any(not Path(path).is_file() for path in libraries):
        raise RuntimeError('Bibliotecas ausentes ou lista inválida')
    records.append({'label': 'runtime-manifest', 'files': [
        {'path': str(path), 'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}
        for path in [BINARY, *map(Path, libraries)]]})
    temporary = tempfile.mkdtemp(prefix='.opencode-workspace-', dir=ROOT)
    cleanup_safe = True
    gateway = None
    try:
        workspace = Path(temporary) / 'workspace'
        workspace.mkdir()
        ipc = Path(temporary) / 'ipc'
        if options.synthetic_inference:
            ipc.mkdir(mode=0o700)
            node = Path('/home/debian/.local/share/pi-node/current/bin/node').resolve()
            node_dependencies = collect('synthetic-node-ldd', ['ldd', str(node)])
            if node_dependencies['returncode'] != 0:
                raise RuntimeError('Bibliotecas do Node não identificadas')
            libraries = sorted(set(libraries + re.findall(
                r'/[^\s()]+', node_dependencies['stdout'])))
            gateway_run_id = uuid.uuid4().hex
            gateway = subprocess.Popen([
                sys.executable, str(ROOT / 'gateway_fixture.py'), '--socket', 'gateway.sock',
                '--audit', str(ROOT / 'evidence/opencode-gateway-audit.jsonl'),
                '--first-part', str(Path(temporary) / 'first-part'),
                '--run-id', gateway_run_id], cwd=ipc, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 5
            while not (ipc / 'gateway.sock').exists() and gateway.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if not (ipc / 'gateway.sock').exists():
                raise RuntimeError('Mediador sintético não publicou socket')
            records.append(dict(label='synthetic-gateway', run_id=gateway_run_id,
                                synthetic_only=True))
        for directory in ('home', 'config', 'data', 'cache', 'state', 'tmp'):
            (workspace / directory).mkdir()
        probes = [
            ('version', ['--version']),
            ('help', ['--help']),
            ('run-help', ['run', '--help']),
            ('models-help', ['models', '--help']),
            ('models', ['models']),
            ('debug-help', ['debug', '--help']),
            ('agent-list', ['agent', 'list']),
            ('debug-config', ['debug', 'config']),
        ]
        if options.synthetic_inference:
            probes.append(('synthetic-inference', ['run', '--format', 'json', '--model',
                           'udh_synthetic/udh-spike', 'Teste sintético.']))
        for label, arguments in probes:
            label = str(options.memory_mib) + 'm-' + label
            name = 'udh-opencode-probe-' + uuid.uuid4().hex
            cidfile = Path(temporary) / (name + '.cid')
            check = collect(label + '-preflight', ['podman', 'container', 'exists', name])
            if check['returncode'] != 1:
                raise RuntimeError('Nome preexistente ou consulta inconclusiva')
            command = ['podman', 'run', '--pull=never', '--network=none',
                       '--read-only', '--read-only-tmpfs=false', '--cap-drop=ALL',
                       '--security-opt=no-new-privileges', '--userns=keep-id',
                       '--memory=' + str(options.memory_mib) + 'm',
                       '--memory-swap=' + str(options.memory_mib) + 'm', '--pids-limit=64',
                       '--name', name, '--cidfile', str(cidfile),
                       '--volume', str(workspace) + ':/workspace:rw',
                       '--volume', str(BINARY) + ':/opt/opencode:ro',
                       '--workdir=/workspace']
            for library in libraries:
                command += ['--volume', library + ':' + library + ':ro']
            if options.synthetic_inference:
                command += ['--volume', str(node) + ':/runtime/node:ro',
                            '--volume', str(ROOT / 'bridge.mjs') + ':/bridge.mjs:ro',
                            '--volume', str(ipc) + ':/ipc:ro']
            for key, value in {'HOME': '/workspace/home',
                               'XDG_CONFIG_HOME': '/workspace/config',
                               'XDG_DATA_HOME': '/workspace/data',
                               'XDG_CACHE_HOME': '/workspace/cache',
                               'XDG_STATE_HOME': '/workspace/state',
                               'TMPDIR': '/workspace/tmp',
                               'OPENCODE_DISABLE_AUTOUPDATE': 'true',
                               'OPENCODE_DISABLE_MODELS_FETCH': 'true',
                               'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true',
                               'OPENCODE_DISABLE_LSP_DOWNLOAD': 'true',
                               'OPENCODE_DISABLE_EXTERNAL_SKILLS': 'true',
                               'OPENCODE_DISABLE_CLAUDE_CODE': 'true',
                               'OPENCODE_CONFIG_CONTENT': json.dumps({
                                   'autoupdate': False, 'share': 'disabled', 'plugin': [],
                                   'tools': {'webfetch': False, 'websearch': False,
                                             'bash': False, 'edit': False, 'write': False},
                                   'provider': {'udh_synthetic': {
                                       'npm': '@ai-sdk/openai-compatible',
                                       'options': {
                                           'baseURL': ('http://127.0.0.1:43127/v1' if options.synthetic_inference
                                                       else 'http://127.0.0.1:9/synthetic-endpoint'),
                                           'apiKey': 'UDH_SYNTHETIC_CAPABILITY_ONLY'},
                                       'models': {('udh-spike' if options.synthetic_inference else 'synthetic'):
                                                  {'name': 'Modelo sintético'}}}},
                               }),
                               'NO_COLOR': '1', 'TERM': 'dumb'}.items():
                command += ['--env', key + '=' + value]
            try:
                execution = ['/opt/opencode', '--pure'] + arguments
                if label.endswith('-synthetic-inference'):
                    shell = '''
set -eu
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
                    execution = ['sh', '-c', shell, 'udh-opencode-probe'] + execution
                result = collect(label, command + [image] + execution,
                                 timeout=30, config_fields_only=label.endswith('-debug-config'))
                if label.endswith('-synthetic-inference'):
                    records.append(dict(label='synthetic-result', run_id=gateway_run_id,
                                        marker_received='SPIKE_SYNTHETIC_OK' in result['stdout'],
                                        cli_returncode=result['returncode'],
                                        real_inference=False))
            finally:
                if cidfile.exists():
                    container_id = cidfile.read_text().strip()
                    if not re.fullmatch('[a-f0-9]{64}', container_id):
                        cleanup_safe = False
                        raise RuntimeError('Identificador inválido; remoção recusada')
                    try:
                        collect(label + '-inspect', ['podman', 'inspect', container_id])
                    finally:
                        cleanup = collect(label + '-cleanup', ['podman', 'rm', '-f', container_id])
                        if cleanup['returncode'] == 0:
                            cidfile.unlink(missing_ok=True)
                        else:
                            cleanup_safe = False
        records.append(dict(label='limits', result='parcial',
                            detail='Sem provedor real ou credenciais reais; endpoint sintético '
                            'exercitado somente com --synthetic-inference. '
                            'Ajuda/listagens não comprovam inferência ou controles E1–E4.'))
        OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    finally:
        if gateway is not None:
            gateway.terminate()
            try:
                _, gateway_stderr = gateway.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                gateway.kill()
                _, gateway_stderr = gateway.communicate(timeout=5)
            records.append(dict(label='synthetic-gateway-cleanup',
                                returncode=gateway.returncode, stderr=gateway_stderr))
            OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
        if cleanup_safe:
            shutil.rmtree(temporary)
        else:
            print('Workspace e CID preservados por falha na limpeza:', temporary)


if __name__ == '__main__':
    main()
