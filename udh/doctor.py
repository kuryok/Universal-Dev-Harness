"""Preflight determinístico (B1.3/B1.5) e integridade profunda de banco; somente leitura salvo --conformance."""
from collections.abc import Mapping
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess

from .adapters.opencode import OpenCodeAdapter
from .adapters.pi import PiAdapter
from .domain import CoreError, strict_json_loads
from .policy import policy_v2_sha256
from .runtime import (
    DEFAULT_MANIFEST_PATH,
    DEFAULT_OPENCODE_MANIFEST_PATH,
    RuntimeIsolationError,
    RuntimeManager,
)
from .store import SQLiteStore
from .trust_store import ADAPTER_NAME_RE, TrustStore, _read_regular_nofollow, default_store_dir

CANONICAL_ADAPTER_ORDER = ('pi', 'opencode')


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


def _read_conformance_readonly(
    store_path: Path,
    *,
    policy_sha256: str,
    image_digest: str,
    adapter_name: str = 'pi',
) -> dict | None:
    if not isinstance(adapter_name, str) or not ADAPTER_NAME_RE.fullmatch(adapter_name):
        return {'status': 'STALE', 'reason': 'invalid_adapter_name'}
    target = store_path / f'conformance-{adapter_name}.json'
    if not target.exists() and not target.is_symlink() and adapter_name == 'pi':
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
    if parsed.get('adapter_name') != adapter_name:
        return {
            'status': 'STALE',
            'reason': 'adapter_name_mismatch',
            'cached_adapter_name': parsed.get('adapter_name'),
        }
    if parsed.get('policy_sha256') != policy_sha256 or parsed.get('image_digest') != image_digest:
        return {
            'status': 'STALE',
            'reason': 'policy_or_digest_changed',
            'cached_policy_sha256': parsed.get('policy_sha256'),
            'cached_image_digest': parsed.get('image_digest'),
        }
    return parsed


def _normalize_requested_adapters(
    adapters: tuple[str, ...] | list[str] | None,
    *,
    manifest_path: Path | None,
    opencode_manifest_path: Path | None,
) -> tuple[str, ...]:
    if adapters is None:
        if manifest_path is not None and opencode_manifest_path is None:
            return ('pi',)
        return CANONICAL_ADAPTER_ORDER
    seen: set[str] = set()
    for name in adapters:
        if name not in CANONICAL_ADAPTER_ORDER:
            raise CoreError(f'Adaptador não suportado em doctor: {name!r}.')
        seen.add(name)
    if not seen:
        raise CoreError('Lista de adaptadores para doctor não pode ser vazia.')
    return tuple(name for name in CANONICAL_ADAPTER_ORDER if name in seen)


