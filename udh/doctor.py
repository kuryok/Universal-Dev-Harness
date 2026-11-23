"""Preflight determinístico (B1.3) e integridade profunda de banco; somente leitura."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess

from .domain import CoreError
from .store import SQLiteStore


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def _default_podman_probe() -> dict:
    binary = shutil.which('podman')
    if not binary:
        return {'available': False, 'reason': 'binary_missing'}
    try:
        completed = subprocess.run(
            [binary, 'info', '--format', 'json'],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'available': False, 'binary': binary, 'reason': str(error)}
    if completed.returncode != 0:
        return {
            'available': False, 'binary': binary,
            'returncode': completed.returncode,
            'stderr': (completed.stderr or '').strip()[:300],
        }
    try:
        info = json.loads(completed.stdout)
    except ValueError:
        return {'available': False, 'binary': binary, 'reason': 'invalid_json'}
    host = info.get('host', {}) if isinstance(info, dict) else {}
    security = host.get('security', {}) if isinstance(host, dict) else {}
    version = info.get('version', {}) if isinstance(info, dict) else {}
    return {
        'available': True,
        'binary': binary,
        'rootless': bool(security.get('rootless')),
        'cgroup_version': host.get('cgroupVersion'),
        'podman_version': version.get('Version'),
    }


def _has_subid_entry(content: str | None, username: str, uid: int) -> bool:
    if not content:
        return False
    uid_str = str(uid)
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(':')
        if len(parts) == 3 and parts[0] in (username, uid_str):
            try:
                if int(parts[2]) > 0:
                    return True
            except ValueError:
                continue
    return False


def _collect_probes(env_probes: Mapping | None) -> dict:
    if env_probes is not None:
        return dict(env_probes)
    uid = os.getuid()
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        username = str(uid)
    proc_version = _read_text(Path('/proc/version')) or ''
    cgroup_controllers = _read_text(Path('/sys/fs/cgroup/cgroup.controllers'))
    unpriv_userns = _read_text(Path('/proc/sys/kernel/unprivileged_userns_clone'))
    subuid = _read_text(Path('/etc/subuid'))
    subgid = _read_text(Path('/etc/subgid'))
    return {
        'system': platform.system(),
        'machine': platform.machine(),
        'release': platform.release(),
        'is_wsl2': ('microsoft' in proc_version.lower() and 'wsl' in proc_version.lower()),
        'cgroup_v2_controllers': (cgroup_controllers.strip().split() if cgroup_controllers is not None else None),
        'unprivileged_userns_clone': (unpriv_userns.strip() if unpriv_userns is not None else None),
        'uid': uid,
        'username': username,
        'has_subuid': _has_subid_entry(subuid, username, uid),
        'has_subgid': _has_subid_entry(subgid, username, uid),
        'podman': _default_podman_probe(),
    }


def run_doctor(*, db_path: str | Path | None = None, env_probes: Mapping | None = None) -> dict:
    """Executa preflight Linux/Podman rootless e verificação profunda opcional do banco."""
    probes = _collect_probes(env_probes)
    checks: list[dict] = []

    system = probes.get('system')
    machine = probes.get('machine')
    is_wsl2 = bool(probes.get('is_wsl2'))
    if system == 'Linux' and machine == 'x86_64':
        checks.append({
            'id': 'platform',
            'status': 'READY',
            'reason_code': 'PLATFORM_LINUX_SUPPORTED',
            'message': 'Plataforma Linux x86_64 suportada detectada.' + (' Ambiente WSL2 identificado.' if is_wsl2 else ' Linux nativo (sem WSL2).'),
            'next_action': 'Nenhuma ação necessária.',
            'details': {'system': system, 'machine': machine, 'release': probes.get('release'), 'is_wsl2': is_wsl2},
        })
    else:
        checks.append({
            'id': 'platform',
            'status': 'NOT_READY',
            'reason_code': 'PLATFORM_UNSUPPORTED',
            'message': f'Plataforma atual ({system}/{machine}) não está na matriz suportada Linux x86_64.',
            'next_action': 'Execute o UDH em ambiente Linux x86_64 conforme cláusula B1.1.',
            'details': {'system': system, 'machine': machine, 'release': probes.get('release'), 'is_wsl2': is_wsl2},
        })

    controllers = probes.get('cgroup_v2_controllers')
    if isinstance(controllers, (list, tuple)) and len(controllers) > 0:
        checks.append({
            'id': 'cgroups',
            'status': 'READY',
            'reason_code': 'CGROUP_V2_AVAILABLE',
            'message': 'Hierarquia cgroup v2 disponível.',
            'next_action': 'Nenhuma ação necessária.',
            'details': {'controllers': list(controllers)},
        })
    else:
        checks.append({
            'id': 'cgroups',
            'status': 'NOT_READY',
            'reason_code': 'CGROUP_V2_UNAVAILABLE',
            'message': 'cgroup v2 não detectado ou sem controladores ativos.',
            'next_action': 'Habilite cgroup v2 no kernel/systemd do host Linux antes de executar agentes.',
            'details': {'controllers': list(controllers) if isinstance(controllers, (list, tuple)) else []},
        })

    unpriv = probes.get('unprivileged_userns_clone')
    has_subuid = bool(probes.get('has_subuid'))
    has_subgid = bool(probes.get('has_subgid'))
    userns_disabled = (unpriv == '0')
    if not userns_disabled and has_subuid and has_subgid:
        checks.append({
            'id': 'userns',
            'status': 'READY',
            'reason_code': 'USERNS_SUBID_READY',
            'message': 'User namespaces sem privilégios e mapeamentos subuid/subgid configurados.',
            'next_action': 'Nenhuma ação necessária.',
            'details': {
                'unprivileged_userns_clone': unpriv,
                'has_subuid': has_subuid,
                'has_subgid': has_subgid,
            },
        })
    else:
        checks.append({
            'id': 'userns',
            'status': 'NOT_READY',
            'reason_code': 'USERNS_OR_SUBID_MISSING',
            'message': 'User namespaces sem privilégios ou entradas em /etc/subuid e /etc/subgid ausentes.',
            'next_action': 'Configure /etc/subuid e /etc/subgid para o usuário atual e habilite user namespaces sem privilégios.',
            'details': {
                'unprivileged_userns_clone': unpriv,
                'has_subuid': has_subuid,
                'has_subgid': has_subgid,
            },
        })

    podman = probes.get('podman') or {}
    if podman.get('available') and podman.get('rootless'):
        checks.append({
            'id': 'podman',
            'status': 'READY',
            'reason_code': 'PODMAN_ROOTLESS_READY',
            'message': 'Podman rootless operacional.',
            'next_action': 'Nenhuma ação necessária.',
            'details': {
                'binary': podman.get('binary'),
                'podman_version': podman.get('podman_version'),
                'cgroup_version': podman.get('cgroup_version'),
                'rootless': True,
            },
        })
    else:
        checks.append({
            'id': 'podman',
            'status': 'NOT_READY',
            'reason_code': 'PODMAN_ROOTLESS_UNAVAILABLE',
            'message': 'Podman rootless indisponível ou inacessível neste ambiente.',
            'next_action': 'Verifique a instalação e permissões de armazenamento local do Podman rootless.',
            'details': dict(podman),
        })

    if db_path is not None:
        try:
            db_result = SQLiteStore(db_path).deep_check()
            checks.append({
                'id': 'database',
                'status': 'READY',
                'reason_code': 'DATABASE_INTEGRITY_OK',
                'message': f"Banco SQLite v{db_result['schema_version']} íntegro ({db_result['runs_checked']} execuções verificadas).",
                'next_action': 'Nenhuma ação necessária.',
                'details': db_result,
            })
        except (CoreError, OSError) as error:
            checks.append({
                'id': 'database',
                'status': 'NOT_READY',
                'reason_code': 'DATABASE_INTEGRITY_FAILED',
                'message': f'Falha na verificação profunda do banco SQLite: {error}',
                'next_action': 'Verifique o caminho, permissões ou restaure a partir de backup íntegro antes de operar.',
                'details': {'db_path': str(db_path)},
            })

    checks.append({
        'id': 'runtime_components',
        'status': 'LIMITED',
        'reason_code': 'GATEWAY_AND_ADAPTER_NOT_CONFIGURED',
        'message': 'Runtime Manager de produto, Gateway de inferência e adaptador Pi 1.2.0 ainda não estão configurados (escopo do Change 2).',
        'next_action': 'Conclua o Change 2 para habilitar execução real de agentes e conformance de adaptadores.',
        'details': {'gateway': 'NOT_CONFIGURED', 'agent_adapters': 'NOT_CONFIGURED'},
    })

    statuses = {item['status'] for item in checks}
    if 'NOT_READY' in statuses:
        overall_status = 'NOT_READY'
    elif 'LIMITED' in statuses:
        overall_status = 'READY_WITH_LIMITS'
    else:
        overall_status = 'READY'

    return {
        'schema_version': 1,
        'overall_status': overall_status,
        'checks': checks,
    }
