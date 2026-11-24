"""Valida um snapshot OpenSpec aprovado sem alterar a etapa da jornada."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time


_CLI_VERSION = '2.1.1'
_CLI_TIMEOUT_SECONDS = 15
_OUTPUT_LIMIT = 256 * 1024
_FILE_LIMIT = 64 * 1024
_SNAPSHOT_LIMIT = 256 * 1024
_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_REVISION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
_OTHER_DELTAS = re.compile(r'^\s*##[ \t]+(?:MODIFIED|REMOVED|RENAMED)[ \t]+Requirements\b', re.M | re.I)
_DOCUMENTS = {'proposal', 'tasks', 'specs'}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class SnapshotError(ValueError):
    """Os artefatos fornecidos não satisfazem o contrato do snapshot."""


class _CLIError(Exception):
    pass


@dataclass(frozen=True)
class SpecValidationResult:
    change_id: str
    revision: str
    snapshot_sha256: str
    code: str
    cli_version: str | None


def _require_slug(value, label):
    if type(value) is not str or not 1 <= len(value) <= 64 or not _SLUG.fullmatch(value):
        raise SnapshotError(f'{label} inválido.')


def _files(change_id, artifacts):
    _require_slug(change_id, 'ID do change')
    if (type(artifacts) is not dict or len(artifacts) not in (3, 4)
            or any(type(key) is not str for key in artifacts)
            or not _DOCUMENTS <= artifacts.keys() <= _DOCUMENTS | {'design'}):
        raise SnapshotError('Conjunto de artefatos OpenSpec inválido.')
    specs = artifacts['specs']
    if type(specs) is not dict or not 1 <= len(specs) <= 16:
        raise SnapshotError('O snapshot deve declarar de 1 a 16 capacidades.')

    files = {f'{name}.md': content for name, content in artifacts.items() if name != 'specs'}
    for capability, content in specs.items():
        _require_slug(capability, 'ID da capacidade')
        files[f'specs/{capability}/spec.md'] = content
    total = 0
    for path, content in files.items():
        if type(content) is not bytes or not 1 <= len(content) <= _FILE_LIMIT:
            raise SnapshotError('Cada artefato deve conter de 1 byte a 64 KiB.')
        total += len(content)
        if total > _SNAPSHOT_LIMIT:
            raise SnapshotError('O snapshot excede 256 KiB.')
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            raise SnapshotError('Artefato inválido: exige UTF-8.') from None
        if path.startswith('specs/') and _OTHER_DELTAS.search(text):
            raise SnapshotError('O snapshot só admite requisitos novos.')
    return files


def _digest(files):
    pairs = [[path, hashlib.sha256(files[path]).hexdigest()] for path in sorted(files)]
    return hashlib.sha256(json.dumps(pairs, separators=(',', ':')).encode('utf-8')).hexdigest()


def snapshot_digest(change_id, artifacts) -> str:
    """Calcula o digest a ser apresentado à rota confiável de aprovação."""
    return _digest(_files(change_id, artifacts))


def _matches_snapshot(root, expected):
    directories = {parent.as_posix() for name in expected for parent in Path(name).parents}
    remaining = set(expected)

    def visit(dir_fd, prefix):
        with os.scandir(dir_fd) as entries:
            for entry in entries:
                path = f'{prefix}{entry.name}'
                if path in directories:
                    child = os.open(entry.name, _DIR_FLAGS, dir_fd=dir_fd)
                    try:
                        if not visit(child, f'{path}/'):
                            return False
                    finally:
                        os.close(child)
                elif path in remaining and entry.is_file(follow_symlinks=False):
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    with os.fdopen(os.open(entry.name, flags, dir_fd=dir_fd), 'rb') as stream:
                        content = expected[path]
                        if (not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                                or stream.read(len(content) + 1) != content):
                            return False
                    remaining.remove(path)
                else:
                    return False
        return True

    try:
        root_fd = os.open(root, _DIR_FLAGS)
        try:
            return visit(root_fd, '') and not remaining
        finally:
            os.close(root_fd)
    except OSError:
        return False


def _invoke(command, *, cwd, env):
    deadline = time.monotonic() + _CLI_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except FileNotFoundError:
        raise _CLIError('CLI_MISSING') from None
    except OSError:
        raise _CLIError('CLI_INCOMPATIBLE') from None

    stdout = bytearray()
    remaining_bytes = _OUTPUT_LIMIT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise _CLIError('CLI_TIMEOUT')
                for key, _ in selector.select(remaining_seconds):
                    chunk = os.read(key.fd, min(8192, remaining_bytes + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining_bytes -= len(chunk)
                    if remaining_bytes < 0:
                        raise _CLIError('CLI_OUTPUT_LIMIT')
                    if key.data:
                        stdout.extend(chunk)
        return process.wait(timeout=max(0, deadline - time.monotonic())), bytes(stdout)
    except subprocess.TimeoutExpired:
        raise _CLIError('CLI_TIMEOUT') from None
    except OSError:
        raise _CLIError('CLI_OUTPUT_INVALID') from None
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close()
        process.stderr.close()


def _unique_object(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError('Campo JSON repetido.')
    return data


def _invalid_constant(_):
    raise ValueError('Constante JSON inválida.')


def _verdict(returncode, stdout, change_id):
    try:
        data = json.loads(stdout.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        return 'CLI_OUTPUT_INVALID'
    items = data.get('items') if type(data) is dict else None
    if type(items) is not list or len(items) != 1 or type(items[0]) is not dict:
        return 'CLI_OUTPUT_INVALID'
    item = items[0]
    if item.get('type') != 'change' or item.get('id') != change_id or type(item.get('valid')) is not bool:
        return 'CLI_OUTPUT_INVALID'
    return 'VALIDATED' if returncode == 0 and item['valid'] else 'VALIDATION_REJECTED'


def validate_spec_snapshot(change_id, revision, artifacts, *, approval, cli_path, node_dir) -> SpecValidationResult:
    """Executa a CLI configurada sobre os bytes vinculados pela aprovação confiável."""
    if type(revision) is not str or not _REVISION.fullmatch(revision):
        raise SnapshotError('Revisão documental inválida.')
    files = _files(change_id, artifacts)
    digest = _digest(files)
    version = None

    def result(code):
        return SpecValidationResult(change_id, revision, digest, code, version)

    if (type(approval) is not tuple or any(type(value) is not str for value in approval)
            or approval != (change_id, revision, digest)):
        return result('APPROVAL_MISMATCH')
    cli_path, node_dir = Path(cli_path), Path(node_dir)
    if not cli_path.is_absolute() or not node_dir.is_absolute() or ':' in str(node_dir):
        return result('CLI_INCOMPATIBLE')

    expected = {f'openspec/changes/{change_id}/{path}': content for path, content in files.items()}
    with tempfile.TemporaryDirectory(prefix='udh-spec-') as temporary:
        base = Path(temporary)
        cwd = base / 'snapshot'
        for relative, content in expected.items():
            destination = cwd / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        env = {'PATH': str(node_dir), 'OPENSPEC_TELEMETRY': '0'}
        for key in ('HOME', 'TMPDIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
            directory = base / key.lower()
            directory.mkdir(mode=0o700)
            env[key] = str(directory)

        def run(*args):
            if not _matches_snapshot(cwd, expected):
                raise _CLIError('SNAPSHOT_CHANGED')
            try:
                return _invoke([str(cli_path), *args], cwd=cwd, env=env)
            finally:
                if not _matches_snapshot(cwd, expected):
                    raise _CLIError('SNAPSHOT_CHANGED') from None

        try:
            status, output = run('--version')
            if status != 0 or output.strip() != _CLI_VERSION.encode('ascii'):
                return result('CLI_INCOMPATIBLE')
            version = _CLI_VERSION
            status, output = run('validate', '--help')
            if status != 0 or not re.search(rb'^[ \t]+--strict(?:[ \t]|$)', output, re.M):
                return result('CLI_INCOMPATIBLE')
            status, output = run('validate', change_id, '--type', 'change', '--strict', '--no-interactive', '--json')
            return result(_verdict(status, output, change_id))
        except _CLIError as error:
            return result(error.args[0])
