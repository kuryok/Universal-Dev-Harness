"""Identificação determinística do preparo transitório, sem assinatura do host."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class SealError(ValueError):
    """O selo ou sua origem não atende ao contrato fixo."""


SOURCE_FILES = {
    'tests/test_core_state.py': 'bootstrap/slice1/integrated_acceptance/test_core_state.py',
    'tests/coverage.json': 'bootstrap/slice1/integrated_acceptance/coverage.json',
    'controller/cli_bridge.py': 'bootstrap/slice1/cli_bridge.py',
    'controller/isolated_process.py': 'bootstrap/slice1/isolated_process.py',
    'controller/seal_version.py': 'bootstrap/slice1/seal_version.py',
    'controller/integrate_acceptance.py': 'bootstrap/slice1/integrate_acceptance.py',
}
SPEC_FILES = (
    'openspec/changes/slice1-core-state/acceptance.md',
    'openspec/changes/slice1-core-state/bootstrap-executor.md',
    'openspec/changes/slice1-core-state/at-integration.md',
)
APPROVAL = 'Integrar os AT ao controlador externo e definir o selo da primeira versão, antes do Core + SQLite.'


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError) as exc:
        raise SealError('JSON inválido no selo') from exc


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _safe(path: Path, *, directory=False):
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        try:
            mode = part.lstat().st_mode
        except OSError as exc:
            raise SealError(f'Caminho ausente: {part}') from exc
        if stat.S_ISLNK(mode):
            raise SealError(f'Symlink recusado: {part}')
        if part != path and not stat.S_ISDIR(mode):
            raise SealError(f'Intermediário inválido: {part}')
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise SealError(f'Tipo inválido: {path}')
    return path


def _review_valid(review, files):
    if not isinstance(review, dict) or review.get('status') != 'PASS':
        raise SealError('Revisão independente PASS obrigatória')
    if not isinstance(review.get('review_id'), str) or not review['review_id'].strip():
        raise SealError('Identificador da revisão obrigatório')
    if review.get('files') != files or review.get('unresolved_findings') != []:
        raise SealError('Revisão não corresponde aos hashes atuais ou possui pendências')
    baseline = review.get('baseline_gate')
    if (not isinstance(baseline, dict) or baseline.get('status') != 'PASS'
            or type(baseline.get('tests_run')) is not int or baseline['tests_run'] != 10
            or type(baseline.get('infrastructure_errors')) is not int
            or baseline['infrastructure_errors'] != 0):
        raise SealError('Baseline negativo obrigatório antes da publicação')


def verify_seal(destination: Path, expected_at_version: str):
    """Valida a cópia selada contra a identidade fornecida pelo chamador."""
    destination = _safe(destination, directory=True)
    try:
        raw = _safe(destination / 'manifest.json').read_bytes()
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or set(manifest) != {'at_version', 'body'}:
            raise SealError('Campos de manifesto inválidos')
        body = manifest['body']
        required = {'schema', 'generation', 'previous_at_version', 'status', 'files',
                    'specs', 'provenance', 'approval', 'review', 'review_sha256'}
        if not isinstance(body, dict) or set(body) != required:
            raise SealError('Campos de corpo inválidos')
        version = 'sha256:' + _hash(_canonical(body))
        if manifest['at_version'] != version or version != expected_at_version:
            raise SealError('STALE: identidade divergente')
        if raw != _canonical(manifest) + b'\n':
            raise SealError('Serialização de manifesto divergente')
        if (type(body['schema']) is not int or body['schema'] != 1
                or type(body['generation']) is not int or body['generation'] != 1
                or body['previous_at_version'] is not None):
            raise SealError('Formato ou cadeia inválidos')
        if body['approval'] != APPROVAL or body['provenance'] != {'acceptance_commit': 'dc848ba', 'boundary_commit': 'de2b70d'}:
            raise SealError('Origem ou aprovação inválida')
        for mapping, expected in [(body['files'], SOURCE_FILES), (body['specs'], SPEC_FILES)]:
            if not isinstance(mapping, dict) or set(mapping) != set(expected):
                raise SealError('Lista de caminhos inválida')
            if any(not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest) for digest in mapping.values()):
                raise SealError('Hash inválido')
        if body['status'] == 'DRAFT':
            if body['review'] is not None or body['review_sha256'] is not None:
                raise SealError('Rascunho com revisão inválida')
        elif body['status'] == 'PUBLISHED':
            _review_valid(body['review'], body['files'])
            if body['review_sha256'] != _hash(_canonical(body['review'])):
                raise SealError('Identidade da revisão divergente')
        else:
            raise SealError('Estado inválido')
        expected_files = set(SOURCE_FILES) | {'manifest.json'}
        expected_dirs = {'tests', 'controller'}
        seen = set()
        for path in destination.rglob('*'):
            relative = path.relative_to(destination).as_posix()
            if path.is_symlink():
                raise SealError('Symlink no selo')
            if path.is_dir():
                if relative not in expected_dirs:
                    raise SealError('Diretório extra no selo')
            else:
                _safe(path)
                seen.add(relative)
        if seen != expected_files:
            raise SealError('Arquivos ausentes ou extras no selo')
        for relative, digest in body['files'].items():
            if _hash(_safe(destination / relative).read_bytes()) != digest:
                raise SealError(f'STALE: arquivo divergente: {relative}')
        return manifest
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, SealError):
            raise
        raise SealError('Manifesto inválido') from exc


def _publish_exclusive(source: Path, destination: Path):
    # renameat2 é o primitivo Linux que impede substituir até um diretório vazio.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise SealError('Publicação exclusiva indisponível neste host')
    result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(destination)
        raise SealError(f'Falha de publicação exclusiva: errno {error}')


def create_seal(root: Path, destination: Path, review: dict | None = None):
    """Cria/reutiliza geração 1; nunca atualiza uma versão existente."""
    root = _safe(root, directory=True)
    destination = Path(os.path.abspath(destination))
    _safe(destination.parent, directory=True)
    try:
        relative = destination.relative_to(root / 'bootstrap/slice1')
    except ValueError as exc:
        raise SealError('Destino fora do preparo permitido') from exc
    if review is None:
        if not any(part.startswith('integration-') for part in relative.parts):
            raise SealError('DRAFT exige destino temporário integration-*')
    elif relative.as_posix() != 'seals/v1':
        raise SealError('Publicação inicial exige seals/v1')
    contents = {target: _safe(root / source).read_bytes() for target, source in SOURCE_FILES.items()}
    files = {target: _hash(data) for target, data in contents.items()}
    if review is not None:
        _review_valid(review, files)
        review = json.loads(_canonical(review))
    body = {'schema': 1, 'generation': 1, 'previous_at_version': None,
            'status': 'DRAFT' if review is None else 'PUBLISHED', 'files': files,
            'specs': {source: _hash(_safe(root / source).read_bytes()) for source in SPEC_FILES},
            'provenance': {'acceptance_commit': 'dc848ba', 'boundary_commit': 'de2b70d'},
            'approval': APPROVAL, 'review': review,
            'review_sha256': None if review is None else _hash(_canonical(review))}
    manifest = {'at_version': 'sha256:' + _hash(_canonical(body)), 'body': body}
    if destination.exists() or destination.is_symlink():
        return verify_seal(destination, manifest['at_version'])
    staging = Path(tempfile.mkdtemp(prefix='.seal-staging-', dir=destination.parent))
    try:
        for target, data in contents.items():
            path = staging / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / 'manifest.json').write_bytes(_canonical(manifest) + b'\n')
        verify_seal(staging, manifest['at_version'])
        try:
            _publish_exclusive(staging, destination)
        except FileExistsError:
            return verify_seal(destination, manifest['at_version'])
        return verify_seal(destination, manifest['at_version'])
    finally:
        if staging.exists():
            shutil.rmtree(staging)
