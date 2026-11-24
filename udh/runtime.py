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
DEFAULT_OPENCODE_MANIFEST_PATH = Path(__file__).resolve().parent.parent / 'runtime' / 'opencode-image-manifest.json'
DEFAULT_ARTIFACT_MANIFEST_PATH = Path(__file__).resolve().parent.parent / 'runtime' / 'artifact-image-manifest.json'
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
class OpenCodeImageManifest:
    schema_version: int
    adapter_name: str
    stack_profile: str
    image_repository: str
    image_digest: str
    image_ref: str
    opencode_version: str
    opencode_bin: str
    node_bin: str
    bridge_path: str
    bridge_sha256: str

    def __post_init__(self):
        if self.schema_version != 1:
            raise RuntimeIsolationError('Versão de manifesto de imagem OCI OpenCode não suportada.')
        if self.adapter_name != 'opencode':
            raise RuntimeIsolationError('Manifesto de imagem OCI OpenCode deve declarar adapter_name="opencode".')
        require_text(self.stack_profile, 'Perfil de stack')
        require_text(self.image_repository, 'Repositório da imagem OCI OpenCode')
        if not isinstance(self.image_digest, str) or not DIGEST_RE.fullmatch(self.image_digest):
            raise RuntimeIsolationError('Digest da imagem OCI OpenCode inválido; esperado sha256:<64-hex>.')
        expected_ref = f'{self.image_repository}@{self.image_digest}'
        if self.image_ref != expected_ref or not IMAGE_REF_RE.fullmatch(self.image_ref):
            raise RuntimeIsolationError('Referência da imagem OCI OpenCode não corresponde ao digest fixado.')
        require_text(self.opencode_version, 'Versão do OpenCode')
        require_text(self.opencode_bin, 'Binário OpenCode na imagem')
        require_text(self.node_bin, 'Binário Node na imagem')
        require_text(self.bridge_path, 'Caminho da ponte na imagem')
        require_sha256(self.bridge_sha256, 'Hash SHA-256 da ponte')


@dataclass(frozen=True)
class ArtifactImageManifest:
    schema_version: int
    profile: str
    stack_profile: str
    image_repository: str
    image_tag: str
    image_digest: str
    image_ref: str
    at_runner_path: str
    at_runner_sha256: str
    preview_bridge_path: str
    preview_bridge_sha256: str

    def __post_init__(self):
        if self.schema_version != 1:
            raise RuntimeIsolationError('Versão de manifesto de imagem OCI artifact não suportada.')
        if self.profile != 'artifact':
            raise RuntimeIsolationError('Manifesto de imagem OCI artifact deve declarar profile="artifact".')
        require_text(self.stack_profile, 'Perfil de stack da imagem artifact')
        require_text(self.image_repository, 'Repositório da imagem OCI artifact')
        require_text(self.image_tag, 'Tag da imagem OCI artifact')
        if not isinstance(self.image_digest, str) or not DIGEST_RE.fullmatch(self.image_digest):
            raise RuntimeIsolationError('Digest da imagem OCI artifact inválido; esperado sha256:<64-hex>.')
        expected_ref = f'{self.image_repository}@{self.image_digest}'
        if self.image_ref != expected_ref or not IMAGE_REF_RE.fullmatch(self.image_ref):
            raise RuntimeIsolationError('Referência da imagem OCI artifact não corresponde ao digest fixado.')
        require_text(self.at_runner_path, 'Caminho do runner F-04 na imagem artifact')
        require_sha256(self.at_runner_sha256, 'Hash SHA-256 do runner F-04 na imagem artifact')
        require_text(self.preview_bridge_path, 'Caminho da ponte de preview na imagem artifact')
        require_sha256(self.preview_bridge_sha256, 'Hash SHA-256 da ponte de preview na imagem artifact')


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
DEFAULT_PREVIEW_LIMITS = ResourceLimits(memory_mb=256, pids_limit=64, cpus=1.0, timeout_seconds=300.0)


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


