"""Runtime Manager rootless (Podman) com contratos fail-closed e perfis developer/artifact."""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import selectors
import signal
import subprocess
import threading
import time
import uuid

from .domain import CoreError, require_sha256, require_text, strict_json_loads

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent.parent / 'runtime' / 'image-manifest.json'
VALID_PROFILES = ('developer', 'artifact')
DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
IMAGE_REF_RE = re.compile(r'^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$')
FORBIDDEN_PATH_CHARS = (':', ',', '\n', '\r', '\x00')
FORBIDDEN_HOST_PATHS = (
    Path('/'),
    Path('/proc'),
    Path('/sys'),
    Path('/etc'),
    Path('/run/podman'),
    Path('/var/run/docker.sock'),
)
ARTIFACT_FORBIDDEN_DESTS = ('/ipc', '/udh/agent-config')


class RuntimeIsolationError(CoreError):
    """Violação de contrato de isolamento, digest ou ciclo de vida do Runtime Manager."""


@dataclass(frozen=True)
class ImageManifest:
    schema_version: int
    stack_profile: str
    image_repository: str
    image_digest: str
    image_ref: str
    pi_version: str
    pi_entrypoint: str
    node_bin: str
    bridge_path: str
    at_runner_path: str
    bridge_sha256: str
    at_runner_sha256: str

    def __post_init__(self):
        if self.schema_version != 1:
            raise RuntimeIsolationError('Versão de manifesto de imagem OCI não suportada.')
        require_text(self.stack_profile, 'Perfil de stack')
        require_text(self.image_repository, 'Repositório da imagem OCI')
        if not isinstance(self.image_digest, str) or not DIGEST_RE.fullmatch(self.image_digest):
            raise RuntimeIsolationError('Digest da imagem OCI inválido; esperado sha256:<64-hex>.')
        expected_ref = f'{self.image_repository}@{self.image_digest}'
        if self.image_ref != expected_ref or not IMAGE_REF_RE.fullmatch(self.image_ref):
            raise RuntimeIsolationError('Referência da imagem OCI não corresponde ao digest fixado.')
        require_text(self.pi_version, 'Versão do Pi')
        require_text(self.pi_entrypoint, 'Entrypoint do Pi')
        require_text(self.node_bin, 'Binário Node na imagem')
        require_text(self.bridge_path, 'Caminho da ponte na imagem')
        require_text(self.at_runner_path, 'Caminho do runner F-04 na imagem')
        require_sha256(self.bridge_sha256, 'Hash SHA-256 da ponte')
        require_sha256(self.at_runner_sha256, 'Hash SHA-256 do runner F-04')


@dataclass(frozen=True)
class BindMount:
    source: Path
    destination: str
    readonly: bool = True


@dataclass(frozen=True)
class ResourceLimits:
    memory_mb: int
    pids_limit: int
    cpus: float
    timeout_seconds: float
    output_limit_bytes: int = 262144

    def __post_init__(self):
        if type(self.memory_mb) is not int or self.memory_mb < 16 or self.memory_mb > 4096:
            raise RuntimeIsolationError('Limite de memória inválido (esperado 16..4096 MiB).')
        if type(self.pids_limit) is not int or self.pids_limit < 8 or self.pids_limit > 1024:
            raise RuntimeIsolationError('Limite de processos inválido (esperado 8..1024).')
        if not isinstance(self.cpus, (int, float)) or isinstance(self.cpus, bool) or self.cpus <= 0 or self.cpus > 8:
            raise RuntimeIsolationError('Limite de CPU inválido (esperado 0 < cpus <= 8).')
        if (not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool)
                or self.timeout_seconds <= 0 or self.timeout_seconds > 600):
            raise RuntimeIsolationError('Timeout inválido (esperado 0 < timeout <= 600s).')
        if type(self.output_limit_bytes) is not int or self.output_limit_bytes < 1024 or self.output_limit_bytes > 4 * 1024 * 1024:
            raise RuntimeIsolationError('Limite de saída inválido.')


DEFAULT_DEVELOPER_LIMITS = ResourceLimits(memory_mb=512, pids_limit=96, cpus=1.0, timeout_seconds=60.0)
DEFAULT_ARTIFACT_LIMITS = ResourceLimits(memory_mb=256, pids_limit=64, cpus=1.0, timeout_seconds=30.0)


