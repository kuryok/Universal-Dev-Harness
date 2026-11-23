"""Runner oficial F-04 executado em modo isolado (python3 -I -B) no perfil artifact.

Separação de processos contra forja:
- Processo pai: aplica PR_SET_DUMPABLE=0 (fail-closed), lê nonce exclusivamente via stdin
  (fechado antes de tocar /artifact), valida stdlib e ausência de sombreamento em /artifact,
  faz inventário AST estático dos testes em /sealed-at, dispara worker filho isolado
  (também com PR_SET_DUMPABLE=0, stdin=DEVNULL, sem nonce, sem /artifact em sys.path,
  stdout redirecionado para stderr) e só emite o envelope JSON único em stdout após verificar
  cobertura integral do inventário AST.
- Processo worker (--worker): aplica PR_SET_DUMPABLE=0 (fail-closed) antes de rodar testes,
  não conhece o nonce, não adiciona /artifact ao sys.path (ATs caixa-preta B4.3 via
  UDH_ARTIFACT_DIR), executa unittest sobre /sealed-at e grava relatório no pipe dedicado.
"""
import argparse
import ast
import ctypes
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4
FORBIDDEN_NAMES = frozenset({
    'sitecustomize.py',
    'sitecustomize',
    'usercustomize.py',
    'usercustomize',
    'unittest.py',
    'unittest',
    'udh.py',
    'udh',
})


def _disable_dumpable() -> None:
    """Desabilita dumpable (fail-closed) para bloquear /proc/<pid>/mem e /proc/<pid>/fd."""
    libc = ctypes.CDLL(None)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise RuntimeError('Falha ao aplicar PR_SET_DUMPABLE=0.')
    if libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise RuntimeError('PR_GET_DUMPABLE não confirmou valor 0.')


def _scan_artifact_tampering(artifact_dir: Path) -> str | None:
    root = artifact_dir.resolve()
    if not root.is_dir():
        return 'artifact_dir_missing'
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for dirname in list(dirnames):
            entry = current_path / dirname
            st = os.lstat(entry)
            if stat.S_ISLNK(st.st_mode):
                return f'symlink_forbidden:{entry.relative_to(root)}'
            if not stat.S_ISDIR(st.st_mode):
                return f'special_entry_forbidden:{entry.relative_to(root)}'
            if dirname in FORBIDDEN_NAMES:
                return f'shadow_package_forbidden:{entry.relative_to(root)}'
        for filename in filenames:
            entry = current_path / filename
            st = os.lstat(entry)
            if stat.S_ISLNK(st.st_mode):
                return f'symlink_forbidden:{entry.relative_to(root)}'
            if not stat.S_ISREG(st.st_mode):
                return f'special_file_forbidden:{entry.relative_to(root)}'
            if filename in FORBIDDEN_NAMES or filename.endswith('.pth'):
                return f'shadow_file_forbidden:{entry.relative_to(root)}'
    return None


def _is_testcase_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == 'TestCase' or node.id.endswith('TestCase')
    if isinstance(node, ast.Attribute):
        return node.attr == 'TestCase' or node.attr.endswith('TestCase')
    return False


def _discover_expected_tests(at_dir: Path) -> list[str]:
    root = at_dir.resolve()
    expected: list[str] = []
    for path in sorted(root.rglob('test_*.py')):
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            continue
        rel = path.relative_to(root)
        module = '.'.join(rel.with_suffix('').parts)
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(rel))
        for item in tree.body:
            if isinstance(item, ast.ClassDef) and any(_is_testcase_base(base) for base in item.bases):
                for member in item.body:
                    if isinstance(member, ast.FunctionDef) and member.name.startswith('test'):
                        expected.append(f'{module}.{item.name}.{member.name}')
    return sorted(set(expected))


class _StructuredResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.passed_ids: list[str] = []
        self.failed_ids: list[str] = []
        self.error_ids: list[str] = []
        self.skipped_ids: list[str] = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.failed_ids.append(test.id())

    def addError(self, test, err):
        super().addError(test, err)
        self.error_ids.append(test.id())

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.skipped_ids.append(test.id())