def load_opencode_manifest(
    manifest_path: Path = DEFAULT_OPENCODE_MANIFEST_PATH,
    *,
    verify_local_sources: bool = True,
) -> OpenCodeImageManifest:
    path = Path(manifest_path)
    if not path.is_file():
        raise RuntimeIsolationError(f'IMAGE_DIGEST_UNAVAILABLE: manifesto de imagem OpenCode ausente em {path}.')
    raw = strict_json_loads(path.read_text(encoding='utf-8'), 'Manifesto de imagem OCI OpenCode')
    if not isinstance(raw, dict):
        raise RuntimeIsolationError('Manifesto de imagem OCI OpenCode deve ser um objeto JSON.')
    manifest = OpenCodeImageManifest(
        schema_version=raw.get('schema_version'),
        adapter_name=raw.get('adapter_name', ''),
        stack_profile=raw.get('stack_profile', ''),
        image_repository=raw.get('image_repository', ''),
        image_digest=raw.get('image_digest', ''),
        image_ref=raw.get('image_ref', ''),
        opencode_version=raw.get('opencode_version', ''),
        opencode_bin=raw.get('opencode_bin', ''),
        node_bin=raw.get('node_bin', ''),
        bridge_path=raw.get('bridge_path', ''),
        bridge_sha256=raw.get('bridge_sha256', ''),
    )
    if verify_local_sources:
        bridge_local = path.parent / 'bridge.mjs'
        if bridge_local.is_file() and _sha256_file(bridge_local) != manifest.bridge_sha256:
            raise RuntimeIsolationError('IMAGE_DIGEST_STALE: runtime/bridge.mjs diverge do manifesto da imagem OpenCode.')
    return manifest


def load_artifact_manifest(
    manifest_path: Path = DEFAULT_ARTIFACT_MANIFEST_PATH,
    *,
    verify_local_sources: bool = True,
) -> ArtifactImageManifest:
    path = Path(manifest_path)
    if not path.is_file():
        raise RuntimeIsolationError(f'IMAGE_DIGEST_UNAVAILABLE: manifesto de imagem artifact ausente em {path}.')
    raw = strict_json_loads(path.read_text(encoding='utf-8'), 'Manifesto de imagem OCI artifact')
    if not isinstance(raw, dict):
        raise RuntimeIsolationError('Manifesto de imagem OCI artifact deve ser um objeto JSON.')
    manifest = ArtifactImageManifest(
        schema_version=raw.get('schema_version'),
        profile=raw.get('profile', ''),
        stack_profile=raw.get('stack_profile', ''),
        image_repository=raw.get('image_repository', ''),
        image_tag=raw.get('image_tag', ''),
        image_digest=raw.get('image_digest', ''),
        image_ref=raw.get('image_ref', ''),
        at_runner_path=raw.get('at_runner_path', ''),
        at_runner_sha256=raw.get('at_runner_sha256', ''),
        preview_bridge_path=raw.get('preview_bridge_path', ''),
        preview_bridge_sha256=raw.get('preview_bridge_sha256', ''),
    )
    if verify_local_sources:
        runtime_dir = path.parent
        runner_local = runtime_dir / 'at_runner.py'
        bridge_local = runtime_dir / 'preview_bridge.py'
        if runner_local.is_file() and _sha256_file(runner_local) != manifest.at_runner_sha256:
            raise RuntimeIsolationError('IMAGE_DIGEST_STALE: runtime/at_runner.py diverge do manifesto artifact.')
        if bridge_local.is_file() and _sha256_file(bridge_local) != manifest.preview_bridge_sha256:
            raise RuntimeIsolationError('IMAGE_DIGEST_STALE: runtime/preview_bridge.py diverge do manifesto artifact.')
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