@dataclass(frozen=True)
class ContainerResult:
    container_name: str
    profile: str
    image_ref: str
    returncode: int | None
    stdout: str
    stderr: str
    outcome: str | None
    elapsed_seconds: float
    inspection: dict | None
    cleanup_verified: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_image_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH, *, verify_local_sources: bool = True) -> ImageManifest:
    path = Path(manifest_path)
    if not path.is_file():
        raise RuntimeIsolationError(f'IMAGE_DIGEST_UNAVAILABLE: manifesto de imagem ausente em {path}.')
    raw = strict_json_loads(path.read_text(encoding='utf-8'), 'Manifesto de imagem OCI')
    if not isinstance(raw, dict):
        raise RuntimeIsolationError('Manifesto de imagem OCI deve ser um objeto JSON.')
    manifest = ImageManifest(
        schema_version=raw.get('schema_version'),
        stack_profile=raw.get('stack_profile', ''),
        image_repository=raw.get('image_repository', ''),
        image_digest=raw.get('image_digest', ''),
        image_ref=raw.get('image_ref', ''),
        pi_version=raw.get('pi_version', ''),
        pi_entrypoint=raw.get('pi_entrypoint', ''),
        node_bin=raw.get('node_bin', ''),
        bridge_path=raw.get('bridge_path', ''),
        at_runner_path=raw.get('at_runner_path', ''),
        bridge_sha256=raw.get('bridge_sha256', ''),
        at_runner_sha256=raw.get('at_runner_sha256', ''),
    )
    if verify_local_sources:
        runtime_dir = path.parent
        bridge_local = runtime_dir / 'bridge.mjs'
        runner_local = runtime_dir / 'at_runner.py'
        if bridge_local.is_file() and _sha256_file(bridge_local) != manifest.bridge_sha256:
            raise RuntimeIsolationError('IMAGE_DIGEST_STALE: runtime/bridge.mjs diverge do manifesto da imagem.')
        if runner_local.is_file() and _sha256_file(runner_local) != manifest.at_runner_sha256:
            raise RuntimeIsolationError('IMAGE_DIGEST_STALE: runtime/at_runner.py diverge do manifesto da imagem.')
    return manifest


def _check_clean_path_string(value: str, label: str) -> None:
    require_text(value, label)
    if any(ch in value for ch in FORBIDDEN_PATH_CHARS):
        raise RuntimeIsolationError(f'{label} contém caractere proibido para montagem segura.')


def _validate_destination(dest: str) -> str:
    _check_clean_path_string(dest, 'Destino do mount')
    if not dest.startswith('/'):
        raise RuntimeIsolationError('Destino do mount deve ser caminho absoluto.')
    parts = Path(dest).parts
    if '..' in parts:
        raise RuntimeIsolationError('Destino do mount não pode conter traversal (..).')
    normalized = posixpath.normpath(dest)
    if normalized.startswith('//'):
        normalized = '/' + normalized.lstrip('/')
    return normalized


