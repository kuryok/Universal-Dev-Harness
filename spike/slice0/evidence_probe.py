"""Experimentos sintéticos descartáveis F1–F3; não implementa o Core."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
IMAGE = 'docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'


def digest(path):
    """Identifica bytes sem fazer afirmação sobre seu significado."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    """Coleta resultados fora do domínio gravável do processo simulado."""
    run_id = uuid.uuid4().hex
    evidence = ROOT / 'evidence'
    workspace = ROOT / 'workspace' / ('f-' + run_id)
    fixtures = ROOT / 'host-fixtures' / ('f-' + run_id)
    for directory in (evidence, workspace, fixtures):
        directory.mkdir(parents=True, exist_ok=True)
    records = []
    outcomes = {}

    def collect(label, args):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            item = dict(label=label, args=args, returncode=result.returncode,
                        stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as exc:
            item = dict(label=label, args=args, returncode=None, stdout=str(exc.stdout),
                        stderr='Tempo limite excedido')
        records.append(item)
        return item

    def container(label, script, extra=()):
        name = 'udh-slice0-f-' + uuid.uuid4().hex
        cidfile = fixtures / (name + '.cid')
        args = ['podman', 'run', '--pull=never', '--network=none', '--read-only',
                '--read-only-tmpfs=false', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--userns=keep-id',
                '--pids-limit=32', '--memory=64m', '--memory-swap=64m',
                '--volume', str(workspace) + ':/workspace:rw', '--workdir=/workspace',
                '--name', name, '--cidfile', str(cidfile)]
        try:
            return collect(label, args + list(extra) + [IMAGE, 'sh', '-c', script])
        finally:
            if cidfile.exists():
                cid = cidfile.read_text().strip()
                if len(cid) != 64 or any(c not in '0123456789abcdef' for c in cid):
                    raise RuntimeError('CID inválido; limpeza recusada')
                try:
                    collect(label + '-inspect', ['podman', 'inspect', cid])
                finally:
                    cleanup = collect(label + '-cleanup', ['podman', 'rm', '-f', cid])
                    if cleanup['returncode'] != 0:
                        raise RuntimeError('Falha na limpeza; cidfile preservado')
                    cidfile.unlink(missing_ok=True)

    try:
        fake = container('F1', "printf '{\"status\":\"success\"}\\n' | tee /workspace/report.json; exit 23")
        outcomes['F1'] = dict(status='passou' if fake['returncode'] == 23 and
                             json.loads((workspace / 'report.json').read_text())['status'] == 'success'
                             else 'falhou', official_success=fake['returncode'] == 0,
                             claimed_success=True)
        protected = fixtures / 'protected-synthetic.txt'
        protected.write_text('FIXTURE SINTÉTICA; NÃO É TESTE DE ACEITE\n')
        baseline = digest(protected)
        (fixtures / 'reference.json').write_text(json.dumps({'sha256': baseline}))
        attempt = container('F2', '''set -eu
failed=0
if (echo changed > /protected-fixture) 2>/dev/null; then failed=1; fi
if rm /protected-fixture 2>/dev/null; then failed=1; fi
if ln -sf /workspace/report.json /protected-fixture 2>/dev/null; then failed=1; fi
test "$failed" = 0
printf 'Tentativas de alteração recusadas\\n'
''', ['--volume', str(protected) + ':/protected-fixture:ro'])
        outcomes['F2'] = dict(status='passou' if attempt['returncode'] == 0 and
                             digest(protected) == baseline else 'falhou',
                             sha256_before=baseline, sha256_after=digest(protected),
                             synthetic_fixture=True)
        checkpoint = fixtures / 'checkpoint.json'
        child_code = '''import json, os, pathlib, signal, sys
p = pathlib.Path(sys.argv[1]); stage = sys.argv[2]
tmp = p.with_suffix('.tmp')
with tmp.open('w') as f:
 if stage == 'during':
  f.write('{'); f.flush(); os.fsync(f.fileno())
  print('READY', flush=True); signal.pause()
 json.dump({'run_id':sys.argv[3], 'sequence':1,'last_confirmed_step':'new'}, f)
 f.flush(); os.fsync(f.fileno())
if stage == 'before':
 print('READY', flush=True); signal.pause()
os.replace(tmp, p)
fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
os.fsync(fd); os.close(fd)
print('READY', flush=True)
if stage == 'after': signal.pause()
'''
        cases = []
        for stage in ('during', 'before', 'after', 'normal'):
            checkpoint.write_text(json.dumps({'run_id': run_id, 'sequence': 0,
                                             'last_confirmed_step': 'old'}))
            with checkpoint.open('rb') as handle:
                os.fsync(handle.fileno())
            process = subprocess.Popen([sys.executable, '-c', child_code, str(checkpoint), stage, run_id],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout=10):
                        raise RuntimeError('Filho não alcançou o ponto de corte')
                ready = process.stdout.readline().strip()
                if ready != 'READY':
                    raise RuntimeError('Sinalização de corte inválida')
                if stage != 'normal':
                    process.send_signal(signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=10)
                state = json.loads(checkpoint.read_text())
                expected = 0 if stage in ('during', 'before') else 1
                temporary = checkpoint.with_suffix('.tmp')
                temporary_exists = temporary.exists()
                temporary_valid = None
                if temporary_exists:
                    try:
                        json.loads(temporary.read_text())
                        temporary_valid = True
                    except json.JSONDecodeError:
                        temporary_valid = False
                temporary_ok = (temporary_exists and temporary_valid is False) if stage == 'during' else True
                cases.append(dict(stage=stage, returncode=process.returncode, state=state,
                                  temporary_exists=temporary_exists, temporary_valid_json=temporary_valid,
                                  temporary_sha256=digest(temporary) if temporary_exists else None,
                                  stderr=stderr, passed=temporary_ok and state['sequence'] == expected and
                                  state['run_id'] == run_id and process.returncode ==
                                  (0 if stage == 'normal' else -signal.SIGKILL)))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
        outcomes['F3'] = dict(status='passou' if all(c['passed'] for c in cases) else 'falhou',
                              cases=cases, scope='Corte SIGKILL de processo local; sem queda de energia ou Core/SQLite')
    finally:
        payload = dict(run_id=run_id, outcomes=outcomes, records=records,
                       limits=['Fixtures sintéticas; nenhum AT real',
                               'Coletor e arquivos no host são editáveis pelo usuário; não é store confiável do produto',
                               'F3 não valida recuperação transacional SQLite nem queda de energia'])
        (evidence / 'f-probe.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(outcomes, ensure_ascii=False, indent=2))
    return 0 if all(o['status'] == 'passou' for o in outcomes.values()) and len(outcomes) == 3 else 1


if __name__ == '__main__':
    raise SystemExit(main())
