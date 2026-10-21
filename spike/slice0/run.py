"""Executa experimentos descartáveis de isolamento, sem credenciais reais."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import uuid

ROOT = Path(__file__).resolve().parent
IMAGE = 'docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'
records = []
for directory in ('evidence', 'workspace', 'host-fixtures'):
    (ROOT / directory).mkdir(parents=True, exist_ok=True)


def collect(label, args, timeout=30, env=None):
    """Registra argumentos, resultado e limites de tempo de cada tentativa."""
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=timeout, env=env)
        record = dict(label=label, args=args, returncode=result.returncode,
                      stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired:
        record = dict(label=label, args=args, returncode=None,
                      stdout='', stderr='Tempo limite excedido')
    records.append(record)
    (ROOT / 'evidence/results.json').write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    print(label, record['returncode'], flush=True)
    return record


workspace = ROOT / 'workspace'
sentinel = ROOT / 'host-fixtures/sentinel.txt'
sentinel.write_text('UDH_SYNTHETIC_SENTINEL\n')
link = workspace / 'outside-link'
if link.is_symlink():
    if link.readlink() != sentinel:
        raise RuntimeError('Atalho sentinela preexistente aponta para outro destino')
elif link.exists():
    raise RuntimeError('Caminho da sentinela já existe e não é um atalho')
else:
    link.symlink_to(sentinel)
credential_fixture = ROOT / 'host-fixtures/synthetic-credential.txt'
credential_fixture.write_text('UDH_SYNTHETIC_CREDENTIAL_ONLY\n')
credential_link = workspace / 'credential-link'
if credential_link.is_symlink():
    if credential_link.readlink() != credential_fixture:
        raise RuntimeError('Atalho sintético preexistente aponta para outro destino')
elif credential_link.exists():
    raise RuntimeError('Caminho da fixture sintética já existe e não é um atalho')
else:
    credential_link.symlink_to(credential_fixture)

collect('runtime', ['podman', 'info', '--format', 'json'])
collect('image', ['podman', 'image', 'inspect', IMAGE])
base = ['podman', 'run', '--pull=never', '--network=none', '--read-only',
        '--read-only-tmpfs=false', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--userns=keep-id',
        '--pids-limit=32', '--memory=64m', '--memory-swap=64m',
        '--volume', str(workspace) + ':/workspace:rw', '--workdir=/workspace']


def experiment(label, script, extra=(), env=None):
    """Inspeciona o contêiner antes de remover apenas o recurso criado aqui."""
    name = 'udh-slice0-' + label.lower() + '-' + uuid.uuid4().hex
    existing = collect(label + '-preflight', ['podman', 'container', 'exists', name])
    if existing['returncode'] != 1:
        raise RuntimeError('Contêiner preexistente ou consulta inconclusiva; execução recusada')
    cidfile = ROOT / 'evidence' / (name + '.cid')
    try:
        result = collect(label, base + ['--name', name, '--cidfile', str(cidfile)] + list(extra) +
                         [IMAGE, 'sh', '-c', script], env=env)
    finally:
        if cidfile.exists():
            container_id = cidfile.read_text().strip()
            if len(container_id) != 64 or any(c not in '0123456789abcdef' for c in container_id):
                raise RuntimeError('Identificador do contêiner criado é inválido; remoção recusada')
            try:
                collect(label + '-inspect', ['podman', 'inspect', container_id])
            finally:
                cleanup = collect(label + '-cleanup', ['podman', 'rm', '-f', container_id])
                if cleanup['returncode'] == 0:
                    cidfile.unlink(missing_ok=True)
                else:
                    raise RuntimeError('Limpeza falhou; identificador preservado no cidfile')
    return result


experiment('A1', 'id; cat /proc/self/uid_map; cat /proc/self/status')
experiment('A3-B1-B2-B3-B5-B6', '''
set -eu
test -d /workspace
echo synthetic > /workspace/write-proof
test ! -e /workspace/outside-link
test ! -e "''' + str(sentinel) + '''"
test ! -e /run/podman/podman.sock
test ! -e /run/user/1000/podman/podman.sock
test ! -e /var/run/docker.sock
test "${UDH_SYNTHETIC_SECRET-unset}" = unset
test ! -e /workspace/credential-link
test ! -r /workspace/credential-link
test ! -e ''' + shlex.quote(str(credential_fixture)) + '''
test ! -r ''' + shlex.quote(str(credential_fixture)) + '''
test -r /proc/1/environ
if tr '\\000' '\\n' < /proc/1/environ | grep -q '^UDH_SYNTHETIC_SECRET='; then
  echo 'VARIÁVEL SINTÉTICA EXPOSTA NO PROCESSO'; exit 1
fi
echo 'B6: arquivo sintético direto/atalho inacessível; variável sintética ausente'
for p in /outside-proof /tmp/outside-proof /home/outside-proof /etc/outside-proof; do
  if (echo synthetic > "$p") 2>/dev/null; then echo "ESCRITA INDEVIDA: $p"; exit 1; fi
done
cat /proc/mounts
echo ISOLATION_CHECKS_OK
''', env=dict(os.environ, UDH_SYNTHETIC_SECRET='synthetic-only'))
experiment('B4', '''
set -eu
for tool in nc nslookup; do
 if ! command -v "$tool"; then echo "NÃO EXECUTADO: ferramenta ausente: $tool"; exit 77; fi
done
echo 'Diagnóstico nc (ajuda; não prova conectividade):'
nc -h 2>&1 || true
cat /proc/net/route
cat /proc/net/ipv6_route
for target in 1.1.1.1 192.0.2.1; do
 for port in 53 80 443 8443; do
  if nc -w 1 "$target" "$port" </dev/null; then
   echo "UNEXPECTED_CONNECTION $target $port"; exit 1
  else
   status=$?; echo "TCP $target:$port retorno=$status (falha exige análise do diagnóstico)"
  fi
 done
done
if nslookup example.com; then echo UNEXPECTED_DNS; exit 1; fi
echo NETWORK_CHECKS_OK
''')
experiment('A2-memory', "awk 'BEGIN { for(i=0;i<2000000;i++) a[i]=sprintf(\"%01024d\",i) }'")
experiment('A2-pids', '''
cat /sys/fs/cgroup/pids.max
for i in $(seq 1 64); do sleep 5 & done
wait
''')
experiment('A2-cpu', '''
set -eu
cat /sys/fs/cgroup/cpu.max
echo BEFORE
cat /sys/fs/cgroup/cpu.stat
sh -c 'while :; do :; done' &
worker=$!
sleep 2
kill "$worker"
wait "$worker" || true
echo AFTER
cat /sys/fs/cgroup/cpu.stat
''', extra=['--cpus=0.25'])
