"""Constrói localmente (100% offline) a imagem OCI dedicada do UDH (Change 2) e grava o manifesto por digest.

Empacota sobre a base Alpine local:
- Node v22 (binário do runtime Pi)
- Pi 1.2.0 (/opt/pi/node_modules)
- Python 3 stdlib completa (/usr/bin/python3 + /usr/lib/python3.X)
- Fechamento transitivo ldd (glibc, ld-linux-x86-64.so.2, libs de lib-dynload/*.so)
- /opt/udh/bridge.mjs e /opt/udh/at_runner.py (0444)
- Pontos de montagem vazios fora do workspace (/udh/agent-config, /run/udh-scratch, /ipc, /artifact, /sealed-at, /work)

Nunca baixa pacotes da rede (--pull=never --network=none) e nunca commita binários no Git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / 'runtime'
MANIFEST_PATH = RUNTIME_DIR / 'image-manifest.json'
OPENCODE_MANIFEST_PATH = RUNTIME_DIR / 'opencode-image-manifest.json'
DEFAULT_NODE = Path('/home/debian/.local/share/pi-node/current/bin/node')
DEFAULT_PI_MODULES = Path('/home/debian/.pi/agent/install/releases/1.2.0/node_modules')
DEFAULT_OPENCODE_BIN = Path('/home/debian/.opencode/bin/opencode')
DEFAULT_REPO = 'localhost/udh-runtime'
DEFAULT_TAG = 'change2'
DEFAULT_OPENCODE_REPO = 'localhost/udh-runtime-opencode'
DEFAULT_OPENCODE_TAG = 'change3'
EXCLUDED_STDLIB_DIRS = frozenset({
    '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo',
    'ensurepip', 'venv', 'pydoc_data', 'config-3.13-x86_64-linux-gnu',
})
EXCLUDED_STDLIB_FILES = frozenset({
    'sitecustomize.py', 'usercustomize.py', 'EXTERNALLY-MANAGED',
})


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _ldd_paths(binaries: list[Path]) -> set[Path]:
    found: set[Path] = set()
    queue = list(binaries)
    seen: set[Path] = set()
    while queue:
        current = queue.pop()
        resolved = current.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        completed = subprocess.run(
            ['ldd', str(resolved)],
            capture_output=True, text=True, check=False, timeout=20,
        )
        if completed.returncode != 0:
            continue
        for match in re.findall(r'(/[^\s()]+)', completed.stdout):
            lib_path = Path(match)
            if lib_path.exists():
                found.add(lib_path)
                lib_real = lib_path.resolve()
                if lib_real not in seen:
                    queue.append(lib_real)
    return found


def _stage_file(src: Path, dst: Path, mode: int | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    shutil.copy2(src.resolve(), dst)
    if mode is not None:
        os.chmod(dst, mode)


def _stage_library(lib_path: Path, stage_root: Path) -> None:
    # Preserva o caminho esperado pelo linker (inclusive symlink soname -> arquivo real)
    # sem tocar no loader musl da Alpine.
    if 'musl' in lib_path.name:
        return
    real = lib_path.resolve()
    rel_real = real.relative_to('/')
    dst_real = stage_root / rel_real
    if not dst_real.exists():
        _stage_file(real, dst_real, mode=0o755)
    rel_link = lib_path.relative_to('/')
    if rel_link != rel_real:
        dst_link = stage_root / rel_link
        dst_link.parent.mkdir(parents=True, exist_ok=True)
        if dst_link.exists() or dst_link.is_symlink():
            dst_link.unlink()
        dst_link.symlink_to(str(real))


def _copy_tree_safe(
    src_root: Path,
    dst_root: Path,
    *,
    exclude_dirs: frozenset[str] = frozenset(),
    exclude_files: frozenset[str] = frozenset(),
) -> int:
    src_resolved = src_root.resolve(strict=True)
    count = 0
    for current, dirnames, filenames in os.walk(src_resolved, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        rel_dir = Path(current).relative_to(src_resolved)
        target_dir = dst_root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            if filename.endswith('.pyc') or filename.endswith('.pyo') or filename in exclude_files:
                continue
            src_file = Path(current) / filename
            if src_file.is_symlink():
                target = src_file.resolve()
                if not target.is_relative_to(src_resolved):
                    raise RuntimeError(f'Symlink externo recusado durante staging: {src_file} -> {target}')
                shutil.copy2(target, target_dir / filename)
            else:
                shutil.copy2(src_file, target_dir / filename)
            count += 1
    return count


def _prepare_stage(stage: Path, *, node_path: Path, pi_modules: Path) -> dict:
    node_real = node_path.resolve(strict=True)
    pi_real = pi_modules.resolve(strict=True)
    python_real = Path(sys.executable).resolve(strict=True)
    py_ver = f'python{sys.version_info.major}.{sys.version_info.minor}'
    stdlib_dir = Path(f'/usr/lib/{py_ver}').resolve(strict=True)

    # Diretórios de mountpoint fora do workspace e estrutura base
    for rel_dir in (
        'opt/udh/bin', 'opt/pi', 'udh/agent-config', 'run/udh-scratch',
        'ipc', 'workspace', 'artifact', 'sealed-at', 'work', 'usr/bin',
    ):
        (stage / rel_dir).mkdir(parents=True, exist_ok=True)

    # Node v22 + Pi 1.2.0
    _stage_file(node_real, stage / 'opt/udh/bin/node', mode=0o755)
    pi_files = _copy_tree_safe(pi_real, stage / 'opt/pi/node_modules')

    # Python 3 + stdlib completa (sem dist-packages)
    _stage_file(python_real, stage / f'usr/bin/{python_real.name}', mode=0o755)
    py_link = stage / 'usr/bin/python3'
    if py_link.exists() or py_link.is_symlink():
        py_link.unlink()
    py_link.symlink_to(python_real.name)
    py_files = _copy_tree_safe(
        stdlib_dir, stage / f'usr/lib/{py_ver}',
        exclude_dirs=EXCLUDED_STDLIB_DIRS,
        exclude_files=EXCLUDED_STDLIB_FILES,
    )

    # Fechamento transitivo de bibliotecas dinâmicas (node, python3 e todos os .so em lib-dynload)
    binaries_to_scan = [node_real, python_real]
    dynload = stdlib_dir / 'lib-dynload'
    if dynload.is_dir():
        binaries_to_scan.extend(sorted(dynload.glob('*.so')))
    libs = _ldd_paths(binaries_to_scan)
    for lib in sorted(libs):
        _stage_library(lib, stage)

    # Artefatos UDH imutáveis
    bridge_src = RUNTIME_DIR / 'bridge.mjs'
    runner_src = RUNTIME_DIR / 'at_runner.py'
    _stage_file(bridge_src, stage / 'opt/udh/bridge.mjs', mode=0o444)
    _stage_file(runner_src, stage / 'opt/udh/at_runner.py', mode=0o444)

    return {
        'pi_files': pi_files,
        'python_stdlib_files': py_files,
        'shared_libs_count': len(libs),
        'bridge_sha256': _sha256_file(bridge_src),
        'at_runner_sha256': _sha256_file(runner_src),
    }


def _smoke_check_image(image_ref: str) -> dict:
    base_cmd = [
        'podman', 'run', '--rm', '--pull=never', '--network=none',
        '--read-only', '--read-only-tmpfs=false', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--userns=keep-id',
        f'--user={os.getuid()}:{os.getgid()}',
        '--pids-limit=64', '--memory=256m', '--memory-swap=256m',
        image_ref,
    ]
    py_check = subprocess.run(
        base_cmd + [
            'python3', '-I', '-B', '-c',
            'import ast, ctypes, hashlib, hmac, http.client, json, secrets, sqlite3, ssl, unittest; '
            'print("PY_STDLIB_OK")',
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if py_check.returncode != 0 or 'PY_STDLIB_OK' not in py_check.stdout:
        raise RuntimeError(f'Smoke check Python falhou (rc={py_check.returncode}): {py_check.stderr}')

    pi_check = subprocess.run(
        base_cmd + [
            '/opt/udh/bin/node',
            '/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js',
            '--version',
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    pi_version = (pi_check.stdout or '').strip()
    if pi_check.returncode != 0 or '1.2.0' not in pi_version:
        raise RuntimeError(f'Smoke check Pi 1.2.0 falhou (rc={pi_check.returncode}): {pi_check.stdout} {pi_check.stderr}')

    syntax_check = subprocess.run(
        base_cmd + [
            '/opt/udh/bin/node', '--check', '/opt/udh/bridge.mjs',
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if syntax_check.returncode != 0:
        raise RuntimeError(f'Smoke check bridge.mjs falhou: {syntax_check.stderr}')

    runner_check = subprocess.run(
        base_cmd + [
            'python3', '-I', '-B', '-c',
            'import ast, pathlib; ast.parse(pathlib.Path("/opt/udh/at_runner.py").read_text()); print("RUNNER_OK")',
        ],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if runner_check.returncode != 0 or 'RUNNER_OK' not in runner_check.stdout:
        raise RuntimeError(f'Smoke check at_runner.py falhou: {runner_check.stderr}')

    return {'python_stdlib': 'PY_STDLIB_OK', 'pi_version': pi_version}


def _extract_digest(tag_ref: str, repo: str) -> str:
    inspect = subprocess.run(
        ['podman', 'image', 'inspect', tag_ref],
        capture_output=True, text=True, timeout=15, check=True,
    )
    data = json.loads(inspect.stdout)
    if not isinstance(data, list) or not data:
        raise RuntimeError('podman image inspect retornou payload vazio.')
    entry = data[0]
    digest = entry.get('Digest')
    if isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        return digest
    for candidate in entry.get('RepoDigests') or ():
        if isinstance(candidate, str) and '@sha256:' in candidate:
            _, dig = candidate.split('@', 1)
            if re.fullmatch(r'sha256:[0-9a-f]{64}', dig):
                return dig
    raise RuntimeError(f'Não foi possível extrair digest sha256 da imagem {tag_ref}.')


def build_image(*, node_path: Path = DEFAULT_NODE, pi_modules: Path = DEFAULT_PI_MODULES,
                repo: str = DEFAULT_REPO, tag: str = DEFAULT_TAG) -> dict:
    tag_ref = f'{repo}:{tag}'
    with tempfile.TemporaryDirectory(prefix='udh-oci-stage-') as tmpdir:
        stage = Path(tmpdir)
        stage_info = _prepare_stage(stage, node_path=node_path, pi_modules=pi_modules)
        cmd = [
            'podman', 'build', '--pull=never', '--network=none',
            '--build-context', f'stage={stage}',
            '-f', str(RUNTIME_DIR / 'Containerfile'),
            '-t', tag_ref,
            str(RUNTIME_DIR),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f'podman build falhou (rc={completed.returncode}):\n{completed.stdout}\n{completed.stderr}')

    digest = _extract_digest(tag_ref, repo)
    image_ref = f'{repo}@{digest}'
    smoke = _smoke_check_image(image_ref)
    manifest = {
        'schema_version': 1,
        'stack_profile': 'python3-stdlib-v1',
        'image_repository': repo,
        'image_tag': tag,
        'image_digest': digest,
        'image_ref': image_ref,
        'pi_version': '1.2.0',
        'pi_entrypoint': '/opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js',
        'node_bin': '/opt/udh/bin/node',
        'bridge_path': '/opt/udh/bridge.mjs',
        'at_runner_path': '/opt/udh/at_runner.py',
        'bridge_sha256': stage_info['bridge_sha256'],
        'at_runner_sha256': stage_info['at_runner_sha256'],
        'smoke_verified': smoke,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return manifest


def _prepare_opencode_stage(stage: Path, *, node_path: Path, opencode_bin: Path) -> dict:
    node_real = node_path.resolve(strict=True)
    opencode_real = opencode_bin.resolve(strict=True)
    python_real = Path(sys.executable).resolve(strict=True)
    py_ver = f'python{sys.version_info.major}.{sys.version_info.minor}'
    stdlib_dir = Path(f'/usr/lib/{py_ver}').resolve(strict=True)

    for rel_dir in (
        'opt/udh/bin', 'udh/agent-config', 'run/udh-scratch',
        'ipc', 'workspace', 'usr/bin',
    ):
        (stage / rel_dir).mkdir(parents=True, exist_ok=True)

    _stage_file(opencode_real, stage / 'opt/udh/bin/opencode', mode=0o755)
    _stage_file(node_real, stage / 'opt/udh/bin/node', mode=0o755)

    _stage_file(python_real, stage / f'usr/bin/{python_real.name}', mode=0o755)
    py_link = stage / 'usr/bin/python3'
    if py_link.exists() or py_link.is_symlink():
        py_link.unlink()
    py_link.symlink_to(python_real.name)
    py_files = _copy_tree_safe(
        stdlib_dir, stage / f'usr/lib/{py_ver}',
        exclude_dirs=EXCLUDED_STDLIB_DIRS,
        exclude_files=EXCLUDED_STDLIB_FILES,
    )

    binaries_to_scan = [opencode_real, node_real, python_real]
    dynload = stdlib_dir / 'lib-dynload'
    if dynload.is_dir():
        binaries_to_scan.extend(sorted(dynload.glob('*.so')))
    libs = _ldd_paths(binaries_to_scan)
    for lib in sorted(libs):
        _stage_library(lib, stage)

    bridge_src = RUNTIME_DIR / 'bridge.mjs'
    _stage_file(bridge_src, stage / 'opt/udh/bridge.mjs', mode=0o444)

    return {
        'python_stdlib_files': py_files,
        'shared_libs_count': len(libs),
        'bridge_sha256': _sha256_file(bridge_src),
        'opencode_sha256': _sha256_file(opencode_real),
    }


def _smoke_check_opencode_image(image_ref: str) -> dict:
    with tempfile.TemporaryDirectory(prefix='udh-opencode-smoke-') as scratch:
        scratch_root = Path(scratch)
        for sub in ('home', 'config', 'data', 'cache', 'state', 'tmp'):
            (scratch_root / sub).mkdir(parents=True, exist_ok=True)
        base_cmd = [
            'podman', 'run', '--rm', '--pull=never', '--network=none',
            '--read-only', '--read-only-tmpfs=false', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--userns=keep-id',
            f'--user={os.getuid()}:{os.getgid()}',
            '--pids-limit=128', '--memory=768m', '--memory-swap=768m',
            f'--mount=type=bind,source={scratch_root},destination=/run/udh-scratch',
            '-e', 'HOME=/run/udh-scratch/home',
            '-e', 'XDG_CONFIG_HOME=/run/udh-scratch/config',
            '-e', 'XDG_DATA_HOME=/run/udh-scratch/data',
            '-e', 'XDG_CACHE_HOME=/run/udh-scratch/cache',
            '-e', 'XDG_STATE_HOME=/run/udh-scratch/state',
            '-e', 'TMPDIR=/run/udh-scratch/tmp',
            image_ref,
        ]
        py_check = subprocess.run(
            base_cmd + [
                'python3', '-I', '-B', '-c',
                'import ast, ctypes, hashlib, hmac, http.client, json, secrets, sqlite3, ssl, unittest; '
                'print("PY_STDLIB_OK")',
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if py_check.returncode != 0 or 'PY_STDLIB_OK' not in py_check.stdout:
            raise RuntimeError(f'Smoke check Python falhou (rc={py_check.returncode}): {py_check.stderr}')

        oc_check = subprocess.run(
            base_cmd + ['/opt/udh/bin/opencode', '--version'],
            capture_output=True, text=True, timeout=30, check=False,
        )
        oc_version = (oc_check.stdout or '').strip().splitlines()[-1].strip() if (oc_check.stdout or '').strip() else ''
        if oc_check.returncode != 0 or not oc_version:
            raise RuntimeError(
                f'Smoke check OpenCode falhou (rc={oc_check.returncode}): {oc_check.stdout} {oc_check.stderr}'
            )

        syntax_check = subprocess.run(
            base_cmd + ['/opt/udh/bin/node', '--check', '/opt/udh/bridge.mjs'],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if syntax_check.returncode != 0:
            raise RuntimeError(f'Smoke check bridge.mjs falhou: {syntax_check.stderr}')

    return {
        'python_stdlib': 'PY_STDLIB_OK',
        'opencode_version': oc_version,
        'bridge_syntax': 'BRIDGE_OK',
    }


def build_opencode_image(
    *,
    node_path: Path = DEFAULT_NODE,
    opencode_bin: Path = DEFAULT_OPENCODE_BIN,
    repo: str = DEFAULT_OPENCODE_REPO,
    tag: str = DEFAULT_OPENCODE_TAG,
) -> dict:
    tag_ref = f'{repo}:{tag}'
    with tempfile.TemporaryDirectory(prefix='udh-oci-opencode-stage-') as tmpdir:
        stage = Path(tmpdir)
        stage_info = _prepare_opencode_stage(stage, node_path=node_path, opencode_bin=opencode_bin)
        cmd = [
            'podman', 'build', '--pull=never', '--network=none',
            '--build-context', f'stage={stage}',
            '-f', str(RUNTIME_DIR / 'Containerfile.opencode'),
            '-t', tag_ref,
            str(RUNTIME_DIR),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f'podman build (opencode) falhou (rc={completed.returncode}):\n{completed.stdout}\n{completed.stderr}'
            )

    digest = _extract_digest(tag_ref, repo)
    image_ref = f'{repo}@{digest}'
    smoke = _smoke_check_opencode_image(image_ref)
    manifest = {
        'schema_version': 1,
        'adapter_name': 'opencode',
        'stack_profile': 'python3-stdlib-v1',
        'image_repository': repo,
        'image_tag': tag,
        'image_digest': digest,
        'image_ref': image_ref,
        'opencode_version': smoke['opencode_version'],
        'opencode_bin': '/opt/udh/bin/opencode',
        'node_bin': '/opt/udh/bin/node',
        'bridge_path': '/opt/udh/bridge.mjs',
        'bridge_sha256': stage_info['bridge_sha256'],
        'smoke_verified': smoke,
    }
    OPENCODE_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Construir imagem OCI local do UDH offline por digest.')
    parser.add_argument('--target', choices=('pi', 'opencode', 'all'), default='pi')
    parser.add_argument('--node', type=Path, default=DEFAULT_NODE)
    parser.add_argument('--pi-modules', type=Path, default=DEFAULT_PI_MODULES)
    parser.add_argument('--opencode-bin', type=Path, default=DEFAULT_OPENCODE_BIN)
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--tag', default=DEFAULT_TAG)
    parser.add_argument('--opencode-repo', default=DEFAULT_OPENCODE_REPO)
    parser.add_argument('--opencode-tag', default=DEFAULT_OPENCODE_TAG)
    args = parser.parse_args(argv)
    if args.target == 'pi':
        result = build_image(node_path=args.node, pi_modules=args.pi_modules, repo=args.repo, tag=args.tag)
    elif args.target == 'opencode':
        result = build_opencode_image(
            node_path=args.node, opencode_bin=args.opencode_bin,
            repo=args.opencode_repo, tag=args.opencode_tag,
        )
    else:
        result = {
            'pi': build_image(node_path=args.node, pi_modules=args.pi_modules, repo=args.repo, tag=args.tag),
            'opencode': build_opencode_image(
                node_path=args.node, opencode_bin=args.opencode_bin,
                repo=args.opencode_repo, tag=args.opencode_tag,
            ),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
