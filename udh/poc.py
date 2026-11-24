"""Orquestrador verificável das POCs fundacionais (POC-01, POC-02, POC-04) sobre o Core real."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import threading
import time

from .adapters.opencode import DEFAULT_OPENCODE_LIMITS, OpenCodeAdapter
from .adapters.pi import PiAdapter
from .doctor import run_doctor
from .domain import CoreError
from .executor import TrustedExecutor
from .gateway import DEFAULT_STUB_MODEL, GatewayConfig, InferenceGateway
from .handoff import (
    HandoffError,
    build_handoff_packet,
    commit_handoff_snapshot,
    init_core_worktree,
    render_handoff_prompt,
    select_adapter,
    verify_handoff_packet,
)
from .policy import policy_v2_sha256
from .runtime import (
    BindMount,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_OPENCODE_MANIFEST_PATH,
    ResourceLimits,
    RuntimeIsolationError,
    RuntimeManager,
)
from .service import CoreService
from .store import SQLiteStore
from .transitions import ConflictError
from .trust_store import TamperingError, TrustStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _uds_raw_request(sock_path: Path, raw: bytes, timeout: float = 4.0) -> tuple[int, str]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(sock_path))
        client.sendall(raw)
        chunks: list[bytes] = []
        while True:
            try:
                piece = client.recv(8192)
            except socket.timeout:
                break
            if not piece:
                break
            chunks.append(piece)
    text = b''.join(chunks).decode('utf-8', errors='replace')
    first_line = text.split('\r\n', 1)[0]
    parts = first_line.split(' ')
    status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    return status, text


def _build_post_request(
    *,
    method: str = 'POST',
    path: str = '/v1/chat/completions',
    token: str,
    purpose: str = 'inference',
    body: bytes = b'{}',
    extra_headers: tuple[str, ...] = (),
    content_length_override: str | None = None,
) -> bytes:
    cl = content_length_override if content_length_override is not None else str(len(body))
    lines = [
        f'{method} {path} HTTP/1.1',
        'Host: localhost',
        f'Authorization: Bearer {token}',
        f'X-UDH-Purpose: {purpose}',
        f'Content-Length: {cl}',
        *extra_headers,
        '',
        '',
    ]
    return '\r\n'.join(lines).encode('utf-8') + body


def run_poc_01(*, manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict:
    """POC-01: isolamento rootless, políticas efetivas, cgroups, rede, gateway UDS e adaptador Pi 1.2.0."""
    manager = RuntimeManager(manifest_path=manifest_path)
    manifest = manager.load_manifest()
    pol_sha = policy_v2_sha256()
    checks: dict[str, dict] = {}

    # 1. Fail-closed por digest
    digest_rejected = False
    try:
        manager.verify_image_present('localhost/udh-runtime:change2')
    except RuntimeIsolationError:
        digest_rejected = True
    fake_digest_rejected = False
    try:
        manager.verify_image_present('localhost/udh-runtime@sha256:' + '0' * 64)
    except RuntimeIsolationError:
        fake_digest_rejected = True
    checks['image_digest_enforcement'] = {
        'ok': digest_rejected and fake_digest_rejected,
        'tag_only_rejected': digest_rejected,
        'unknown_digest_rejected': fake_digest_rejected,
        'image_digest': manifest.image_digest,
    }

    with tempfile.TemporaryDirectory(prefix='udh-poc01-') as tmp:
        root = Path(tmp)
        ws = root / 'workspace'
        host_secret_dir = root / 'host-secrets'
        store = TrustStore(root / 'trust-store')
        ws.mkdir()
        host_secret_dir.mkdir()
        sentinel = host_secret_dir / 'sentinel.txt'
        sentinel.write_text('UDH_POC01_SENTINEL_SECRET\n', encoding='utf-8')
        outside_link = ws / 'outside-link'
        outside_link.symlink_to(sentinel)

        # 2. Isolamento rootless + namespaces (on_started) + capabilities + RO + symlink + sockets + env
        live_inspect: dict = {}

        def _capture_live(container_name: str):
            res = subprocess.run(
                ['podman', 'inspect', '--format', '{{.State.Running}}:{{.HostConfig.NetworkMode}}:{{.HostConfig.ReadonlyRootfs}}', container_name],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if res.returncode == 0:
                live_inspect['snapshot'] = res.stdout.strip()

        host_net_ns = os.readlink('/proc/self/ns/net')
        host_pid_ns = os.readlink('/proc/self/ns/pid')
        iso_script = f'''
set -eu
echo ok > /workspace/write-proof
test ! -e /workspace/outside-link
test ! -e "{sentinel}"
test ! -e /run/podman/podman.sock
test ! -e /var/run/docker.sock
test "${{UDH_SYNTHETIC_SECRET-unset}}" = unset
if (echo bad > /outside-root-write) 2>/dev/null; then exit 11; fi
if (echo bad > /etc/outside-etc-write) 2>/dev/null; then exit 12; fi
grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status
echo "UID_MAP=$(tr -s ' ' < /proc/self/uid_map | tr '\\n' ';')"
echo "NET_NS=$(readlink /proc/self/ns/net)"
echo "PID_NS=$(readlink /proc/self/ns/pid)"
sleep 0.4
echo ISO_OK
'''
        old_env = os.environ.get('UDH_SYNTHETIC_SECRET')
        os.environ['UDH_SYNTHETIC_SECRET'] = 'synthetic-host-secret-must-not-leak'
        try:
            iso = manager.run_developer(
                command=['sh', '-c', iso_script],
                mounts=(BindMount(ws, '/workspace', readonly=False),),
                limits=ResourceLimits(memory_mb=128, pids_limit=32, cpus=0.5, timeout_seconds=15.0),
                on_started=_capture_live,
                forbidden_rw_roots=(store.root,),
            )
        finally:
            if old_env is None:
                os.environ.pop('UDH_SYNTHETIC_SECRET', None)
            else:
                os.environ['UDH_SYNTHETIC_SECRET'] = old_env

        cap_ok = 'CapEff:\t0000000000000000' in iso.stdout
        nnp_ok = 'NoNewPrivs:\t1' in iso.stdout
        seccomp_ok = 'Seccomp:\t2' in iso.stdout
        uid_map_ok = 'UID_MAP=' in iso.stdout and str(os.getuid()) in iso.stdout
        ns_isolated = (
            f'NET_NS={host_net_ns}' not in iso.stdout
            and f'PID_NS={host_pid_ns}' not in iso.stdout
            and 'NET_NS=net:[' in iso.stdout
            and 'PID_NS=pid:[' in iso.stdout
        )
        live_ok = live_inspect.get('snapshot') == 'true:none:true'
        checks['rootless_isolation'] = {
            'ok': (
                iso.returncode == 0
                and 'ISO_OK' in iso.stdout
                and cap_ok and nnp_ok and seccomp_ok
                and uid_map_ok and ns_isolated and live_ok
                and iso.cleanup_verified
            ),
            'cap_eff_zero': cap_ok,
            'no_new_privs': nnp_ok,
            'seccomp_enforced': seccomp_ok,
            'uid_map_verified': uid_map_ok,
            'namespaces_distinct_from_host': ns_isolated,
            'live_inspect': live_inspect.get('snapshot'),
            'cleanup_verified': iso.cleanup_verified,
        }

        # 3. Cgroups efetivos: memória, PIDs, CPU
        oom_ws = root / 'ws-cgroup'
        oom_ws.mkdir()
        mem_res = manager.run_developer(
            command=['python3', '-I', '-c', 'a = bytearray(96 * 1024 * 1024); print(len(a))'],
            mounts=(BindMount(oom_ws, '/workspace', readonly=False),),
            limits=ResourceLimits(memory_mb=32, pids_limit=32, cpus=0.5, timeout_seconds=15.0),
        )
        oom_killed = bool((mem_res.inspection or {}).get('State', {}).get('OOMKilled')) or mem_res.returncode == 137

        pids_res = manager.run_developer(
            command=['sh', '-c', 'cat /sys/fs/cgroup/pids.max; i=0; while [ "$i" -lt 40 ]; do sleep 2 & i=$((i+1)); done; wait'],
            mounts=(BindMount(oom_ws, '/workspace', readonly=False),),
            limits=ResourceLimits(memory_mb=64, pids_limit=16, cpus=0.5, timeout_seconds=15.0),
        )
        pids_limited = '16' in pids_res.stdout and pids_res.returncode != 0

        cpu_res = manager.run_developer(
            command=[
                'python3', '-I', '-c',
                'import pathlib, time\n'
                'print("MAX=" + pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().strip())\n'
                't0=time.monotonic()\n'
                'x=0\n'
                'while time.monotonic()-t0 < 0.8: x += 1\n'
                'print(pathlib.Path("/sys/fs/cgroup/cpu.stat").read_text())\n',
            ],
            mounts=(BindMount(oom_ws, '/workspace', readonly=False),),
            limits=ResourceLimits(memory_mb=64, pids_limit=32, cpus=0.25, timeout_seconds=15.0),
        )
        throttled_count = 0
        for line in cpu_res.stdout.splitlines():
            if line.startswith('nr_throttled '):
                try:
                    throttled_count = int(line.split()[1])
                except (IndexError, ValueError):
                    throttled_count = 0
        cpu_throttled = 'MAX=25000 100000' in cpu_res.stdout and throttled_count > 0
        checks['cgroup_v2_limits'] = {
            'ok': oom_killed and pids_limited and cpu_throttled,
            'memory_oom_enforced': oom_killed,
            'pids_limit_enforced': pids_limited,
            'cpu_quota_enforced': cpu_throttled,
            'nr_throttled': throttled_count,
        }

        # 4. Rede isolada (network=none: externo bloqueado, loopback OK)
        net_script = '''
import socket, threading
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
port = srv.getsockname()[1]
srv.listen(1)
def accept_one():
    c, _ = srv.accept()
    c.sendall(b"LOOPBACK_OK")
    c.close()
    srv.close()
t = threading.Thread(target=accept_one, daemon=True)
t.start()
cli = socket.create_connection(("127.0.0.1", port), timeout=2)
loopback_ok = cli.recv(32) == b"LOOPBACK_OK"
cli.close()
t.join(timeout=2)
ext_blocked = True
for host, p in (("1.1.1.1", 443), ("192.0.2.1", 80)):
    try:
        s = socket.create_connection((host, p), timeout=1)
        s.close()
        ext_blocked = False
    except OSError:
        pass
print(f"LOOPBACK={loopback_ok} EXT_BLOCKED={ext_blocked}")
assert loopback_ok and ext_blocked
'''
        net_res = manager.run_developer(
            command=['python3', '-I', '-c', net_script],
            mounts=(BindMount(oom_ws, '/workspace', readonly=False),),
            limits=ResourceLimits(memory_mb=64, pids_limit=32, cpus=0.5, timeout_seconds=15.0),
        )
        checks['network_isolation'] = {
            'ok': net_res.returncode == 0 and 'LOOPBACK=True EXT_BLOCKED=True' in net_res.stdout,
        }

        # 5. Gateway UDS endurecido (permissões, SO_PEERCRED, controles negativos, slowloris)
        audit_path = store.audit_path_for('poc01-gateway')
        gw_cfg = GatewayConfig(
            run_id='poc01-gateway',
            policy_sha256=pol_sha,
            audit_path=audit_path,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=False,
            max_calls=2,
            max_concurrency=2,
            header_timeout_seconds=1.2,
            body_timeout_seconds=1.5,
        )
        with InferenceGateway(gw_cfg) as gw:
            assert gw.ipc_dir is not None and gw.socket_path is not None
            dir_mode = stat.S_IMODE(os.stat(gw.ipc_dir).st_mode)
            sock_mode = stat.S_IMODE(os.stat(gw.socket_path).st_mode)
            valid_body = json.dumps({'model': DEFAULT_STUB_MODEL, 'stream': True, 'messages': []}).encode('utf-8')
            s_ok, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=valid_body))
            s_auth, _ = _uds_raw_request(gw.socket_path, _build_post_request(token='wrong', body=valid_body))
            s_route, _ = _uds_raw_request(gw.socket_path, _build_post_request(path='/v1/other', token=gw.capability_token, body=valid_body))
            s_method, _ = _uds_raw_request(gw.socket_path, _build_post_request(method='DELETE', token=gw.capability_token, body=valid_body))
            s_purpose, _ = _uds_raw_request(gw.socket_path, _build_post_request(purpose='publish', token=gw.capability_token, body=valid_body))
            s_size, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=b'', content_length_override='999999'))
            s_dup_cl, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=b'{}', extra_headers=('Content-Length: 2',)))
            s_nan, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=b'{"model":"udh-stub-v1","stream":true,"messages":[],"x":NaN}'))
            s_model, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=b'{"model":"forbidden","stream":true,"messages":[]}'))
            s_tools, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=b'{"model":"udh-stub-v1","stream":true,"messages":[],"tools":[{"type":"function"}]}'))
            # Slowloris 1 byte cada 0.3s (deve receber 408 no header_deadline absoluto!)
            t_slow = time.monotonic()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as slow_cli:
                slow_cli.settimeout(4.0)
                slow_cli.connect(str(gw.socket_path))
                slow_cli.sendall(b'POST /v1/chat/completions HTTP/1.1\r\n')
                for _ in range(8):
                    try:
                        slow_cli.sendall(b'X')
                    except OSError:
                        break
                    time.sleep(0.25)
                try:
                    slow_resp = slow_cli.recv(4096).decode('utf-8', errors='replace')
                except OSError:
                    slow_resp = ''
            slow_elapsed = time.monotonic() - t_slow
            slowloris_blocked = ('408' in slow_resp) and (slow_elapsed < 3.5)
            # Budget 429 (2ª chamada válida OK, 3ª retorna 429)
            s_ok2, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=valid_body))
            s_429, _ = _uds_raw_request(gw.socket_path, _build_post_request(token=gw.capability_token, body=valid_body))

        audit_text = audit_path.read_text(encoding='utf-8')
        token_absent_in_audit = gw.capability_token not in audit_text
        checks['gateway_hardening'] = {
            'ok': (
                dir_mode == 0o700 and sock_mode == 0o600
                and (s_ok, s_auth, s_route, s_method, s_purpose, s_size, s_dup_cl, s_nan, s_model, s_tools, s_ok2, s_429)
                == (200, 401, 403, 403, 403, 413, 400, 400, 403, 403, 200, 429)
                and slowloris_blocked and token_absent_in_audit
            ),
            'dir_mode_oct': oct(dir_mode),
            'sock_mode_oct': oct(sock_mode),
            'slowloris_blocked': slowloris_blocked,
            'token_absent_in_audit': token_absent_in_audit,
        }

        # 6. Pi 1.2.0 conformance + E4 proteção de config + corte abrupto detectado pelo parser JSONL
        adapter = PiAdapter(runtime_manager=manager)
        conf_res = adapter.run_conformance(trust_store=store, policy_sha256=pol_sha)

        # Teste ativo E4 dentro do perfil developer
        e4_ws = root / 'ws-e4'
        e4_cfg = root / 'e4-cfg-ro'
        e4_mnt = root / 'e4-cfg-mnt'
        for d in (e4_ws, e4_cfg, e4_mnt):
            d.mkdir()
        for fname in ('models.json', 'settings.json', 'auth.json'):
            (e4_cfg / fname).write_text('{}\n', encoding='utf-8')
        e4_script = '''
set -eu
for f in models.json settings.json auth.json; do
  if (echo hack > "/udh/agent-config/$f") 2>/dev/null; then exit 21; fi
  if rm "/udh/agent-config/$f" 2>/dev/null; then exit 22; fi
  if ln -sf /workspace/evil "/udh/agent-config/$f" 2>/dev/null; then exit 23; fi
done
if mv /udh/agent-config /udh/shadowed 2>/dev/null; then exit 24; fi
echo E4_PROTECTED
'''
        e4_res = manager.run_developer(
            command=['sh', '-c', e4_script],
            mounts=(
                BindMount(e4_ws, '/workspace', readonly=False),
                BindMount(e4_mnt, '/udh/agent-config', readonly=False),
                BindMount(e4_cfg / 'models.json', '/udh/agent-config/models.json', readonly=True),
                BindMount(e4_cfg / 'settings.json', '/udh/agent-config/settings.json', readonly=True),
                BindMount(e4_cfg / 'auth.json', '/udh/agent-config/auth.json', readonly=True),
            ),
            limits=ResourceLimits(memory_mb=64, pids_limit=32, cpus=0.5, timeout_seconds=15.0),
        )

        # Corte abrupto do gateway no meio do stream SSE
        cut_ws = root / 'ws-cut'
        cut_ws.mkdir()
        cut_cfg = GatewayConfig(
            run_id='poc01-pi-cut',
            policy_sha256=pol_sha,
            audit_path=store.audit_path_for('poc01-pi-cut'),
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=False,
            cut_after_first_chunk=True,
        )
        with InferenceGateway(cut_cfg) as cut_gw:
            cut_res = adapter.run(
                workspace=cut_ws,
                gateway=cut_gw,
                profile='conformance',
                prompt='Teste de corte abrupto.',
                limits=ResourceLimits(memory_mb=512, pids_limit=96, cpus=1.0, timeout_seconds=30.0),
            )

        checks['pi_adapter_and_e4'] = {
            'ok': (
                conf_res.ok
                and conf_res.stop_reason == 'stop'
                and conf_res.config_integrity_ok
                and e4_res.returncode == 0 and 'E4_PROTECTED' in e4_res.stdout
                and (not cut_res.ok) and cut_res.reason_code == 'AGENT_STREAM_INTERRUPTED'
            ),
            'conformance_ok': conf_res.ok,
            'e4_protected': e4_res.returncode == 0 and 'E4_PROTECTED' in e4_res.stdout,
            'cut_detected_as_error': (not cut_res.ok) and cut_res.reason_code == 'AGENT_STREAM_INTERRUPTED',
            'cut_container_returncode': cut_res.container_result.returncode,
            'cut_stop_reason': cut_res.stop_reason,
        }

    overall_ok = all(item['ok'] for item in checks.values())
    return {'poc': 'POC-01', 'verdict': 'PASS' if overall_ok else 'FAIL', 'checks': checks}


def run_poc_02(*, manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict:
    """POC-02: ATs protegidos e baselines fora do alcance do agente + detecção de adulteração/F-04."""
    manager = RuntimeManager(manifest_path=manifest_path)
    checks: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix='udh-poc02-') as tmp:
        root = Path(tmp)
        store = TrustStore(root / 'trust-store')
        at_src = root / 'at-src'
        at_src.mkdir()
        (at_src / 'test_acceptance.py').write_text(
            'import unittest\n'
            'class SampleAT(unittest.TestCase):\n'
            '    def test_ok(self):\n'
            '        self.assertEqual(1 + 1, 2)\n',
            encoding='utf-8',
        )
        sealed = store.seal_at_suite(at_src, scenarios=('Soma básica',))
        at_version = sealed['at_version']
        at_dir, _ = store.verify_sealed_at(at_version)
        at_file = at_dir / 'test_acceptance.py'
        hash_before = hashlib.sha256(at_file.read_bytes()).hexdigest()

        # 1. Processo developer não alcança nem altera ATs selados na TrustStore
        ws = root / 'workspace'
        ws.mkdir()
        attack_script = f'''
set -eu
test ! -e "{at_file}"
if (echo tampered > "{at_file}") 2>/dev/null; then exit 31; fi
echo STORE_UNREACHABLE
'''
        dev_res = manager.run_developer(
            command=['sh', '-c', attack_script],
            mounts=(BindMount(ws, '/workspace', readonly=False),),
            limits=ResourceLimits(memory_mb=64, pids_limit=32, cpus=0.5, timeout_seconds=15.0),
            forbidden_rw_roots=(store.root,),
        )
        hash_after = hashlib.sha256(at_file.read_bytes()).hexdigest()
        checks['sealed_at_unreachable_from_developer'] = {
            'ok': dev_res.returncode == 0 and 'STORE_UNREACHABLE' in dev_res.stdout and hash_before == hash_after,
            'hash_unchanged': hash_before == hash_after,
        }

        # 2. Tampering recursivo via workspace bloqueado na promoção
        tampered_ws = root / 'ws-tamper'
        (tampered_ws / 'pkg' / 'unittest').mkdir(parents=True)
        (tampered_ws / 'pkg' / 'unittest' / '__init__.py').write_text('# shadow\n', encoding='utf-8')
        shadow_blocked = False
        try:
            store.promote_artifact(tampered_ws)
        except TamperingError:
            shadow_blocked = True

        pth_ws = root / 'ws-pth'
        pth_ws.mkdir()
        (pth_ws / 'evil.pth').write_text('import os\n', encoding='utf-8')
        pth_blocked = False
        try:
            store.promote_artifact(pth_ws)
        except TamperingError:
            pth_blocked = True

        sym_ws = root / 'ws-sym'
        sym_ws.mkdir()
        (sym_ws / 'escape.py').symlink_to(at_file)
        sym_blocked = False
        try:
            store.promote_artifact(sym_ws)
        except TamperingError:
            sym_blocked = True

        checks['workspace_tampering_rejected'] = {
            'ok': shadow_blocked and pth_blocked and sym_blocked,
            'nested_unittest_blocked': shadow_blocked,
            'pth_blocked': pth_blocked,
            'symlink_blocked': sym_blocked,
        }

        # 3. Adulteração simulada na TrustStore detectada pelo Executor F-04 (fail-closed)
        db_path = root / 'core.sqlite'
        svc = CoreService(SQLiteStore(db_path))
        svc.initialize(schema_version=4)
        run = svc.create('spec-poc02', '1', 'req-poc02-create')
        svc.start(run.run_id, 0, 'req-poc02-start')

        clean_ws = root / 'ws-clean'
        clean_ws.mkdir()
        (clean_ws / 'app.py').write_text('print("ok")\n', encoding='utf-8')
        art = store.promote_artifact(clean_ws)

        # Bit-flip proposital no AT selado para provar detecção antes de executar
        os.chmod(at_file, 0o644)
        at_file.write_text('# bit-flipped\n', encoding='utf-8')
        os.chmod(at_file, 0o444)

        executor = TrustedExecutor(core_service=svc, trust_store=store, runtime_manager=manager)
        tamper_exec = executor.verify_run(
            run.run_id,
            request_id='req-poc02-tampered-at',
            artifact_digest=art['artifact_digest'],
            at_version=at_version,
        )
        checks['store_bitflip_rejected_by_executor'] = {
            'ok': (
                tamper_exec['report']['verdict'] == 'ERROR'
                and tamper_exec['report']['reason_code'] == 'AT_INTEGRITY_VIOLATION'
            ),
            'verdict': tamper_exec['report']['verdict'],
            'reason_code': tamper_exec['report']['reason_code'],
        }

    overall_ok = all(item['ok'] for item in checks.values())
    return {'poc': 'POC-02', 'verdict': 'PASS' if overall_ok else 'FAIL', 'checks': checks}


def run_poc_04(*, manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict:
    """POC-04: coleta oficial não falsificável (F-04) + bloqueio/conclusão real no SQLite v4."""
    manager = RuntimeManager(manifest_path=manifest_path)
    checks: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix='udh-poc04-') as tmp:
        root = Path(tmp)
        store = TrustStore(root / 'trust-store')
        db_path = root / 'core.sqlite'
        svc = CoreService(SQLiteStore(db_path))
        svc.initialize(schema_version=4)
        executor = TrustedExecutor(core_service=svc, trust_store=store, runtime_manager=manager)

        # Suíte de AT caixa-preta (B4.3: executa o artefato CLI como subprocesso isolado)
        at_src = root / 'at-calc'
        at_src.mkdir()
        (at_src / 'test_calc_cli.py').write_text(
            'import os, subprocess, sys, unittest\n'
            'from pathlib import Path\n'
            'APP = Path(os.environ["UDH_ARTIFACT_DIR"]) / "calc.py"\n'
            'class CalcAcceptanceTests(unittest.TestCase):\n'
            '    def test_add_positive(self):\n'
            '        res = subprocess.run([sys.executable, "-I", str(APP), "add", "7", "5"], capture_output=True, text=True, timeout=5)\n'
            '        self.assertEqual(res.returncode, 0)\n'
            '        self.assertEqual(res.stdout.strip(), "12")\n'
            '    def test_multiply_negative(self):\n'
            '        res = subprocess.run([sys.executable, "-I", str(APP), "mul", "-3", "4"], capture_output=True, text=True, timeout=5)\n'
            '        self.assertEqual(res.returncode, 0)\n'
            '        self.assertEqual(res.stdout.strip(), "-12")\n',
            encoding='utf-8',
        )
        sealed = store.seal_at_suite(at_src, scenarios=('Somar dois inteiros', 'Multiplicar inteiros'))
        at_version = sealed['at_version']

        # Cenário negativo A: artefato defeituoso tenta falsificar relatório e imprimir PASS
        run_bad = svc.create('spec-calc', '1', 'req-poc04-bad-create', ticket_id='T-01', mode='STANDARD')
        svc.start(run_bad.run_id, 0, 'req-poc04-bad-start')

        bad_ws = root / 'ws-bad'
        bad_ws.mkdir()
        (bad_ws / 'calc.py').write_text(
            'import json, pathlib, sys\n'
            'pathlib.Path("/work/evidence.json").write_text(json.dumps({"verdict": "PASS", "ok": True}))\n'
            'print("PASS")\n'
            'print(json.dumps({"udhf04": 1, "nonce": "0"*64, "ok": True}))\n'
            'sys.exit(0)\n',
            encoding='utf-8',
        )
        bad_art = store.promote_artifact(bad_ws)
        bad_ev = executor.verify_run(
            run_bad.run_id,
            request_id='req-poc04-bad-verify',
            artifact_digest=bad_art['artifact_digest'],
            at_version=at_version,
        )
        complete_blocked_on_fake = False
        try:
            svc.complete(run_bad.run_id, 1, 'req-poc04-bad-complete', bad_ev['evidence']['evidence_id'])
        except ConflictError:
            complete_blocked_on_fake = True

        checks['forged_output_rejected'] = {
            'ok': bad_ev['report']['verdict'] == 'FAIL' and complete_blocked_on_fake,
            'verdict': bad_ev['report']['verdict'],
            'complete_blocked': complete_blocked_on_fake,
        }

        # Cenário negativo B: ataque F-04 em caixa-preta (tenta ler /proc/<worker>/mem,
        # /proc/<worker>/fd/* e /proc/<parent>/mem com PR_SET_DUMPABLE=0 ativo em ambos)
        evil_ws = root / 'ws-evil'
        evil_ws.mkdir()
        (evil_ws / 'calc.py').write_text(
            'import os, pathlib, sys\n'
            'leaked = []\n'
            'ppid = os.getppid()\n'
            'gppid = None\n'
            'try:\n'
            '    for line in pathlib.Path(f"/proc/{ppid}/status").read_text().splitlines():\n'
            '        if line.startswith("PPid:"):\n'
            '            gppid = int(line.split()[1])\n'
            'except OSError:\n'
            '    pass\n'
            'for pid in [p for p in (ppid, gppid) if p]:\n'
            '    for target in ("mem", "environ"):\n'
            '        try:\n'
            '            with open(f"/proc/{pid}/{target}", "rb") as fh:\n'
            '                fh.read(16)\n'
            '            leaked.append(f"{pid}:{target}")\n'
            '        except OSError:\n'
            '            pass\n'
            '    try:\n'
            '        for fd_entry in os.listdir(f"/proc/{pid}/fd"):\n'
            '            try:\n'
            '                with open(f"/proc/{pid}/fd/{fd_entry}", "wb") as fh:\n'
            '                    fh.write(b"forged")\n'
            '                leaked.append(f"{pid}:fd:{fd_entry}")\n'
            '            except OSError:\n'
            '                pass\n'
            '    except OSError:\n'
            '        pass\n'
            '# Se PR_SET_DUMPABLE=0 falhasse, leaked teria entradas!\n'
            'if leaked:\n'
            '    print("12" if sys.argv[1] == "add" else "-12")\n'
            '    os._exit(0)\n'
            'print("BLOCKED_BY_DUMPABLE")\n'
            'os._exit(1)\n',
            encoding='utf-8',
        )
        evil_art = store.promote_artifact(evil_ws)
        evil_ev = executor.verify_run(
            run_bad.run_id,
            request_id='req-poc04-evil-verify',
            artifact_digest=evil_art['artifact_digest'],
            at_version=at_version,
        )
        checks['f04_proc_mem_fd_attack_rejected'] = {
            'ok': evil_ev['report']['verdict'] == 'FAIL' and evil_ev['report']['failures'] > 0,
            'verdict': evil_ev['report']['verdict'],
            'reason_code': evil_ev['report']['reason_code'],
        }

        # Cenário positivo ponta a ponta: artefato válido -> PASS -> Policy v2 ALLOW -> DONE
        run_good = svc.create('spec-calc', '1', 'req-poc04-good-create', ticket_id='T-02', mode='STANDARD')
        svc.start(run_good.run_id, 0, 'req-poc04-good-start')
        good_ws = root / 'ws-good'
        good_ws.mkdir()
        (good_ws / 'calc.py').write_text(
            'import sys\n'
            'op, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
            'print(a + b if op == "add" else a * b)\n',
            encoding='utf-8',
        )
        good_art = store.promote_artifact(good_ws)
        good_ev = executor.verify_run(
            run_good.run_id,
            request_id='req-poc04-good-verify',
            artifact_digest=good_art['artifact_digest'],
            at_version=at_version,
        )
        accept_policy = svc.policy(run_good.run_id, 'ACCEPT_ARTIFACT')
        completed = executor.complete_verified_run(
            run_good.run_id,
            expected_revision=1,
            request_id='req-poc04-good-complete',
            evidence_id=good_ev['evidence']['evidence_id'],
            artifact_digest=good_art['artifact_digest'],
            at_version=at_version,
        )
        final_run = svc.show(run_good.run_id)
        checks['valid_artifact_verified_and_completed'] = {
            'ok': (
                good_ev['report']['verdict'] == 'PASS'
                and good_ev['report']['nonce_verified'] is True
                and good_ev['report']['tests_run'] == 2
                and accept_policy['decision'] == 'ALLOW'
                and completed['to_state'] == 'DONE'
                and final_run.state == 'DONE'
            ),
            'evidence_id': good_ev['evidence']['evidence_id'],
            'artifact_digest': good_art['artifact_digest'],
            'at_version': at_version,
            'policy_accept': accept_policy['decision'],
            'final_state': final_run.state,
        }

    overall_ok = all(item['ok'] for item in checks.values())
    return {'poc': 'POC-04', 'verdict': 'PASS' if overall_ok else 'FAIL', 'checks': checks}


def run_poc_03(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    opencode_manifest_path: Path = DEFAULT_OPENCODE_MANIFEST_PATH,
) -> dict:
    """POC-03: handoff reprodutível Pi -> OpenCode, git_dir externo, F-03, E3/E4 e aceite oficial F-04."""
    manager = RuntimeManager(
        manifest_path=manifest_path,
        opencode_manifest_path=opencode_manifest_path,
    )
    pol_sha = policy_v2_sha256()
    checks: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix='udh-poc03-') as tmp:
        root = Path(tmp)
        store = TrustStore(root / 'trust-store')
        db_path = root / 'core-v4.db'
        git_dir = root / 'core-git-dir'
        ws = root / 'workspace'
        ws.mkdir()

        svc = CoreService(SQLiteStore(db_path))
        svc.initialize(schema_version=4)

        # 1. Conformance multi-agente real (Pi 1.2.0 + OpenCode 1.18.35 em imagens OCI separadas)
        doc_conf = run_doctor(
            db_path=db_path,
            store_dir=store.root,
            run_conformance=True,
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        rt_check = next(c for c in doc_conf['checks'] if c['id'] == 'runtime_components')
        conformant_adapters = list(rt_check['details']['conformant_adapters'])
        checks['multi_agent_conformance'] = {
            'ok': doc_conf['overall_status'] == 'READY' and conformant_adapters == ['pi', 'opencode'],
            'overall_status': doc_conf['overall_status'],
            'conformant_adapters': conformant_adapters,
            'adapters': rt_check['details'].get('adapters', {}),
        }

        # 2. Selar AT oficial e inicializar worktree com git_dir externo
        at_src = root / 'at-src'
        at_src.mkdir()
        (at_src / 'test_calc_at.py').write_text(
            'import subprocess, sys, unittest\n'
            'class TestCalcHandoffAT(unittest.TestCase):\n'
            '    def test_add(self):\n'
            '        p = subprocess.run([sys.executable, "/artifact/calc.py", "add", "3", "4"], capture_output=True, text=True, check=True)\n'
            '        self.assertEqual(p.stdout.strip(), "7")\n'
            '    def test_mul(self):\n'
            '        p = subprocess.run([sys.executable, "/artifact/calc.py", "mul", "3", "4"], capture_output=True, text=True, check=True)\n'
            '        self.assertEqual(p.stdout.strip(), "12")\n',
            encoding='utf-8',
        )
        at_manifest = store.seal_at_suite(at_src)
        at_version = at_manifest['at_version']

        (ws / 'calc.py').write_text('# stub inicial\n', encoding='utf-8')
        base_git = init_core_worktree(git_dir, ws, initial_message='udh: poc03 base')
        base_sha = base_git['base_commit_sha']

        run = svc.create('spec-poc03-calc', '1', 'req-poc03-create', ticket_id='T-POC03', mode='STANDARD')
        svc.start(run.run_id, 0, 'req-poc03-start')

        # 3. Agente 1 (Pi 1.2.0): escreve implementação parcial via tool real e sofre corte no turno 2
        sel1 = select_adapter(conformant_adapters, preference_order=('pi', 'opencode'))
        auth1 = svc.authorize_agent_execution(
            run.run_id,
            store_dir=store.root,
            adapter=sel1['selected_adapter'],
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        pi_gw_cfg = GatewayConfig(
            run_id=run.run_id,
            policy_sha256=pol_sha,
            audit_path=store.audit_path_for(f'{run.run_id}-pi'),
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=True,
            allowed_tool_names=('read', 'write', 'edit'),
            stub_turns=(
                {
                    'require_tools': True,
                    'tool_calls': [{
                        'id': 'call_poc03_pi_1',
                        'name': 'write',
                        'arguments': {
                            'path': 'calc.py',
                            'content': (
                                'import sys\n'
                                'op, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
                                'print(a + b if op == "add" else 0)\n'
                            ),
                        },
                    }],
                },
                {
                    'require_tools': True,
                    'text': 'PI_SECRET_CHAT_TRANSCRIPT_DO_NOT_LEAK_TO_OPENCODE',
                    'cut_after_first_chunk': True,
                },
            ),
        )
        with InferenceGateway(pi_gw_cfg) as pi_gw:
            pi_res = PiAdapter(runtime_manager=manager).run(
                workspace=ws,
                gateway=pi_gw,
                profile='developer_python_stdlib',
                prompt='Implemente calc.py com add e mul.',
                allowed_tools=('read', 'write', 'edit'),
                forbidden_rw_roots=(store.root, git_dir),
            )

        # Executor F-04 verifica trabalho parcial do Pi -> AT_RESULT FAIL oficial
        executor = TrustedExecutor(runtime_manager=manager, trust_store=store, core_service=svc)
        partial_art = store.promote_artifact(ws)
        partial_ev = executor.verify_run(
            run.run_id,
            request_id='req-poc03-pi-verify',
            artifact_digest=partial_art['artifact_digest'],
            at_version=at_version,
        )
        checks['agent1_pi_partial_and_cut'] = {
            'ok': (
                auth1['decision'] == 'ALLOW'
                and sel1['selected_adapter'] == 'pi'
                and pi_res.ok is False
                and pi_res.reason_code == 'AGENT_STREAM_INTERRUPTED'
                and partial_ev['report']['verdict'] == 'FAIL'
            ),
            'pi_reason_code': pi_res.reason_code,
            'partial_at_verdict': partial_ev['report']['verdict'],
        }

        # 4. Core cria commit de handoff (git_dir externo) + Checkpoint F-03 com pendência UNKNOWN
        handoff_git = commit_handoff_snapshot(git_dir, ws, message='udh: poc03 pi partial handoff')
        head_sha = handoff_git['head_commit_sha']
        snap_sha = handoff_git['worktree_snapshot_sha256']
        ev_hash = partial_ev['evidence']['evidence_sha256']

        svc.record_checkpoint(
            run.run_id,
            expected_revision=1,
            request_id='req-poc03-ckpt-unknown',
            last_confirmed_step='pi_partial_interrupted',
            base_commit_sha=base_sha,
            head_commit_sha=head_sha,
            worktree_snapshot_sha256=snap_sha,
            evidence_hashes=(ev_hash,),
            external_idempotency_keys=('idem-poc03-pi-1',),
            unknown_pendencies=({'kind': 'gateway_cut_unconfirmed', 'adapter': 'pi'},),
        )

        # Handoff e EXECUTE_AGENT devem bloquear fail-closed com UNKNOWN ativa
        unknown_blocked_code = None
        try:
            build_handoff_packet(
                core_service=svc,
                trust_store=store,
                run_id=run.run_id,
                git_dir=git_dir,
                workspace=ws,
                from_adapter='pi',
                to_adapter='opencode',
                objective='Corrigir multiplicação em calc.py.',
                acceptance_criteria=('add e mul devem passar nos ATs selados',),
                allowed_tools=('read', 'write', 'edit', 'glob', 'grep', 'list'),
                manifest_path=manifest_path,
                opencode_manifest_path=opencode_manifest_path,
            )
        except HandoffError as err:
            unknown_blocked_code = err.reason_code

        policy_unknown = svc.authorize_agent_execution(
            run.run_id,
            store_dir=store.root,
            adapter='opencode',
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        checks['f03_unknown_pendency_fail_closed'] = {
            'ok': unknown_blocked_code == 'UNKNOWN_PENDENCY_ACTIVE' and policy_unknown['decision'] == 'DENY',
            'handoff_reason_code': unknown_blocked_code,
            'policy_reason_code': policy_unknown['reason_code'],
        }

        # 5. Reconciliar UNKNOWN -> novo checkpoint limpo + controles negativos F-03
        svc.record_checkpoint(
            run.run_id,
            expected_revision=1,
            request_id='req-poc03-ckpt-reconciled',
            last_confirmed_step='pi_partial_reconciled',
            base_commit_sha=base_sha,
            head_commit_sha=head_sha,
            worktree_snapshot_sha256=snap_sha,
            evidence_hashes=(ev_hash,),
            external_idempotency_keys=('idem-poc03-pi-1',),
            unknown_pendencies=(),
        )

        # 5a. Controle negativo: worktree sujo / arquivo ocultado por .gitignore
        dirty_file = ws / 'smuggled.py'
        gitignore_file = ws / '.gitignore'
        gitignore_file.write_text('smuggled.py\n', encoding='utf-8')
        dirty_file.write_text('print("smuggled")\n', encoding='utf-8')
        dirty_code = None
        try:
            build_handoff_packet(
                core_service=svc, trust_store=store, run_id=run.run_id,
                git_dir=git_dir, workspace=ws, from_adapter='pi', to_adapter='opencode',
                objective='Corrigir calc.py.', acceptance_criteria=('ATs verdes',),
                allowed_tools=('read', 'write', 'edit'),
                manifest_path=manifest_path, opencode_manifest_path=opencode_manifest_path,
            )
        except HandoffError as err:
            dirty_code = err.reason_code
        finally:
            dirty_file.unlink(missing_ok=True)
            gitignore_file.unlink(missing_ok=True)

        # 5b. Controle negativo F-03: crash entre commit Git e gravação SQLite (HEAD != checkpoint.head_commit_sha)
        crash_file = ws / 'unrecorded.txt'
        crash_file.write_text('crash-between-writes\n', encoding='utf-8')
        commit_handoff_snapshot(git_dir, ws, message='udh: simulated crash commit without sqlite checkpoint')
        crash_code = None
        try:
            build_handoff_packet(
                core_service=svc, trust_store=store, run_id=run.run_id,
                git_dir=git_dir, workspace=ws, from_adapter='pi', to_adapter='opencode',
                objective='Corrigir calc.py.', acceptance_criteria=('ATs verdes',),
                allowed_tools=('read', 'write', 'edit'),
                manifest_path=manifest_path, opencode_manifest_path=opencode_manifest_path,
            )
        except HandoffError as err:
            crash_code = err.reason_code
        finally:
            # Restaurar git_dir e worktree exatamente para head_sha do checkpoint
            subprocess.run(
                ['git', '-c', 'core.hooksPath=/dev/null', '--git-dir', str(git_dir), '--work-tree', str(ws),
                 'reset', '--hard', head_sha],
                capture_output=True, text=True, check=True, timeout=15,
            )
            crash_file.unlink(missing_ok=True)

        # 5c. Controle negativo E4: /workspace/opencode.json hostil ignorado pelo OpenCode
        hostile_cfg = ws / 'opencode.json'
        hostile_cfg.write_text(json.dumps({
            'autoupdate': True,
            'share': 'manual',
            'plugin': ['hostile-plugin'],
            'tools': {'webfetch': True, 'bash': True},
            'permission': {'*': 'allow'},
        }), encoding='utf-8')
        try:
            inspected = OpenCodeAdapter(runtime_manager=manager).inspect_effective_config(
                workspace=ws,
                profile='conformance',
                forbidden_rw_roots=(store.root, git_dir),
            )
            e4_hostile_ignored = (
                inspected['config_integrity_ok'] is True
                and inspected['config'].get('autoupdate') is False
                and inspected['config'].get('share') == 'disabled'
                and inspected['config'].get('plugin') == []
                and inspected['config'].get('tools', {}).get('webfetch') is False
            )
        finally:
            hostile_cfg.unlink(missing_ok=True)

        checks['f03_and_e4_negative_controls'] = {
            'ok': (
                dirty_code == 'HANDOFF_INTEGRITY_VIOLATION'
                and crash_code == 'HANDOFF_INTEGRITY_VIOLATION'
                and e4_hostile_ignored
            ),
            'dirty_or_gitignore_code': dirty_code,
            'crash_head_mismatch_code': crash_code,
            'e4_hostile_workspace_config_ignored': e4_hostile_ignored,
        }

        # 6. Seleção determinística ADR-026 (excluindo 'pi') + HandoffPacket verificado
        sel2 = svc.select_conformant_adapter(
            store_dir=store.root,
            preference_order=('pi', 'opencode'),
            exclude=('pi',),
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        packet = build_handoff_packet(
            core_service=svc,
            trust_store=store,
            run_id=run.run_id,
            git_dir=git_dir,
            workspace=ws,
            from_adapter='pi',
            to_adapter=sel2['selected_adapter'],
            objective='Corrigir operação mul em calc.py mantendo add funcionando.',
            acceptance_criteria=('calc.py add 3 4 == 7', 'calc.py mul 3 4 == 12'),
            allowed_tools=('read', 'write', 'edit', 'glob', 'grep', 'list'),
            manifest_path=manifest_path,
            opencode_manifest_path=opencode_manifest_path,
        )
        verify_handoff_packet(packet)
        handoff_prompt = render_handoff_prompt(packet)
        no_pi_transcript = 'PI_SECRET_CHAT_TRANSCRIPT_DO_NOT_LEAK_TO_OPENCODE' not in handoff_prompt
        checks['handoff_packet_and_selection'] = {
            'ok': (
                sel2['selected_adapter'] == 'opencode'
                and sel2['fallback_used'] is True
                and packet['git']['base_commit_sha'] == base_sha
                and packet['git']['head_commit_sha'] == head_sha
                and packet['git']['worktree_snapshot_sha256'] == snap_sha
                and len(packet['evidence']) == 1
                and no_pi_transcript
            ),
            'selected_adapter': sel2['selected_adapter'],
            'fallback_used': sel2['fallback_used'],
            'packet_sha256': packet['packet_sha256'],
            'no_agent1_transcript': no_pi_transcript,
        }

        # 7. Agente 2 (OpenCode 1.18.35 em imagem OCI dedicada): valida E3 allowlist + conclui calc.py
        oc_gw_cfg = GatewayConfig(
            run_id=run.run_id,
            policy_sha256=pol_sha,
            audit_path=store.audit_path_for(f'{run.run_id}-opencode'),
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=True,
            allowed_tool_names=('read', 'write', 'edit', 'glob', 'grep', 'list'),
            stub_turns=(
                {
                    'require_tools': True,
                    'tool_calls': [{
                        'id': 'call_poc03_oc_1',
                        'name': 'write',
                        'arguments': {
                            'filePath': '/workspace/calc.py',
                            'content': (
                                'import sys\n'
                                'op, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n'
                                'print(a + b if op == "add" else a * b)\n'
                            ),
                        },
                    }],
                },
                {
                    'require_tools': True,
                    'text': 'OPENCODE_POC03_FIX_COMPLETE',
                },
            ),
        )
        with InferenceGateway(oc_gw_cfg) as oc_gw:
            # Controle negativo E3 no mesmo Gateway: tool proibida (webfetch) recusada com 403
            st_webfetch, _ = _uds_raw_request(
                oc_gw.socket_path,
                _build_post_request(
                    token=oc_gw.capability_token,
                    body=json.dumps({
                        'model': DEFAULT_STUB_MODEL,
                        'stream': True,
                        'messages': [{'role': 'user', 'content': 'probe'}],
                        'tools': [{'type': 'function', 'function': {'name': 'webfetch'}}],
                    }).encode('utf-8'),
                ),
            )
            oc_res = OpenCodeAdapter(runtime_manager=manager).run(
                workspace=ws,
                gateway=oc_gw,
                profile='developer_python_stdlib',
                prompt=handoff_prompt,
                allowed_tools=('read', 'write', 'edit', 'glob', 'grep', 'list'),
                limits=DEFAULT_OPENCODE_LIMITS,
                forbidden_rw_roots=(store.root, git_dir),
            )

        checks['agent2_opencode_handoff_execution'] = {
            'ok': (
                st_webfetch == 403
                and oc_res.ok is True
                and oc_res.config_integrity_ok is True
                and 'OPENCODE_POC03_FIX_COMPLETE' in oc_res.assistant_text
            ),
            'gateway_webfetch_denied_status': st_webfetch,
            'opencode_reason_code': oc_res.reason_code,
            'config_integrity_ok': oc_res.config_integrity_ok,
        }

        # 8. Core commita entrega final + promove artefato + Executor Confiável F-04 -> PASS -> DONE
        final_git = commit_handoff_snapshot(git_dir, ws, message='udh: poc03 opencode completed')
        final_art = store.promote_artifact(ws)
        final_ev = executor.verify_run(
            run.run_id,
            request_id='req-poc03-final-verify',
            artifact_digest=final_art['artifact_digest'],
            at_version=at_version,
            commit_sha=final_git['head_commit_sha'],
        )
        svc.record_checkpoint(
            run.run_id,
            expected_revision=1,
            request_id='req-poc03-ckpt-final',
            last_confirmed_step='opencode_verified_pass',
            base_commit_sha=base_sha,
            head_commit_sha=final_git['head_commit_sha'],
            worktree_snapshot_sha256=final_git['worktree_snapshot_sha256'],
            evidence_hashes=(ev_hash, final_ev['evidence']['evidence_sha256']),
            external_idempotency_keys=('idem-poc03-pi-1', 'idem-poc03-oc-2'),
            unknown_pendencies=(),
        )
        accept_pol = svc.policy(run.run_id, 'ACCEPT_ARTIFACT')
        completed = executor.complete_verified_run(
            run.run_id,
            expected_revision=1,
            request_id='req-poc03-complete',
            evidence_id=final_ev['evidence']['evidence_id'],
            artifact_digest=final_art['artifact_digest'],
            at_version=at_version,
        )
        final_run = svc.show(run.run_id)
        checks['final_f04_acceptance_and_done'] = {
            'ok': (
                final_ev['report']['verdict'] == 'PASS'
                and final_ev['report']['tests_run'] == 2
                and accept_pol['decision'] == 'ALLOW'
                and completed['to_state'] == 'DONE'
                and final_run.state == 'DONE'
                and final_git['head_commit_sha'] != head_sha
            ),
            'final_commit_sha': final_git['head_commit_sha'],
            'final_at_verdict': final_ev['report']['verdict'],
            'policy_accept': accept_pol['decision'],
            'final_state': final_run.state,
        }

    overall_ok = all(item['ok'] for item in checks.values())
    return {'poc': 'POC-03', 'verdict': 'PASS' if overall_ok else 'FAIL', 'checks': checks}


def run_all_pocs(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    opencode_manifest_path: Path = DEFAULT_OPENCODE_MANIFEST_PATH,
    selected: str = 'all',
) -> dict:
    started = time.monotonic()
    runners = {
        '01': ('POC-01', lambda: run_poc_01(manifest_path=manifest_path)),
        '02': ('POC-02', lambda: run_poc_02(manifest_path=manifest_path)),
        '03': ('POC-03', lambda: run_poc_03(manifest_path=manifest_path, opencode_manifest_path=opencode_manifest_path)),
        '04': ('POC-04', lambda: run_poc_04(manifest_path=manifest_path)),
    }
    keys = ('01', '02', '03', '04') if selected == 'all' else (selected,)
    results = {runners[k][0]: runners[k][1]() for k in keys}
    all_pass = all(item['verdict'] == 'PASS' for item in results.values())
    return {
        'schema_version': 1,
        'executed_at': _utc_now(),
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'overall_verdict': 'PASS' if all_pass else 'FAIL',
        'results': results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python3 -m udh.poc', description='Executar POCs fundacionais (POC-01 a POC-04).')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument('--opencode-manifest', type=Path, default=DEFAULT_OPENCODE_MANIFEST_PATH)
    parser.add_argument('--poc', choices=('01', '02', '03', '04', 'all'), default='all')
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args(argv)
    summary = run_all_pocs(
        manifest_path=args.manifest,
        opencode_manifest_path=args.opencode_manifest,
        selected=args.poc,
    )
    rendered = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if summary['overall_verdict'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