def _validate_mounts(
    mounts: Sequence[BindMount],
    *,
    profile: str,
    forbidden_rw_roots: Sequence[Path] = (),
) -> list[tuple[Path, str, bool]]:
    home_raw = os.environ.get('HOME')
    home_path = Path(home_raw).resolve() if home_raw and Path(home_raw).exists() else None
    resolved_forbidden_rw = [Path(p).resolve() for p in forbidden_rw_roots]
    validated: list[tuple[Path, str, bool]] = []
    seen_dests: set[str] = set()

    for mount in mounts:
        dest = _validate_destination(mount.destination)
        if dest in seen_dests:
            raise RuntimeIsolationError(f'Destino de mount duplicado: {dest}.')
        seen_dests.add(dest)

        if profile == 'artifact':
            for forbidden_dest in ARTIFACT_FORBIDDEN_DESTS:
                if dest == forbidden_dest or dest.startswith(forbidden_dest + '/'):
                    raise RuntimeIsolationError(f'Perfil artifact proíbe montagem em {dest}.')

        src_raw = str(mount.source)
        _check_clean_path_string(src_raw, 'Origem do mount')
        try:
            src = Path(mount.source).resolve(strict=True)
        except OSError as error:
            raise RuntimeIsolationError(f'Origem de mount inacessível: {mount.source}.') from error
        _check_clean_path_string(str(src), 'Origem resolvida do mount')

        for forbidden in FORBIDDEN_HOST_PATHS:
            if src == forbidden or (forbidden != Path('/') and (src.is_relative_to(forbidden) or forbidden.is_relative_to(src))):
                raise RuntimeIsolationError(f'Origem de mount proibida: {src}.')
        if home_path is not None and home_path.is_relative_to(src):
            raise RuntimeIsolationError('Montagem que engloba $HOME do host é proibida.')
        if not mount.readonly:
            for protected_root in resolved_forbidden_rw:
                if src == protected_root or src.is_relative_to(protected_root) or protected_root.is_relative_to(src):
                    raise RuntimeIsolationError('Montagem gravável sobrepondo domínio confiável é proibida.')
        validated.append((src, dest, bool(mount.readonly)))

    # Verifica sobreposição indevida entre mounts rw distintos (permitindo apenas
    # arquivos individuais :ro montados sob /udh/agent-config :rw para proteção E4).
    for i, (src_a, dest_a, ro_a) in enumerate(validated):
        for src_b, dest_b, ro_b in validated[i + 1:]:
            if not ro_a and not ro_b:
                if src_a == src_b or src_a.is_relative_to(src_b) or src_b.is_relative_to(src_a):
                    raise RuntimeIsolationError('Sobreposição entre montagens graváveis não é permitida.')
            if (dest_a.startswith('/udh/agent-config/') and ro_a and dest_b == '/udh/agent-config') or (
                dest_b.startswith('/udh/agent-config/') and ro_b and dest_a == '/udh/agent-config'
            ):
                continue
            if dest_a != dest_b and (
                dest_a.startswith(dest_b.rstrip('/') + '/') or dest_b.startswith(dest_a.rstrip('/') + '/')
            ):
                raise RuntimeIsolationError(f'Sobreposição de destinos de mount não autorizada: {dest_a} e {dest_b}.')

    return validated