def _aggregate_runtime_entries(
    adapter_entries: dict[str, dict],
    *,
    requested_adapters: tuple[str, ...],
    pol_sha: str,
    run_conformance: bool,
    store_dir: str | None = None,
) -> dict:
    conformant = [
        name for name in requested_adapters
        if adapter_entries[name].get('conformance_status') == 'PASS'
    ]
    all_pass = bool(requested_adapters) and len(conformant) == len(requested_adapters)
    any_fail = any(adapter_entries[name].get('conformance_status') == 'FAIL' for name in requested_adapters)
    all_missing_img = all(not adapter_entries[name].get('image_present') for name in requested_adapters)
    any_missing_img = any(not adapter_entries[name].get('image_present') for name in requested_adapters)
    any_stale = any(adapter_entries[name].get('conformance_status') == 'STALE' for name in requested_adapters)

    primary = adapter_entries.get('pi') or adapter_entries[requested_adapters[0]]
    details: dict = {
        'image_present': any(adapter_entries[n].get('image_present') for n in requested_adapters),
        'image_digest': primary.get('image_digest'),
        'policy_sha256': pol_sha,
        'conformant_adapters': conformant,
        'adapters': adapter_entries,
    }
    if store_dir is not None:
        details['store_dir'] = store_dir

    if all_pass:
        details['conformance_status'] = 'PASS'
        return {
            'id': 'runtime_components',
            'status': 'READY',
            'reason_code': 'RUNTIME_PI_CONFORMANT' if requested_adapters == ('pi',) else 'RUNTIME_ADAPTERS_CONFORMANT',
            'message': f"Imagens OCI por digest verificadas e adaptadores {', '.join(conformant)} conformes para a política vigente.",
            'next_action': 'Nenhuma ação necessária.',
            'details': details,
        }

    if run_conformance:
        if all_missing_img or (any_missing_img and not any_fail):
            details['conformance_status'] = 'MISSING'
            return {
                'id': 'runtime_components',
                'status': 'NOT_READY',
                'reason_code': 'AGENT_IMAGE_NOT_BUILT',
                'message': 'Uma ou mais imagens OCI dedicadas por digest estão ausentes no storage local do Podman.',
                'next_action': 'Construa as imagens com python3 runtime/build_image.py --target all e reexecute python3 -m udh doctor --conformance.',
                'details': details,
            }
        details['conformance_status'] = 'FAIL'
        return {
            'id': 'runtime_components',
            'status': 'NOT_READY',
            'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
            'message': 'A prova de conformance de um ou mais adaptadores falhou.',
            'next_action': 'Inspecione details.adapters, corrija o runtime e reexecute python3 -m udh doctor --conformance.',
            'details': details,
        }

    # Consulta passiva (run_conformance=False) alinhada à B1.3
    if conformant:
        details['conformance_status'] = 'PARTIAL'
        return {
            'id': 'runtime_components',
            'status': 'LIMITED',
            'reason_code': 'RUNTIME_PARTIAL_CONFORMANT',
            'message': f"Conformidade parcial: {', '.join(conformant)} conforme(s); demais adaptadores pendentes ou indisponíveis.",
            'next_action': 'Execute python3 -m udh doctor --conformance para validar todos os adaptadores.',
            'details': details,
        }
    if any_fail:
        details['conformance_status'] = 'FAIL'
        return {
            'id': 'runtime_components',
            'status': 'NOT_READY',
            'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
            'message': 'A prova de conformance do adaptador falhou.',
            'next_action': 'Inspecione o relatório de conformance, corrija o runtime e reexecute python3 -m udh doctor --conformance.',
            'details': details,
        }
    if all_missing_img:
        details['conformance_status'] = 'MISSING'
        return {
            'id': 'runtime_components',
            'status': 'LIMITED',
            'reason_code': 'AGENT_IMAGE_NOT_BUILT',
            'message': 'Imagem OCI dedicada por digest não está disponível no storage local do Podman.',
            'next_action': 'Execute python3 runtime/build_image.py --target all e depois rode python3 -m udh doctor --conformance.',
            'details': details,
        }
    details['conformance_status'] = 'STALE' if any_stale else 'MISSING'
    return {
        'id': 'runtime_components',
        'status': 'LIMITED',
        'reason_code': 'CONFORMANCE_STALE_OR_MISSING',
        'message': 'Imagem OCI presente, mas a conformance do adaptador está ausente ou desatualizada (STALE) para a política/digest atuais.',
        'next_action': 'Execute python3 -m udh doctor --conformance para validar os adaptadores configurados.',
        'details': details,
    }


def _evaluate_runtime_from_probe(
    rt_probe: Mapping,
    *,
    run_conformance: bool,
    requested_adapters: tuple[str, ...] | None = None,
) -> dict:
    pol_sha = rt_probe.get('policy_sha256') or policy_v2_sha256()

    if isinstance(rt_probe.get('adapters'), Mapping):
        raw_adapters = rt_probe['adapters']
        targets = tuple(
            name for name in (requested_adapters or CANONICAL_ADAPTER_ORDER)
            if name in raw_adapters
        )
        if not targets:
            targets = tuple(name for name in CANONICAL_ADAPTER_ORDER if name in raw_adapters)
        entries: dict[str, dict] = {}
        for name in targets:
            item = raw_adapters[name]
            img_present = bool(item.get('image_present'))
            cache = item.get('conformance')
            if not img_present:
                c_status = 'MISSING'
            elif isinstance(cache, Mapping) and cache.get('status') in ('PASS', 'FAIL', 'STALE'):
                c_status = str(cache['status'])
            else:
                c_status = 'MISSING'
            entries[name] = {
                'image_present': img_present,
                'image_digest': item.get('image_digest'),
                'conformance_status': c_status,
                'conformance_tool_mode': (cache.get('details', {}).get('conformance_tool_mode')
                                          if isinstance(cache, Mapping) and isinstance(cache.get('details'), Mapping)
                                          else item.get('conformance_tool_mode')),
            }
        return _aggregate_runtime_entries(
            entries,
            requested_adapters=targets,
            pol_sha=pol_sha,
            run_conformance=run_conformance,
        )

    # Formato legado single-adapter em env_probes
    image_present = bool(rt_probe.get('image_present'))
    image_digest = rt_probe.get('image_digest')
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


