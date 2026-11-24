"""Store oficial protegida fora do workspace (selo de AT, artefatos imutáveis, evidências e conformance)."""
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from .domain import CoreError, require_canonical_uuid, require_sha1, require_sha256, require_text, strict_json_loads

FORBIDDEN_SHADOW_NAMES = frozenset({
    'sitecustomize.py',
    'sitecustomize',
    'usercustomize.py',
    'usercustomize',
    'unittest.py',
    'unittest',
    'udh.py',
    'udh',
})
AT_VERSION_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
ADAPTER_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


class TrustStoreError(CoreError):
    """Falha de integridade ou violação na store protegida do Core."""


class TamperingError(TrustStoreError):
    """Adulteração ou sombreamento proibido detectado em artefato ou selo."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def default_store_dir() -> Path:
    xdg = os.environ.get('XDG_DATA_HOME')
    if xdg:
        return Path(xdg) / 'udh' / 'trust_store'
    return Path.home() / '.local' / 'share' / 'udh' / 'trust_store'


def _canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _read_regular_nofollow(path: Path) -> bytes:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise TamperingError(f'Entrada não regular ou symlink proibido: {path.name}.')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        chunks: list[bytes] = []
        while True:
            piece = os.read(fd, 65536)
            if not piece:
                break
            chunks.append(piece)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _scan_and_collect_files(
    root_dir: Path,
    *,
    reject_shadowing: bool,
    ignore_top_git: bool = False,
) -> dict[str, tuple[Path, str]]:
    """Varre recursivamente root_dir com followlinks=False e O_NOFOLLOW."""
    if root_dir.is_symlink():
        raise TamperingError(f'Diretório raiz não pode ser symlink: {root_dir}.')
    root = root_dir.resolve(strict=True)
    if not root.is_dir():
        raise TrustStoreError(f'Diretório inexistente: {root_dir}.')

    collected: dict[str, tuple[Path, str]] = {}
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if ignore_top_git and current_path == root and '.git' in dirnames:
            dirnames.remove('.git')
        dirnames.sort()
        filenames.sort()

        for dirname in list(dirnames):
            entry = current_path / dirname
            st = os.lstat(entry)
            if stat.S_ISLNK(st.st_mode):
                raise TamperingError(f'TAMPERING_DETECTED: symlink proibido em {entry.relative_to(root)}.')
            if not stat.S_ISDIR(st.st_mode):
                raise TamperingError(f'TAMPERING_DETECTED: entrada especial proibida em {entry.relative_to(root)}.')
            if reject_shadowing and dirname in FORBIDDEN_SHADOW_NAMES:
                raise TamperingError(f'TAMPERING_DETECTED: pacote sombreado proibido ({entry.relative_to(root)}).')
            if dirname == '__pycache__':
                dirnames.remove(dirname)

        for filename in filenames:
            entry = current_path / filename
            rel = entry.relative_to(root).as_posix()
            st = os.lstat(entry)
            if stat.S_ISLNK(st.st_mode):
                raise TamperingError(f'TAMPERING_DETECTED: symlink proibido em {rel}.')
            if not stat.S_ISREG(st.st_mode):
                raise TamperingError(f'TAMPERING_DETECTED: arquivo especial proibido em {rel}.')
            if reject_shadowing and (filename in FORBIDDEN_SHADOW_NAMES or filename.endswith('.pth')):
                raise TamperingError(f'TAMPERING_DETECTED: arquivo de sombreamento proibido ({rel}).')
            raw = _read_regular_nofollow(entry)
            collected[rel] = (entry, hashlib.sha256(raw).hexdigest())

    return collected


class TrustStore:
    """Armazenamento confiável do Core (0700 na raiz, diretórios 0755, arquivos 0444)."""

    def __init__(self, root: Path | str):
        path = Path(root)
        if path.is_symlink():
            raise TrustStoreError('Raiz da TrustStore não pode ser um symlink.')
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        self.root = path.resolve(strict=True)
        for subdir in ('sealed_at', 'artifacts', 'evidence', 'audit'):
            sub = self.root / subdir
            sub.mkdir(exist_ok=True)
            os.chmod(sub, 0o755)

    def assert_disjoint_from(self, other: Path) -> None:
        other_resolved = Path(other).resolve()
        if (self.root == other_resolved
                or self.root.is_relative_to(other_resolved)
                or other_resolved.is_relative_to(self.root)):
            raise TrustStoreError('TrustStore não pode se sobrepor ao workspace do agente.')

    def audit_path_for(self, run_id: str) -> Path:
        safe_id = re.sub(r'[^A-Za-z0-9._-]', '_', require_text(run_id, 'Identificador da execução'))
        return self.root / 'audit' / f'{safe_id}.jsonl'

    def seal_at_suite(self, source_dir: Path, *, scenarios: Sequence[str] = ()) -> dict:
        self.assert_disjoint_from(source_dir)
        files_map = _scan_and_collect_files(Path(source_dir), reject_shadowing=True)
        if not any(Path(rel).name.startswith('test_') and rel.endswith('.py') for rel in files_map):
            raise TrustStoreError('Suíte de AT selada deve conter ao menos um arquivo test_*.py.')

        hashes_only = {rel: digest for rel, (_p, digest) in sorted(files_map.items())}
        at_digest = hashlib.sha256(_canonical_json_bytes(hashes_only)).hexdigest()
        at_version = f'sha256:{at_digest}'
        target_root = self.root / 'sealed_at' / at_digest
        tests_dir = target_root / 'tests'
        manifest_file = target_root / 'manifest.json'

        if manifest_file.exists():
            _existing_tests, existing_manifest = self.verify_sealed_at(at_version)
            return existing_manifest

        tmp_root = self.root / 'sealed_at' / f'.tmp-{at_digest}-{os.getpid()}'
        if tmp_root.exists():
            shutil.rmtree(tmp_root)
        try:
            tmp_tests = tmp_root / 'tests'
            tmp_tests.mkdir(parents=True, exist_ok=True)
            os.chmod(tmp_root, 0o755)
            os.chmod(tmp_tests, 0o755)
            for rel, (src_file, _digest) in sorted(files_map.items()):
                dst = tmp_tests / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(dst.parent, 0o755)
                dst.write_bytes(_read_regular_nofollow(src_file))
                os.chmod(dst, 0o444)
            manifest = {
                'schema_version': 1,
                'at_version': at_version,
                'created_at': _utc_now(),
                'scenarios': list(scenarios),
                'files': hashes_only,
            }
            tmp_manifest = tmp_root / 'manifest.json'
            tmp_manifest.write_bytes(_canonical_json_bytes(manifest) + b'\n')
            os.chmod(tmp_manifest, 0o444)
            os.rename(tmp_root, target_root)
            return manifest
        except BaseException:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise

    def verify_sealed_at(self, at_version: str, *, expected_manifest_sha256: str | None = None) -> tuple[Path, dict]:
        if not isinstance(at_version, str) or not AT_VERSION_RE.fullmatch(at_version):
            raise TrustStoreError('at_version inválido; esperado sha256:<64-hex>.')
        at_digest = at_version.split(':', 1)[1]
        target_root = self.root / 'sealed_at' / at_digest
        tests_dir = target_root / 'tests'
        manifest_file = target_root / 'manifest.json'
        if not manifest_file.is_file() or not tests_dir.is_dir():
            raise TrustStoreError(f'AT_INTEGRITY_VIOLATION: selo {at_version} não encontrado na TrustStore.')
        raw = _read_regular_nofollow(manifest_file)
        if expected_manifest_sha256 is not None:
            require_sha256(expected_manifest_sha256, 'Hash do manifesto de AT aprovado')
            if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
                raise TrustStoreError('AT_INTEGRITY_VIOLATION: hash do manifesto de AT diverge da aprovação.')
        manifest = strict_json_loads(raw.decode('utf-8'), 'Manifesto de AT selado')
        if not isinstance(manifest, dict) or manifest.get('at_version') != at_version:
            raise TrustStoreError('AT_INTEGRITY_VIOLATION: manifesto divergente do at_version.')
        expected_files = manifest.get('files')
        if not isinstance(expected_files, dict) or not expected_files:
            raise TrustStoreError('AT_INTEGRITY_VIOLATION: manifesto de AT sem mapa de arquivos.')
        recomputed_version = 'sha256:' + hashlib.sha256(_canonical_json_bytes(expected_files)).hexdigest()
        if recomputed_version != at_version:
            raise TrustStoreError('AT_INTEGRITY_VIOLATION: hash do manifesto não confere com at_version.')

        actual_map = _scan_and_collect_files(tests_dir, reject_shadowing=True)
        actual_hashes = {rel: digest for rel, (_p, digest) in sorted(actual_map.items())}
        if actual_hashes != expected_files:
            raise TrustStoreError('AT_INTEGRITY_VIOLATION: arquivos de AT selados foram adulterados ou diferem do manifesto.')
        return tests_dir, manifest

    def promote_artifact(
        self,
        source_dir: Path,
        *,
        stack_profile: str = 'python3-stdlib-v1',
        commit_sha: str | None = None,
    ) -> dict:
        self.assert_disjoint_from(source_dir)
        require_text(stack_profile, 'Perfil de stack')
        if commit_sha is not None:
            require_sha1(commit_sha, 'Commit SHA')

        files_map = _scan_and_collect_files(Path(source_dir), reject_shadowing=True, ignore_top_git=True)
        if not files_map:
            raise TrustStoreError('Workspace vazio; nenhum arquivo para promover.')

        hashes_only = {rel: digest for rel, (_p, digest) in sorted(files_map.items())}
        artifact_digest = hashlib.sha256(_canonical_json_bytes(hashes_only)).hexdigest()
        target_root = self.root / 'artifacts' / artifact_digest
        manifest_file = target_root / 'manifest.json'

        if manifest_file.exists():
            _tree, existing = self.verify_artifact(artifact_digest)
            return existing

        tmp_root = self.root / 'artifacts' / f'.tmp-{artifact_digest}-{os.getpid()}'
        if tmp_root.exists():
            shutil.rmtree(tmp_root)
        try:
            tmp_tree = tmp_root / 'tree'
            tmp_tree.mkdir(parents=True, exist_ok=True)
            os.chmod(tmp_root, 0o755)
            os.chmod(tmp_tree, 0o755)
            for rel, (src_file, _digest) in sorted(files_map.items()):
                dst = tmp_tree / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(dst.parent, 0o755)
                dst.write_bytes(_read_regular_nofollow(src_file))
                os.chmod(dst, 0o444)
            manifest = {
                'schema_version': 1,
                'artifact_digest': artifact_digest,
                'stack_profile': stack_profile,
                'commit_sha': commit_sha,
                'promoted_at': _utc_now(),
                'files': hashes_only,
            }
            tmp_manifest = tmp_root / 'manifest.json'
            tmp_manifest.write_bytes(_canonical_json_bytes(manifest) + b'\n')
            os.chmod(tmp_manifest, 0o444)
            os.rename(tmp_root, target_root)
            return manifest
        except BaseException:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise

    def verify_artifact(self, artifact_digest: str) -> tuple[Path, dict]:
        require_sha256(artifact_digest, 'Digest do artefato')
        target_root = self.root / 'artifacts' / artifact_digest
        tree_dir = target_root / 'tree'
        manifest_file = target_root / 'manifest.json'
        if not manifest_file.is_file() or not tree_dir.is_dir():
            raise TrustStoreError(f'ARTIFACT_INTEGRITY_VIOLATION: artefato {artifact_digest} ausente.')
        raw = _read_regular_nofollow(manifest_file)
        manifest = strict_json_loads(raw.decode('utf-8'), 'Manifesto do artefato')
        if not isinstance(manifest, dict) or manifest.get('artifact_digest') != artifact_digest:
            raise TrustStoreError('ARTIFACT_INTEGRITY_VIOLATION: manifesto diverge do artifact_digest.')
        expected_files = manifest.get('files')
        if not isinstance(expected_files, dict) or not expected_files:
            raise TrustStoreError('ARTIFACT_INTEGRITY_VIOLATION: manifesto de artefato sem arquivos.')
        recomputed = hashlib.sha256(_canonical_json_bytes(expected_files)).hexdigest()
        if recomputed != artifact_digest:
            raise TrustStoreError('ARTIFACT_INTEGRITY_VIOLATION: hash do manifesto não confere com artifact_digest.')
        actual_map = _scan_and_collect_files(tree_dir, reject_shadowing=True)
        actual_hashes = {rel: digest for rel, (_p, digest) in sorted(actual_map.items())}
        if actual_hashes != expected_files:
            raise TrustStoreError('ARTIFACT_INTEGRITY_VIOLATION: árvore promovida foi adulterada.')
        return tree_dir, manifest

    def save_evidence(self, run_id: str, request_id: str, report: dict) -> tuple[Path, str]:
        require_canonical_uuid(run_id, 'Identificador da execução')
        require_text(request_id, 'Identificador da requisição de evidência')
        run_dir = self.root / 'evidence' / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir, 0o755)
        safe_req = hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:16]
        target = run_dir / f'{safe_req}.json'
        raw = _canonical_json_bytes(report) + b'\n'
        digest = hashlib.sha256(raw).hexdigest()
        if target.exists():
            existing_raw = _read_regular_nofollow(target)
            if existing_raw == raw:
                return target, digest
            raise TrustStoreError('Evidência oficial já existe com conteúdo diferente para esta requisição.')
        tmp = run_dir / f'.tmp-{safe_req}-{os.getpid()}.json'
        tmp.write_bytes(raw)
        os.chmod(tmp, 0o444)
        os.replace(tmp, target)
        return target, digest

    def save_conformance(
        self,
        *,
        policy_sha256: str,
        image_digest: str,
        adapter_name: str,
        adapter_version: str,
        status: str,
        details: dict | None = None,
    ) -> dict:
        require_sha256(policy_sha256, 'Hash SHA-256 da política')
        if not AT_VERSION_RE.fullmatch(image_digest):
            raise TrustStoreError('image_digest inválido para cache de conformance.')
        require_text(adapter_name, 'Nome do adaptador')
        if not ADAPTER_NAME_RE.fullmatch(adapter_name):
            raise TrustStoreError('Nome de adaptador inválido para cache de conformance.')
        require_text(adapter_version, 'Versão do adaptador')
        if status not in ('PASS', 'FAIL'):
            raise TrustStoreError('Status de conformance inválido.')
        record = {
            'schema_version': 1,
            'policy_sha256': policy_sha256,
            'image_digest': image_digest,
            'adapter_name': adapter_name,
            'adapter_version': adapter_version,
            'status': status,
            'verified_at': _utc_now(),
            'details': details or {},
        }
        payload = _canonical_json_bytes(record) + b'\n'
        target = self.root / f'conformance-{adapter_name}.json'
        tmp = self.root / f'.tmp-conformance-{adapter_name}-{os.getpid()}.json'
        tmp.write_bytes(payload)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        if adapter_name == 'pi':
            legacy_target = self.root / 'conformance.json'
            legacy_tmp = self.root / f'.tmp-conformance-legacy-{os.getpid()}.json'
            legacy_tmp.write_bytes(payload)
            os.chmod(legacy_tmp, 0o644)
            os.replace(legacy_tmp, legacy_target)
        return record

    def load_conformance(
        self,
        *,
        policy_sha256: str,
        image_digest: str,
        adapter_name: str = 'pi',
    ) -> dict | None:
        require_text(adapter_name, 'Nome do adaptador')
        if not ADAPTER_NAME_RE.fullmatch(adapter_name):
            raise TrustStoreError('Nome de adaptador inválido para cache de conformance.')
        target = self.root / f'conformance-{adapter_name}.json'
        if not target.exists() and not target.is_symlink() and adapter_name == 'pi':
            target = self.root / 'conformance.json'
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
