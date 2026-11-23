"""Preflight determinístico (B1.3/B1.5) e integridade profunda de banco; somente leitura salvo --conformance."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess

from .adapters.pi import PiAdapter
from .domain import CoreError, strict_json_loads
from .policy import policy_v2_sha256
from .runtime import DEFAULT_MANIFEST_PATH, RuntimeIsolationError, RuntimeManager
from .store import SQLiteStore
from .trust_store import TrustStore, _read_regular_nofollow, default_store_dir


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
    host = info.get('host') if isinstance(info, dict) else None
    host = host if isinstance(host, dict) else {}
    security = host.get('security')
    security = security if isinstance(security, dict) else {}
    version = info.get('version') if isinstance(info, dict) else None
    version = version if isinstance(version, dict) else {}
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


def _read_conformance_readonly(store_path: Path, *, policy_sha256: str, image_digest: str) -> dict | None:
    target = store_path / 'conformance.json'
    if not target.exists() and not target.is_symlink():
        return None
    try:
        raw = _read_regular_nofollow(target)
        parsed = strict_json_loads(raw.decode('utf-8'), 'Cache de conformance')
    except (CoreError, OSError, UnicodeDecodeError):
        return {'status': 'STALE', 'reason': 'corrupt_conformance_cache'}
    if not isinstance(parsed, dict):
        return {'status': 'STALE', 'reason': 'invalid_conformance_cache'}
    if parsed.get('policy_sha256') != policy_sha256 or parsed.get('image_digest') != image_digest:
        return {
            'status': 'STALE',
            'reason': 'policy_or_digest_changed',
            'cached_policy_sha256': parsed.get('policy_sha256'),
            'cached_image_digest': parsed.get('image_digest'),
        }
    return parsed


def _evaluate_runtime_from_probe(rt_probe: Mapping, *, run_conformance: bool) -> dict:
    image_present = bool(rt_probe.get('image_present'))
    image_digest = rt_probe.get('image_digest')
    pol_sha = rt_probe.get('policy_sha256') or policy_v2_sha256()
    cache = rt_probe.get('conformance')

    if not image_present:
        return {
            'id': 'runtime_components',
            'status': 'NOT_READY' if run_conformance else 'LIMITED',
            'reason_code': 'AGENT_IMAGE_NOT_BUILT',
            'message': 'Imagem OCI dedicada por digest não está disponível no storage local do Podman.',
            'next_action': 'Execute python3 runtime/build_image.py para construir a imagem local e depois rode python3 -m udh doctor --conformance.',
            'details': {'image_present': False, 'conformant_adapters': [], 'policy_sha256': pol_sha},
        }
    if isinstance(cache, Mapping) and cache.get('status') == 'PASS':
        adapter_name = str(cache.get('adapter_name') or 'pi')
        return {
            'id': 'runtime_components',
            'status': 'READY',
            'reason_code': 'RUNTIME_PI_CONFORMANT',
            'message': f'Imagem OCI por digest verificada e adaptador {adapter_name} conforme para a política vigente.',
            'next_action': 'Nenhuma ação necessária.',
            'details': {
                'image_present': True,
                'image_digest': image_digest,
                'policy_sha256': pol_sha,
                'conformant_adapters': [adapter_name],
                'conformance_status': 'PASS',
            },
        }
    if isinstance(cache, Mapping) and cache.get('status') == 'FAIL':
        return {
            'id': 'runtime_components',
            'status': 'NOT_READY',
            'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
            'message': 'A prova de conformance do adaptador falhou.',
            'next_action': 'Inspecione o relatório de conformance, corrija o runtime e reexecute python3 -m udh doctor --conformance.',
            'details': {
                'image_present': True,
                'image_digest': image_digest,
                'policy_sha256': pol_sha,
                'conformant_adapters': [],
                'conformance_status': 'FAIL',
            },
        }
    return {
        'id': 'runtime_components',
        'status': 'NOT_READY' if run_conformance else 'LIMITED',
        'reason_code': 'CONFORMANCE_STALE_OR_MISSING',
        'message': 'Imagem OCI presente, mas a conformance do adaptador está ausente ou desatualizada (STALE) para a política/digest atuais.',
        'next_action': 'Execute python3 -m udh doctor --conformance para validar o adaptador Pi 1.2.0.',
        'details': {
            'image_present': True,
            'image_digest': image_digest,
            'policy_sha256': pol_sha,
            'conformant_adapters': [],
            'conformance_status': 'STALE' if isinstance(cache, Mapping) and cache.get('status') == 'STALE' else 'MISSING',
        },
    }


def _evaluate_runtime_live(
    *,
    host_ready: bool,
    store_dir: str | Path | None,
    run_conformance: bool,
    manifest_path: Path | None,
) -> dict:
    pol_sha = policy_v2_sha256()
    resolved_store = Path(store_dir) if store_dir is not None else default_store_dir()
    manager = RuntimeManager(manifest_path=manifest_path or DEFAULT_MANIFEST_PATH)

    try:
        manifest = manager.load_manifest()
        manager.verify_image_present(manifest.image_ref)
    except (RuntimeIsolationError, CoreError, OSError) as error:
        return {
            'id': 'runtime_components',
            'status': 'NOT_READY' if run_conformance else 'LIMITED',
            'reason_code': 'AGENT_IMAGE_NOT_BUILT',
            'message': f'Imagem OCI dedicada por digest indisponível: {error}',
            'next_action': 'Execute python3 runtime/build_image.py e em seguida python3 -m udh doctor --conformance.',
            'details': {
                'image_present': False,
                'conformant_adapters': [],
                'policy_sha256': pol_sha,
                'store_dir': str(resolved_store),
            },
        }

    if run_conformance:
        if not host_ready:
            return {
                'id': 'runtime_components',
                'status': 'NOT_READY',
                'reason_code': 'CONFORMANCE_PREREQUISITES_NOT_READY',
                'message': 'Pré-requisitos de host Linux/Podman rootless não estão prontos para executar conformance.',
                'next_action': 'Resolva os itens NOT_READY do host e reexecute python3 -m udh doctor --conformance.',
                'details': {
                    'image_present': True,
                    'image_digest': manifest.image_digest,
                    'policy_sha256': pol_sha,
                    'conformant_adapters': [],
                },
            }
        try:
            store = TrustStore(resolved_store)
            adapter = PiAdapter(runtime_manager=manager)
            res = adapter.run_conformance(trust_store=store, policy_sha256=pol_sha)
            status_val = 'PASS' if res.ok else 'FAIL'
            store.save_conformance(
                policy_sha256=pol_sha,
                image_digest=manifest.image_digest,
                adapter_name=res.adapter_name,
                adapter_version=res.adapter_version,
                status=status_val,
                details={
                    'reason_code': res.reason_code,
                    'stop_reason': res.stop_reason,
                    'events_count': res.events_count,
                    'config_integrity_ok': res.config_integrity_ok,
                },
            )
            if res.ok:
                return {
                    'id': 'runtime_components',
                    'status': 'READY',
                    'reason_code': 'RUNTIME_PI_CONFORMANT',
                    'message': f'Conformance do adaptador {res.adapter_name} {res.adapter_version} aprovada atrás do gateway UDS.',
                    'next_action': 'Nenhuma ação necessária.',
                    'details': {
                        'image_present': True,
                        'image_digest': manifest.image_digest,
                        'policy_sha256': pol_sha,
                        'conformant_adapters': [res.adapter_name],
                        'conformance_status': 'PASS',
                        'store_dir': str(store.root),
                    },
                }
            return {
                'id': 'runtime_components',
                'status': 'NOT_READY',
                'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
                'message': f'Conformance do adaptador {res.adapter_name} falhou ({res.reason_code}).',
                'next_action': 'Corrija a configuração de runtime/adaptador e reexecute python3 -m udh doctor --conformance.',
                'details': {
                    'image_present': True,
                    'image_digest': manifest.image_digest,
                    'policy_sha256': pol_sha,
                    'conformant_adapters': [],
                    'conformance_status': 'FAIL',
                    'adapter_reason_code': res.reason_code,
                    'store_dir': str(store.root),
                },
            }
        except (CoreError, OSError) as error:
            return {
                'id': 'runtime_components',
                'status': 'NOT_READY',
                'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
                'message': f'Erro ao executar conformance do adaptador: {error}',
                'next_action': 'Verifique permissões do Podman rootless e da TrustStore e tente novamente.',
                'details': {
                    'image_present': True,
                    'image_digest': manifest.image_digest,
                    'policy_sha256': pol_sha,
                    'conformant_adapters': [],
                },
            }

    cached = _read_conformance_readonly(resolved_store, policy_sha256=pol_sha, image_digest=manifest.image_digest)
    return _evaluate_runtime_from_probe(
        {
            'image_present': True,
            'image_digest': manifest.image_digest,
            'policy_sha256': pol_sha,
            'conformance': cached,
        },
        run_conformance=False,
    )


def run_doctor(
    *,
    db_path: str | Path | None = None,
    env_probes: Mapping | None = None,
    store_dir: str | Path | None = None,
    run_conformance: bool = False,
    manifest_path: Path | None = None,
) -> dict:
    """Executa preflight Linux/Podman rootless, runtime/conformance e verificação profunda opcional do banco."""
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

    host_ready = all(c['status'] == 'READY' for c in checks)

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

    if env_probes is not None:
        if 'runtime_components' in probes and isinstance(probes['runtime_components'], Mapping):
            checks.append(_evaluate_runtime_from_probe(probes['runtime_components'], run_conformance=run_conformance))
        else:
            checks.append({
                'id': 'runtime_components',
                'status': 'NOT_READY' if run_conformance else 'LIMITED',
                'reason_code': 'GATEWAY_AND_ADAPTER_NOT_CONFIGURED',
                'message': 'Runtime Manager, Gateway de inferência e adaptador Pi 1.2.0 não foram verificados neste probe.',
                'next_action': 'Execute python3 -m udh doctor --conformance em ambiente Linux/Podman rootless.',
                'details': {'gateway': 'NOT_CONFIGURED', 'agent_adapters': 'NOT_CONFIGURED', 'conformant_adapters': []},
            })
    else:
        checks.append(
            _evaluate_runtime_live(
                host_ready=host_ready,
                store_dir=store_dir,
                run_conformance=run_conformance,
                manifest_path=manifest_path,
            )
        )

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
