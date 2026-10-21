"""Executa candidatos a AT contra stub, sem produzir resultado oficial de produto."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'bootstrap/slice1'
IMAGE = 'docker.io/library/alpine@sha256:c64c687cbea9300178b30c95835354e34c4e4febc4badfe27102879de0483b5e'


def main():
    with tempfile.TemporaryDirectory(prefix='baseline-', dir=BASE) as temporary:
        command = [
            'podman', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
            '--cap-drop=all', '--security-opt=no-new-privileges',
            '--pids-limit=128', '--memory=384m', '--cpus=1',
            '--userns=keep-id', f'--user={os.getuid()}:{os.getgid()}',
            '--env=PYTHONDONTWRITEBYTECODE=1',
            '--env=UDH_CANDIDATE_ROOT=/candidate',
            '--env=UDH_TEST_WORK_ROOT=/work',
            '--workdir=/candidate',
            '--volume', '/usr:/usr:ro',
            '--volume', '/usr/lib:/lib:ro',
            '--volume', '/usr/lib64:/lib64:ro',
            '--volume', str(BASE / 'acceptance') + ':/acceptance:ro',
            '--volume', str(BASE / 'baseline') + ':/candidate:ro',
            '--volume', temporary + ':/work:rw',
            IMAGE, '/usr/bin/python3', '-m', 'unittest',
            'discover', '-s', '/acceptance', '-v',
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    report = {
        'classification': 'bootstrap-negative-baseline-not-official',
        'image': IMAGE,
        'command': command,
        'returncode': result.returncode,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'files': {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ('acceptance', 'baseline')
            for path in sorted((BASE / folder).rglob('*')) if path.is_file()
        },
        'limits': [
            'Arquivos no host continuam graváveis por esta sessão; não existe store oficial selada.',
            'Teste e stub compartilham namespace; não é executor confiável de produto.',
            'Falhas esperadas precisam ser classificadas por cenário; exit não zero sozinho não basta.',
        ],
    }
    destination = BASE / 'evidence/baseline.json'
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(result.stderr)
    print('Resultado experimental salvo em', destination)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