def _stop_process_group(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _collect_streams(
    process: subprocess.Popen,
    *,
    deadline: float,
    output_limit_bytes: int,
) -> tuple[int | None, str, str, str | None]:
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    outcome: str | None = None
    with selectors.DefaultSelector() as selector:
        for name in ('stdout', 'stderr'):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = 'TIMEOUT'
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buf = buffers[key.data]
                available = output_limit_bytes - len(buf)
                if available > 0:
                    buf.extend(chunk[:available])
                if len(chunk) > available:
                    outcome = 'OUTPUT_LIMIT'
                    break
            if outcome:
                break
        if outcome:
            _stop_process_group(process)
        else:
            try:
                process.wait(timeout=max(0.05, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = 'TIMEOUT'
                _stop_process_group(process)
    for name in ('stdout', 'stderr'):
        try:
            getattr(process, name).close()
        except OSError:
            pass
    return (
        process.returncode if outcome is None else None,
        buffers['stdout'].decode('utf-8', errors='replace'),
        buffers['stderr'].decode('utf-8', errors='replace'),
        outcome,
    )


class RuntimeManager:
    """Gerencia contêineres Podman rootless sob contratos fail-closed."""

    def __init__(self, *, manifest_path: Path = DEFAULT_MANIFEST_PATH, podman_bin: str = 'podman'):
        self.manifest_path = Path(manifest_path)
        self.podman_bin = podman_bin

    def load_manifest(self, *, verify_local_sources: bool = True) -> ImageManifest:
        return load_image_manifest(self.manifest_path, verify_local_sources=verify_local_sources)

    def verify_image_present(self, image_ref: str) -> dict:
        if not isinstance(image_ref, str) or not IMAGE_REF_RE.fullmatch(image_ref):
            raise RuntimeIsolationError('IMAGE_DIGEST_INVALID: imagem deve usar referência imutável <repo>@sha256:<64-hex>.')
        expected_digest = image_ref.split('@', 1)[1]
        try:
            completed = subprocess.run(
                [self.podman_bin, 'image', 'inspect', image_ref],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeIsolationError(f'IMAGE_DIGEST_UNAVAILABLE: falha ao inspecionar imagem ({error}).') from error
        if completed.returncode != 0:
            raise RuntimeIsolationError(
                f'IMAGE_DIGEST_UNAVAILABLE: imagem {image_ref} não encontrada no storage local do Podman.'
            )
        try:
            parsed = json.loads(completed.stdout)
        except ValueError as error:
            raise RuntimeIsolationError('IMAGE_DIGEST_UNAVAILABLE: resposta JSON inválida de podman image inspect.') from error
        if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
            raise RuntimeIsolationError('IMAGE_DIGEST_UNAVAILABLE: inspeção de imagem vazia.')
        entry = parsed[0]
        actual_digest = entry.get('Digest')
        repo_digests = entry.get('RepoDigests') or ()
        matched = (actual_digest == expected_digest) or any(
            isinstance(rd, str) and rd.endswith('@' + expected_digest) for rd in repo_digests
        )
        if not matched:
            raise RuntimeIsolationError(
                f'IMAGE_DIGEST_MISMATCH: digest local ({actual_digest}) diverge do esperado ({expected_digest}).'
            )
        return entry

    def _cleanup_container(self, name: str) -> bool:
        subprocess.run(
            [self.podman_bin, 'rm', '--force', '--time=0', name],
            capture_output=True, text=True, timeout=10, check=False,
        )
        exists = subprocess.run(
            [self.podman_bin, 'container', 'exists', name],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return exists.returncode == 1

    def run_container(
        self,
        *,
        profile: str,
        command: Sequence[str],
        image_ref: str | None = None,
        mounts: Sequence[BindMount] = (),
        env: Mapping[str, str] | None = None,
        workdir: str = '/workspace',
        limits: ResourceLimits | None = None,
        stdin_bytes: bytes | None = None,
        on_started: Callable[[str], None] | None = None,
        forbidden_rw_roots: Sequence[Path] = (),
    ) -> ContainerResult:
        if profile not in VALID_PROFILES:
            raise RuntimeIsolationError(f'Perfil de runtime inválido: {profile}.')
        if not command or not all(isinstance(arg, str) and arg != '' for arg in command):
            raise RuntimeIsolationError('Comando do contêiner deve conter argumentos válidos.')
        _validate_destination(workdir)

        resolved_image_ref = image_ref or self.load_manifest().image_ref
        self.verify_image_present(resolved_image_ref)

        effective_limits = limits or (DEFAULT_DEVELOPER_LIMITS if profile == 'developer' else DEFAULT_ARTIFACT_LIMITS)
        validated_mounts = _validate_mounts(mounts, profile=profile, forbidden_rw_roots=forbidden_rw_roots)

        name = f'udh-{profile}-{uuid.uuid4().hex[:16]}'
        uid = os.getuid()
        gid = os.getgid()

        create_cmd = [
            self.podman_bin, 'create',
            '--name', name,
            '--pull=never',
            '--network=none',
            '--read-only',
            '--read-only-tmpfs=false',
            '--cap-drop=ALL',
            '--security-opt=no-new-privileges',
            '--userns=keep-id',
            f'--user={uid}:{gid}',
            f'--pids-limit={effective_limits.pids_limit}',
            f'--memory={effective_limits.memory_mb}m',
            f'--memory-swap={effective_limits.memory_mb}m',
            f'--cpus={effective_limits.cpus}',
            f'--workdir={workdir}',
        ]
        if stdin_bytes is not None:
            create_cmd.append('--interactive')
        for src, dst, readonly in validated_mounts:
            spec = f'type=bind,source={src},destination={dst}'
            if readonly:
                spec += ',readonly=true'
            create_cmd.extend(['--mount', spec])
        if env:
            for key, value in sorted(env.items()):
                require_text(key, 'Nome de variável de ambiente')
                if '=' in key or '\x00' in key or '\x00' in str(value):
                    raise RuntimeIsolationError('Variável de ambiente contém caractere proibido.')
                create_cmd.extend(['--env', f'{key}={value}'])
        create_cmd.append(resolved_image_ref)
        create_cmd.extend(command)

        created = subprocess.run(create_cmd, capture_output=True, text=True, timeout=20, check=False)
        if created.returncode != 0:
            raise RuntimeIsolationError(f'Falha ao criar contêiner isolado: {created.stderr.strip()[:400]}')

        started = time.monotonic()
        deadline = started + effective_limits.timeout_seconds
        inspection: dict | None = None
        cleanup_ok = False
        try:
            start_cmd = [self.podman_bin, 'start', '--attach']
            if stdin_bytes is not None:
                start_cmd.append('--interactive')
            start_cmd.append(name)
            process = subprocess.Popen(
                start_cmd,
                stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            if stdin_bytes is not None and process.stdin is not None:
                def _feed_stdin():
                    try:
                        process.stdin.write(stdin_bytes)
                        process.stdin.flush()
                    except OSError:
                        pass
                    finally:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass
                feeder = threading.Thread(target=_feed_stdin, daemon=True)
                feeder.start()
            if on_started is not None:
                def _watch_started():
                    watch_deadline = min(deadline, time.monotonic() + 5.0)
                    while time.monotonic() < watch_deadline and process.poll() is None:
                        insp = subprocess.run(
                            [self.podman_bin, 'inspect', '--format', '{{.State.Running}}', name],
                            capture_output=True, text=True, timeout=3, check=False,
                        )
                        if insp.returncode == 0 and insp.stdout.strip() == 'true':
                            on_started(name)
                            return
                        time.sleep(0.05)
                watcher = threading.Thread(target=_watch_started, daemon=True)
                watcher.start()
            else:
                watcher = None

            returncode, stdout, stderr, outcome = _collect_streams(
                process, deadline=deadline, output_limit_bytes=effective_limits.output_limit_bytes,
            )
            if watcher is not None:
                watcher.join(timeout=2.0)

            if outcome in ('TIMEOUT', 'OUTPUT_LIMIT'):
                subprocess.run(
                    [self.podman_bin, 'kill', name],
                    capture_output=True, text=True, timeout=5, check=False,
                )

            insp_final = subprocess.run(
                [self.podman_bin, 'inspect', name],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if insp_final.returncode == 0:
                try:
                    data = json.loads(insp_final.stdout)
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        inspection = data[0]
                except ValueError:
                    inspection = None
        finally:
            cleanup_ok = self._cleanup_container(name)

        if not cleanup_ok:
            raise RuntimeIsolationError(f'Falha ao confirmar remoção do contêiner {name}.')

        return ContainerResult(
            container_name=name,
            profile=profile,
            image_ref=resolved_image_ref,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            outcome=outcome,
            elapsed_seconds=round(time.monotonic() - started, 4),
            inspection=inspection,
            cleanup_verified=cleanup_ok,
        )

    def run_developer(
        self,
        *,
        command: Sequence[str],
        mounts: Sequence[BindMount],
        env: Mapping[str, str] | None = None,
        workdir: str = '/workspace',
        limits: ResourceLimits | None = None,
        image_ref: str | None = None,
        on_started: Callable[[str], None] | None = None,
        forbidden_rw_roots: Sequence[Path] = (),
    ) -> ContainerResult:
        return self.run_container(
            profile='developer',
            command=command,
            image_ref=image_ref,
            mounts=mounts,
            env=env,
            workdir=workdir,
            limits=limits or DEFAULT_DEVELOPER_LIMITS,
            on_started=on_started,
            forbidden_rw_roots=forbidden_rw_roots,
        )

    def run_artifact(
        self,
        *,
        command: Sequence[str],
        artifact_dir: Path,
        at_dir: Path,
        work_dir: Path,
        env: Mapping[str, str] | None = None,
        limits: ResourceLimits | None = None,
        image_ref: str | None = None,
        stdin_bytes: bytes | None = None,
        forbidden_rw_roots: Sequence[Path] = (),
    ) -> ContainerResult:
        mounts = (
            BindMount(artifact_dir, '/artifact', readonly=True),
            BindMount(at_dir, '/sealed-at', readonly=True),
            BindMount(work_dir, '/work', readonly=False),
        )
        return self.run_container(
            profile='artifact',
            command=command,
            image_ref=image_ref,
            mounts=mounts,
            env=env,
            workdir='/work',
            limits=limits or DEFAULT_ARTIFACT_LIMITS,
            stdin_bytes=stdin_bytes,
            forbidden_rw_roots=forbidden_rw_roots,
        )
