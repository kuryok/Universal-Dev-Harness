"""Snapshot transitório verificado; não constitui store oficial de AT."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

REFERENCE = 'dc848ba'
BASELINE = 'bootstrap/slice1/evidence/baseline.json'
ALLOWED = (
    'bootstrap/slice1/acceptance/test_core_state.py',
    'bootstrap/slice1/acceptance/coverage.json',
)
IMAGE = 'docker.io/library/alpine@sha256:c64c687cbea9300178b30c95835354e34c4e4febc4badfe27102879de0483b5e'


class SnapshotError(ValueError):
    """Entrada ou integridade incompatível com a referência confiável."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(root, relative):
    """Lê arquivo regular sem aceitar symlinks nos componentes autorizados."""
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise SnapshotError('Caminho fora da lista permitida')
    current = root
    if not stat.S_ISDIR(current.lstat().st_mode):
        raise SnapshotError('Raiz deve ser diretório real')
    for part in relative.parts[:-1]:
        current /= part
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise SnapshotError('Diretório intermediário inválido ou symlink')
    try:
        descriptor = os.open(root / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise SnapshotError('Arquivo não regular')
            data = source.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise SnapshotError('Arquivo excede limite do snapshot')
            return data
    except OSError as exc:
        raise SnapshotError('Arquivo ausente, inacessível ou symlink') from exc


def reference_files(root):
    """A referência vem do controlador/Git fixado, nunca do candidato."""
    result = subprocess.run(
        ['git', '-C', str(root), 'show', f'{REFERENCE}:{BASELINE}'],
        capture_output=True, timeout=10, check=True,
    )
    if len(result.stdout) > 1024 * 1024:
        raise SnapshotError('Referência excede limite')
    reference = json.loads(result.stdout)
    if digest(regular_bytes(root, BASELINE)) != digest(result.stdout):
        raise SnapshotError('Evidência de referência foi alterada no host')
    return reference['files']


def create_snapshot(root, store, run_id):
    root, store = Path(root), Path(store)
    expected = reference_files(root)
    # Ler e validar tudo antes de criar o snapshot de saída.
    contents = {name: regular_bytes(root, name) for name in ALLOWED}
    for name, data in contents.items():
        if digest(data) != expected[name]:
            raise SnapshotError('Arquivo difere da referência aprovada: ' + name)
    store.mkdir(exist_ok=False)
    copied = store / 'files'
    copied.mkdir()
    for name, data in contents.items():
        destination = copied / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as output:
            output.write(data)
    components = {}
    for name in ('trust_snapshot.py', 'isolated_process.py', 'check_boundary.py'):
        relative = 'bootstrap/slice1/' + name
        components[relative] = digest(regular_bytes(root, relative))
    manifest = {
        'format_version': 1, 'run_id': run_id, 'reference_commit': REFERENCE,
        'classification': 'snapshot-transitorio-sem-at-version-oficial',
        'files': {name: digest(data) for name, data in contents.items()},
        'controller': components,
        'runtime': {
            'image': IMAGE, 'python_version': sys.version,
            'python_binary': str(Path(sys.executable).resolve()),
            'python_sha256': digest(Path(sys.executable).resolve().read_bytes()),
        },
    }
    data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    (store / 'manifest.json').write_bytes(data)
    pin = digest(data)
    verify_snapshot(store, pin)
    return manifest, pin


def verify_snapshot(store, expected_manifest_sha256):
    """O pin pertence ao controlador e não é recalculado a partir do candidato."""
    store = Path(store)
    try:
        data = regular_bytes(store, 'manifest.json')
        if digest(data) != expected_manifest_sha256:
            raise SnapshotError('Manifesto adulterado')
        manifest = json.loads(data)
        if set(manifest['files']) != set(ALLOWED):
            raise SnapshotError('Lista de arquivos divergente')
        actual = set()
        copied = store / 'files'
        if not stat.S_ISDIR(copied.lstat().st_mode):
            raise SnapshotError('Raiz de arquivos inválida')
        for parent, directories, files in os.walk(copied, followlinks=False):
            for name in directories + files:
                entry = Path(parent) / name
                if entry.is_symlink():
                    raise SnapshotError('Symlink no snapshot')
            actual.update(str((Path(parent) / name).relative_to(copied)) for name in files)
        if actual != set(ALLOWED):
            raise SnapshotError('Arquivo ausente ou não autorizado no snapshot')
        for name, expected in manifest['files'].items():
            if digest(regular_bytes(copied, name)) != expected:
                raise SnapshotError('Bytes do snapshot divergentes: ' + name)
        return manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SnapshotError('Snapshot incompleto ou inválido') from exc