class ArtifactPreviewProcess:
    """Ciclo de vida gerenciado para contêiner isolado do perfil artifact em modo preview/smoke."""

    def __init__(
        self,
        *,
        container_name: str,
        image_ref: str,
        socket_path: Path,
        ipc_dir: Path,
        data_dir: Path,
        podman_bin: str,
        max_lifetime_seconds: float,
    ):
        self.container_name = container_name
        self.image_ref = image_ref
        self.socket_path = Path(socket_path)
        self.ipc_dir = Path(ipc_dir)
        self.data_dir = Path(data_dir)
        self.podman_bin = podman_bin
        self.max_lifetime_seconds = max_lifetime_seconds
        self.started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self._stopped = False
        self.cleanup_verified = False

    def is_running(self) -> bool:
        with self._lock:
            if self._stopped:
                return False
        insp = subprocess.run(
            [self.podman_bin, 'inspect', '--format', '{{.State.Running}}', self.container_name],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return insp.returncode == 0 and insp.stdout.strip() == 'true'

    def inspect(self) -> dict | None:
        insp = subprocess.run(
            [self.podman_bin, 'inspect', self.container_name],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if insp.returncode != 0:
            return None
        try:
            parsed = json.loads(insp.stdout)
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]
        except ValueError:
            return None
        return None

    def stop(self) -> bool:
        with self._lock:
            if self._stopped:
                return self.cleanup_verified
            self._stopped = True
        subprocess.run(
            [self.podman_bin, 'stop', '--time=2', self.container_name],
            capture_output=True, text=True, timeout=10, check=False,
        )
        subprocess.run(
            [self.podman_bin, 'rm', '--force', '--time=0', self.container_name],
            capture_output=True, text=True, timeout=10, check=False,
        )
        exists = subprocess.run(
            [self.podman_bin, 'container', 'exists', self.container_name],
            capture_output=True, text=True, timeout=5, check=False,
        )
        self.cleanup_verified = exists.returncode == 1
        return self.cleanup_verified

    def __enter__(self) -> 'ArtifactPreviewProcess':
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


class RuntimeManager:
    """Gerencia contêineres Podman rootless sob contratos fail-closed."""

    def __init__(
        self,
        *,
        manifest_path: Path = DEFAULT_MANIFEST_PATH,
        opencode_manifest_path: Path = DEFAULT_OPENCODE_MANIFEST_PATH,
        artifact_manifest_path: Path = DEFAULT_ARTIFACT_MANIFEST_PATH,
        podman_bin: str = 'podman',
    ):
        self.manifest_path = Path(manifest_path)
        self.opencode_manifest_path = Path(opencode_manifest_path)
        self.artifact_manifest_path = Path(artifact_manifest_path)
        self.podman_bin = podman_bin

    def load_manifest(self, *, verify_local_sources: bool = True) -> ImageManifest:
        return load_image_manifest(self.manifest_path, verify_local_sources=verify_local_sources)

    def load_opencode_manifest(
        self,
        *,
        manifest_path: Path | None = None,
        verify_local_sources: bool = True,
    ) -> OpenCodeImageManifest:
        target = Path(manifest_path) if manifest_path is not None else self.opencode_manifest_path
        return load_opencode_manifest(target, verify_local_sources=verify_local_sources)

    def load_artifact_manifest(
        self,
        *,
        manifest_path: Path | None = None,
        verify_local_sources: bool = True,
    ) -> ArtifactImageManifest:
        target = Path(manifest_path) if manifest_path is not None else self.artifact_manifest_path
        return load_artifact_manifest(target, verify_local_sources=verify_local_sources)

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

    def start_artifact_preview(
        self,
        *,
        artifact_dir: Path,
        data_dir: Path,
        ipc_dir: Path,
        entrypoint: str,
        internal_port: int = 8000,
        preview_mode: str = 'demo',
        limits: ResourceLimits | None = None,
        image_ref: str | None = None,
        bridge_path: str | None = None,
        ready_timeout: float = 12.0,
        forbidden_rw_roots: Sequence[Path] = (),
    ) -> ArtifactPreviewProcess:
        """Inicia contêiner isolado do perfil artifact com ponte reversa em /preview-ipc/preview.sock."""
        if preview_mode not in ('demo', 'smoke'):
            raise RuntimeIsolationError('preview_mode inválido; esperado "demo" ou "smoke".')
        if type(internal_port) is not int or internal_port < 1024 or internal_port > 65535:
            raise RuntimeIsolationError('internal_port inválido (esperado 1024..65535).')
        require_text(entrypoint, 'Entrypoint do artefato')

        if image_ref is None or bridge_path is None:
            manifest = self.load_artifact_manifest()
            resolved_image_ref = image_ref or manifest.image_ref
            resolved_bridge_path = bridge_path or manifest.preview_bridge_path
        else:
            resolved_image_ref = image_ref
            resolved_bridge_path = bridge_path
        self.verify_image_present(resolved_image_ref)

        effective_limits = limits or DEFAULT_PREVIEW_LIMITS
        mounts = (
            BindMount(Path(artifact_dir), '/artifact', readonly=True),
            BindMount(Path(data_dir), '/data', readonly=False),
            BindMount(Path(ipc_dir), '/preview-ipc', readonly=False),
        )
        validated_mounts = _validate_mounts(mounts, profile='artifact', forbidden_rw_roots=forbidden_rw_roots)

        name = f'udh-artifact-prev-{uuid.uuid4().hex[:16]}'
        uid = os.getuid()
        gid = os.getgid()

        command = [
            'python3', '-I', '-B', resolved_bridge_path,
            '--socket', '/preview-ipc/preview.sock',
            '--artifact', '/artifact',
            '--data', '/data',
            '--entrypoint', entrypoint,
            '--internal-port', str(internal_port),
            '--ready-timeout', str(max(2.0, min(ready_timeout, 30.0))),
        ]
        env = {
            'HOME': '/data',
            'TMPDIR': '/data',
            'UDH_ARTIFACT_DIR': '/artifact',
            'UDH_DATA_DIR': '/data',
            'UDH_PREVIEW_MODE': preview_mode,
        }

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
            '--workdir=/data',
        ]
        for src, dst, readonly in validated_mounts:
            spec = f'type=bind,source={src},destination={dst}'
            if readonly:
                spec += ',readonly=true'
            create_cmd.extend(['--mount', spec])
        for key, value in sorted(env.items()):
            create_cmd.extend(['--env', f'{key}={value}'])
        create_cmd.append(resolved_image_ref)
        create_cmd.extend(command)

        created = subprocess.run(create_cmd, capture_output=True, text=True, timeout=20, check=False)
        if created.returncode != 0:
            raise RuntimeIsolationError(f'Falha ao criar contêiner de preview artifact: {created.stderr.strip()[:400]}')

        resolved_ipc = Path(ipc_dir).resolve(strict=True)
        resolved_data = Path(data_dir).resolve(strict=True)
        sock_path = resolved_ipc / 'preview.sock'

        started = subprocess.run(
            [self.podman_bin, 'start', name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if started.returncode != 0:
            self._cleanup_container(name)
            raise RuntimeIsolationError(f'Falha ao iniciar contêiner de preview artifact: {started.stderr.strip()[:400]}')

        proc = ArtifactPreviewProcess(
            container_name=name,
            image_ref=resolved_image_ref,
            socket_path=sock_path,
            ipc_dir=resolved_ipc,
            data_dir=resolved_data,
            podman_bin=self.podman_bin,
            max_lifetime_seconds=effective_limits.timeout_seconds,
        )

        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if sock_path.exists() and not sock_path.is_symlink():
                return proc
            if not proc.is_running():
                logs = subprocess.run(
                    [self.podman_bin, 'logs', '--tail', '40', name],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                proc.stop()
                detail = (logs.stderr or logs.stdout or '').strip()[:400]
                raise RuntimeIsolationError(f'Contêiner de preview encerrou antes de expor preview.sock: {detail}')
            time.sleep(0.05)

        logs = subprocess.run(
            [self.podman_bin, 'logs', '--tail', '40', name],
            capture_output=True, text=True, timeout=5, check=False,
        )
        proc.stop()
        detail = (logs.stderr or logs.stdout or '').strip()[:400]
        raise RuntimeIsolationError(f'Timeout aguardando /preview-ipc/preview.sock do contêiner artifact: {detail}')