def _run_worker(artifact_dir: Path, at_dir: Path, report_fd: int) -> int:
    # Fail-closed: bloqueia ptrace e acesso a /proc/<worker_pid>/mem e /fd por filhos.
    _disable_dumpable()

    artifact_str = str(artifact_dir.resolve())
    at_str = str(at_dir.resolve())
    # B4.3 / F-04: nunca adiciona /artifact ao sys.path do worker; ATs exercitam o artefato
    # pela interface pública (subprocesso/CLI/HTTP) via UDH_ARTIFACT_DIR.
    if at_str not in sys.path:
        sys.path.append(at_str)

    os.environ['UDH_ARTIFACT_DIR'] = artifact_str
    os.environ['UDH_AT_DIR'] = at_str

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=at_str, pattern='test_*.py', top_level_dir=at_str)
    result = _StructuredResult()
    suite.run(result)

    payload = {
        'tests_run': result.testsRun,
        'passed_tests': sorted(result.passed_ids),
        'failed_tests': sorted(result.failed_ids),
        'error_tests': sorted(result.error_ids),
        'skipped_tests': sorted(result.skipped_ids),
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
        'unexpected_successes': len(result.unexpectedSuccesses),
        'was_successful': bool(result.wasSuccessful()),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    with os.fdopen(report_fd, 'wb') as pipe:
        pipe.write(raw)
        pipe.flush()
    return 0 if (result.wasSuccessful() and result.testsRun > 0) else 1


def _emit_envelope(envelope: dict) -> None:
    sys.stdout.write(json.dumps(envelope, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n')
    sys.stdout.flush()


def _run_parent(artifact_dir: Path, at_dir: Path) -> int:
    _disable_dumpable()
    raw_stdin = sys.stdin.buffer.read(4096)
    try:
        sys.stdin.close()
    except OSError:
        pass
    try:
        config = json.loads(raw_stdin.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        sys.stderr.write('Configuração stdin inválida para at_runner.\n')
        return 2
    nonce = config.get('nonce') if isinstance(config, dict) else None
    if not isinstance(nonce, str) or len(nonce) != 64 or any(c not in '0123456789abcdef' for c in nonce):
        sys.stderr.write('Nonce inválido para at_runner.\n')
        return 2

    unittest_file = Path(getattr(unittest, '__file__', '')).resolve()
    artifact_resolved = artifact_dir.resolve()
    if unittest_file.is_relative_to(artifact_resolved):
        _emit_envelope({
            'udhf04': 1,
            'nonce': nonce,
            'tampering_ok': False,
            'tampering_reason': 'unittest_shadowed_by_artifact',
            'expected_tests': [],
            'passed_tests': [],
            'tests_run': 0,
            'failures': 0,
            'errors': 1,
            'skipped': 0,
            'worker_returncode': 2,
            'ok': False,
        })
        return 2

    tampering = _scan_artifact_tampering(artifact_resolved)
    if tampering is not None:
        _emit_envelope({
            'udhf04': 1,
            'nonce': nonce,
            'tampering_ok': False,
            'tampering_reason': tampering,
            'expected_tests': [],
            'passed_tests': [],
            'tests_run': 0,
            'failures': 0,
            'errors': 1,
            'skipped': 0,
            'worker_returncode': 2,
            'ok': False,
        })
        return 2

    expected_tests = _discover_expected_tests(at_dir)
    if not expected_tests:
        _emit_envelope({
            'udhf04': 1,
            'nonce': nonce,
            'tampering_ok': True,
            'tampering_reason': 'no_tests_discovered',
            'expected_tests': [],
            'passed_tests': [],
            'tests_run': 0,
            'failures': 0,
            'errors': 1,
            'skipped': 0,
            'worker_returncode': 1,
            'ok': False,
        })
        return 1

    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)
    worker_env = {
        'PATH': '/opt/udh/bin:/usr/bin:/bin',
        'HOME': '/work',
        'TMPDIR': '/work',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1',
    }
    worker_cmd = [
        sys.executable, '-I', '-B', str(Path(__file__).resolve()),
        '--worker',
        '--artifact', str(artifact_resolved),
        '--at-dir', str(at_dir.resolve()),
        '--report-fd', str(write_fd),
    ]
    worker = subprocess.Popen(
        worker_cmd,
        stdin=subprocess.DEVNULL,
        stdout=sys.stderr,
        stderr=sys.stderr,
        pass_fds=(write_fd,),
        env=worker_env,
        cwd='/work',
    )
    os.close(write_fd)

    chunks: list[bytes] = []
    total = 0
    try:
        with os.fdopen(read_fd, 'rb') as pipe:
            while total < 262144:
                piece = pipe.read(8192)
                if not piece:
                    break
                chunks.append(piece)
                total += len(piece)
    finally:
        worker_rc = worker.wait()

    raw_report = b''.join(chunks)
    try:
        report = json.loads(raw_report.decode('utf-8')) if raw_report else None
    except (UnicodeDecodeError, ValueError):
        report = None

    if not isinstance(report, dict):
        _emit_envelope({
            'udhf04': 1,
            'nonce': nonce,
            'tampering_ok': True,
            'tampering_reason': 'worker_aborted_without_report',
            'expected_tests': expected_tests,
            'passed_tests': [],
            'tests_run': 0,
            'failures': 0,
            'errors': 1,
            'skipped': 0,
            'worker_returncode': worker_rc,
            'ok': False,
        })
        return 1

    passed_tests = report.get('passed_tests')
    if not isinstance(passed_tests, list) or not all(isinstance(x, str) for x in passed_tests):
        passed_tests = []
    tests_run = report.get('tests_run') if isinstance(report.get('tests_run'), int) else 0
    failures = report.get('failures') if isinstance(report.get('failures'), int) else 1
    errors = report.get('errors') if isinstance(report.get('errors'), int) else 1
    skipped = report.get('skipped') if isinstance(report.get('skipped'), int) else 0
    unexpected_successes = report.get('unexpected_successes') if isinstance(report.get('unexpected_successes'), int) else 0

    coverage_ok = set(expected_tests) == set(passed_tests) and len(passed_tests) == len(expected_tests)
    ok = (
        worker_rc == 0
        and bool(report.get('was_successful'))
        and coverage_ok
        and tests_run == len(expected_tests)
        and tests_run > 0
        and failures == 0
        and errors == 0
        and skipped == 0
        and unexpected_successes == 0
    )

    _emit_envelope({
        'udhf04': 1,
        'nonce': nonce,
        'tampering_ok': True,
        'tampering_reason': None if coverage_ok else 'incomplete_test_coverage',
        'expected_tests': expected_tests,
        'passed_tests': sorted(passed_tests),
        'tests_run': tests_run,
        'failures': failures,
        'errors': errors,
        'skipped': skipped,
        'worker_returncode': worker_rc,
        'ok': ok,
    })
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='at_runner')
    parser.add_argument('--artifact', required=True)
    parser.add_argument('--at-dir', required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--report-fd', type=int, default=None)
    args = parser.parse_args(argv)
    if args.worker:
        if args.report_fd is None or args.report_fd < 3:
            return 2
        return _run_worker(Path(args.artifact), Path(args.at_dir), args.report_fd)
    return _run_parent(Path(args.artifact), Path(args.at_dir))


if __name__ == '__main__':
    raise SystemExit(main())
