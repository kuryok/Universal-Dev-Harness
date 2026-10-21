"""Executa experimentos descartáveis de isolamento, sem credenciais reais."""
import json
import os
from pathlib import Path
import subprocess

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
if not link.is_symlink():
    link.symlink_to(sentinel)

collect('runtime', ['podman', 'info', '--format', 'json'])
collect('image', ['podman', 'image', 'inspect', IMAGE])
base = ['podman', 'run', '--pull=never', '--network=none', '--read-only',
        '--read-only-tmpfs=false', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--userns=keep-id',
        '--pids-limit=32', '--memory=64m', '--memory-swap=64m',
        '--volume', str(workspace) + ':/workspace:rw', '--workdir=/workspace']


def experiment(label, script, extra=(), env=None):
    """Inspeciona o contêiner antes de remover apenas o recurso criado aqui."""
    name = 'udh-slice0-' + label.lower()
    result = collect(label, base + ['--name', name] + list(extra) +
                     [IMAGE, 'sh', '-c', script], env=env)
    collect(label + '-inspect', ['podman', 'inspect', name])
    collect(label + '-cleanup', ['podman', 'rm', '-f', name])
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
for p in /outside-proof /tmp/outside-proof /home/outside-proof /etc/outside-proof; do
  if (echo synthetic > "$p") 2>/dev/null; then echo "ESCRITA INDEVIDA: $p"; exit 1; fi
done
cat /proc/mounts
echo ISOLATION_CHECKS_OK
''', env=dict(os.environ, UDH_SYNTHETIC_SECRET='synthetic-only'))
experiment('B4', '''
cat /proc/net/route
cat /proc/net/ipv6_route
for target in 1.1.1.1 192.0.2.1; do
 for port in 53 80 443 8443; do
  if nc -w 1 "$target" "$port" </dev/null; then echo "UNEXPECTED_CONNECTION $target $port"; exit 1; fi
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