def _evaluate_single_adapter_live(
    *,
    adapter_name: str,
    manager: RuntimeManager,
    host_ready: bool,
    resolved_store: Path,
    pol_sha: str,
    run_conformance: bool,
) -> dict:
    try:
        if adapter_name == 'pi':
            manifest = manager.load_manifest()
        else:
            manifest = manager.load_opencode_manifest()
        manager.verify_image_present(manifest.image_ref)
    except (RuntimeIsolationError, CoreError, OSError) as error:
        return {
            'image_present': False,
            'image_digest': None,
            'conformance_status': 'MISSING',
            'reason_code': 'AGENT_IMAGE_NOT_BUILT',
            'error': str(error),
        }

    if run_conformance:
        if not host_ready:
            return {
                'image_present': True,
                'image_digest': manifest.image_digest,
                'conformance_status': 'FAIL',
                'reason_code': 'CONFORMANCE_PREREQUISITES_NOT_READY',
            }
        try:
            store = TrustStore(resolved_store)
            adapter = PiAdapter(runtime_manager=manager) if adapter_name == 'pi' else OpenCodeAdapter(runtime_manager=manager)
            res = adapter.run_conformance(trust_store=store, policy_sha256=pol_sha)
            status_val = 'PASS' if res.ok else 'FAIL'
            tool_mode = res.details.get('conformance_tool_mode', 'zero_tools')
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
                    'conformance_tool_mode': tool_mode,
                    'observed_tool_names': res.details.get('observed_tool_names', []),
                },
            )
            return {
                'image_present': True,
                'image_digest': manifest.image_digest,
                'adapter_version': res.adapter_version,
                'conformance_status': status_val,
                'reason_code': res.reason_code,
                'conformance_tool_mode': tool_mode,
            }
        except (CoreError, OSError) as error:
            return {
                'image_present': True,
                'image_digest': manifest.image_digest,
                'conformance_status': 'FAIL',
                'reason_code': 'ADAPTER_CONFORMANCE_FAILED',
                'error': str(error),
            }

    cached = _read_conformance_readonly(
        resolved_store,
        policy_sha256=pol_sha,
        image_digest=manifest.image_digest,
        adapter_name=adapter_name,
    )
    if isinstance(cached, Mapping) and cached.get('status') in ('PASS', 'FAIL', 'STALE'):
        c_details = cached.get('details') if isinstance(cached.get('details'), Mapping) else {}
        return {
            'image_present': True,
            'image_digest': manifest.image_digest,
            'adapter_version': cached.get('adapter_version'),
            'conformance_status': str(cached['status']),
            'conformance_tool_mode': c_details.get('conformance_tool_mode'),
        }
    return {
        'image_present': True,
        'image_digest': manifest.image_digest,
        'conformance_status': 'MISSING',
    }


def _evaluate_runtime_live(
    *,
    host_ready: bool,
    store_dir: str | Path | None,
    run_conformance: bool,
    manifest_path: Path | None,
    opencode_manifest_path: Path | None,
    requested_adapters: tuple[str, ...],
) -> dict:
    pol_sha = policy_v2_sha256()
    resolved_store = Path(store_dir) if store_dir is not None else default_store_dir()
    manager = RuntimeManager(
        manifest_path=manifest_path or DEFAULT_MANIFEST_PATH,
        opencode_manifest_path=opencode_manifest_path or DEFAULT_OPENCODE_MANIFEST_PATH,
    )

    entries: dict[str, dict] = {}
    for adapter_name in requested_adapters:
        entries[adapter_name] = _evaluate_single_adapter_live(
            adapter_name=adapter_name,
            manager=manager,
            host_ready=host_ready,
            resolved_store=resolved_store,
            pol_sha=pol_sha,
            run_conformance=run_conformance,
        )

    return _aggregate_runtime_entries(
        entries,
        requested_adapters=requested_adapters,
        pol_sha=pol_sha,
        run_conformance=run_conformance,
        store_dir=str(resolved_store),
    )


def run_doctor(
    *,
    db_path: str | Path | None = None,
    env_probes: Mapping | None = None,
    store_dir: str | Path | None = None,
    run_conformance: bool = False,
    manifest_path: Path | None = None,
    opencode_manifest_path: Path | None = None,
    adapters: tuple[str, ...] | list[str] | None = None,
) -> dict:
    """Executa preflight Linux/Podman rootless, runtime/conformance multi-agente e verificação profunda opcional do banco."""
    requested_adapters = _normalize_requested_adapters(
        adapters,
        manifest_path=manifest_path,
        opencode_manifest_path=opencode_manifest_path,
    )
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
            checks.append(
                _evaluate_runtime_from_probe(
                    probes['runtime_components'],
                    run_conformance=run_conformance,
                    requested_adapters=requested_adapters,
                )
            )
        else:
            checks.append({
                'id': 'runtime_components',
                'status': 'NOT_READY' if run_conformance else 'LIMITED',
                'reason_code': 'GATEWAY_AND_ADAPTER_NOT_CONFIGURED',
                'message': 'Runtime Manager, Gateway de inferência e adaptadores não foram verificados neste probe.',
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
                opencode_manifest_path=opencode_manifest_path,
                requested_adapters=requested_adapters,
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
