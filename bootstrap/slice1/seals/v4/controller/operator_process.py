"""Runtime administrativo transitório com estado privado no contêiner rootless."""

from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time
import tempfile
import uuid

from block_fixtures.operator_launcher import digest, regular, validate


IMAGE = "docker.io/library/alpine@sha256:c64c687cbea9300178b30c95835354e34c4e4febc4badfe27102879de0483b5e"
OUTPUT_LIMIT = 64 * 1024


def _stop(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=2)


def _collect(process, deadline):
    """Drena ambos os pipes sem bloquear e sem acumular saída ilimitada."""
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    outcome = None
    with selectors.DefaultSelector() as selector:
        for name in buffers:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "TIMEOUT"
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                available = OUTPUT_LIMIT - len(buffer)
                buffer.extend(chunk[:available])
                if len(chunk) > available:
                    outcome = "OUTPUT_LIMIT"
                    break
            if outcome:
                break
        if outcome:
            _stop(process)
        else:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = "TIMEOUT"
                _stop(process)
    for name in buffers:
        getattr(process, name).close()
    return {
        "returncode": process.returncode,
        "stdout": buffers["stdout"].decode("utf-8", errors="replace"),
        "stderr": buffers["stderr"].decode("utf-8", errors="replace"),
        "outcome": outcome,
    }


def _spawn(command):
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)


def _command(command, deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Prazo do runtime esgotado")
    return _collect(_spawn(command), deadline)


class OperatorProcess:
    """Entrega o estado privado exclusivamente ao launcher administrativo verificado."""

    def __init__(self, core: Path, state: Path, control: Path, *,
                 core_manifest_sha256: str, control_sha256: str,
                 launcher_sha256: str):
        self.core, self.state, self.control = [Path(path).absolute() for path in
                                              (core, state, control)]
        for path in (self.core, self.state, self.control):
            if path != path.resolve(strict=True) or not path.is_dir():
                raise ValueError("Diretório administrativo inválido ou redirecionado")
            if ':' in str(path) or ',' in str(path):
                raise ValueError("Diretório incompatível com montagem do runtime")
        paths = (self.core, self.state, self.control)
        for index, left in enumerate(paths):
            for right in paths[index + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError("Diretórios administrativos sobrepostos")
        self.core_manifest_sha256 = core_manifest_sha256
        self.control_sha256 = control_sha256
        self.launcher_sha256 = launcher_sha256
        for pin in (core_manifest_sha256, control_sha256, launcher_sha256):
            if not isinstance(pin, str) or len(pin) != 64 or any(
                    char not in '0123456789abcdef' for char in pin):
                raise ValueError("Pin administrativo inválido")
        self.container_name = None
        self._validate()

    def _validate(self):
        for root in (self.core, self.control, self.state):
            if root != root.resolve(strict=True) or not stat.S_ISDIR(root.lstat().st_mode):
                raise ValueError('Raiz administrativa alterada')
        for entry in self.state.rglob('*'):
            mode = entry.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError('Entrada especial no estado administrativo')
        validate(self.core, self.control, self.core_manifest_sha256,
                 self.control_sha256)
        if set(path.name for path in self.control.iterdir()) != {
                'manifest.json', 'config.json', 'operator_launcher.py'}:
            raise ValueError("Inventário de controle divergente")
        launcher = regular(self.control, 'operator_launcher.py')
        if digest(launcher) != self.launcher_sha256:
            raise ValueError("Launcher administrativo divergente")
        template = Path(__file__).parent / 'block_fixtures' / 'operator_launcher.py'
        expected = template.read_bytes().replace(
            b'__CORE_MANIFEST_SHA256__', self.core_manifest_sha256.encode()).replace(
            b'__CONTROL_SHA256__', self.control_sha256.encode())
        if launcher != expected:
            raise ValueError("Launcher não deriva do template confiável")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.container_name:
            self._cleanup(self.container_name)

    @staticmethod
    def _cleanup(name):
        removal = _command(["podman", "rm", "--force", "--time=0", name], time.monotonic() + 3)
        exists = _command(["podman", "container", "exists", name], time.monotonic() + 3)
        confirmed = exists["outcome"] is None and exists["returncode"] == 1
        return confirmed, {"remove": removal, "exists": exists}

    def run(self, args=(), seed_core=False, timeout: float = 5, on_started=None):
        return self._run(args, seed_core, timeout, on_started)

    def _run(self, args=(), seed_core=False, timeout=5, on_started=None, observation=None):
        if timeout <= 0:
            raise ValueError("Timeout precisa ser positivo")
        if not isinstance(seed_core, bool):
            raise ValueError("Modo de seed inválido")
        args = list(args)
        if not all(isinstance(arg, str) for arg in args):
            raise ValueError("Argumentos administrativos inválidos")
        if '--seed-core' in args:
            raise ValueError("Modo de seed reservado ao controlador")
        if '--db' in args or any(arg.startswith('--db=') for arg in args):
            raise ValueError("Banco é fixado pelo controlador administrativo")
        if seed_core:
            args.insert(0, '--seed-core')
        name = "udh-operator-" + uuid.uuid4().hex
        self.container_name = name
        command = [
            "podman", "create", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128",
            "--cpus=1", "--memory=384m", "--userns=keep-id",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--volume", "/usr:/usr:ro", "--volume", "/usr/lib:/lib:ro",
            "--volume", "/usr/lib64:/lib64:ro",
            "--volume", f"{self.core}:/core:ro",
            "--volume", f"{self.control}:/control:ro",
            "--volume", f"{self.state}:/state:rw", "--workdir=/state",
            IMAGE, "/usr/bin/python3", "-B", "/control/operator_launcher.py", *args,
        ]
        if observation is not None:
            directory, template, session = observation
            session['container_name'] = name
            (directory / 'session.json').write_text(json.dumps(session, sort_keys=True) + '\n')
            command[command.index(IMAGE):command.index(IMAGE)] = [
                '--volume', f'{directory}:/observe:rw',
                '--volume', f'{template}:/fault_launcher.py:ro']
            command[command.index('/control/operator_launcher.py')] = '/fault_launcher.py'
        result = {"command": command, "container_name": name, "returncode": None,
                  "stdout": "", "stderr": "", "outcome": "INFRA_ERROR",
                  "inspect": None, "removed": False}
        deadline = time.monotonic() + timeout
        attached = None
        try:
            self._validate()
            created = _command(command, deadline)
            result["create"] = created
            if created["outcome"] or created["returncode"] != 0:
                result["stderr"] = created["stderr"]
                if created["outcome"] == "TIMEOUT":
                    result["outcome"] = "TIMEOUT"
                return result
            attached = _spawn(["podman", "start", "--attach", name])
            # A inspeção acontece enquanto o attach inicia; PID pode aparecer depois.
            while True:
                inspected = _command(["podman", "inspect", name], deadline)
                if inspected["outcome"] or inspected["returncode"] != 0:
                    raise RuntimeError("Não foi possível inspecionar o contêiner")
                info = json.loads(inspected["stdout"])[0]
                result["inspect"] = info
                if info["State"].get("Pid", 0) or attached.poll() is not None:
                    break
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            if on_started is not None:
                on_started(info)
            captured = _collect(attached, deadline)
            result.update(captured)
            if result["outcome"] is None:
                code = result["returncode"]
                result["outcome"] = ("INFRA_ERROR" if code in (125, 126, 127)
                                     else "SIGNAL" if code < 0
                                     else "TERMINATION_UNKNOWN" if code >= 128
                                     else "EXIT")
                if code < 0:
                    result["termination_detail"] = "Sinal observado no cliente Podman"
                elif code >= 128:
                    result["termination_detail"] = (
                        "Código bruto preservado: Podman não distingue sinal no candidato "
                        "de saída explícita com esse valor"
                    )
        except TimeoutError as error:
            result.update(outcome="TIMEOUT", stderr=str(error))
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            result.update(outcome="INFRA_ERROR", stderr=str(error))
        finally:
            if attached is not None:
                _stop(attached)
                for stream in (attached.stdout, attached.stderr):
                    if not stream.closed:
                        stream.close()
            try:
                result["removed"], result["cleanup"] = self._cleanup(name)
                if not result["removed"]:
                    result["outcome"] = "INFRA_ERROR"
            except (OSError, TimeoutError, subprocess.SubprocessError) as error:
                result.update(outcome="INFRA_ERROR", cleanup_error=str(error))
            if result["removed"]:
                self.container_name = None
            try:
                self._validate()
                result['integrity_verified'] = True
            except (OSError, ValueError, TypeError, KeyError) as error:
                result.update(outcome='INFRA_ERROR', integrity_verified=False,
                              integrity_error=str(error))
            if result['outcome'] != 'EXIT':
                result['runtime_outcome'] = result['outcome']
                result['outcome'] = 'INFRA_ERROR'

        return result

    def run_fault(self, args=(), checkpoint=None, timeout=5, seed_core=False):
        """Observa uma execução instrumentada, com descoberta ou interrupção confirmada."""
        import re
        if checkpoint is not None and not re.fullmatch(
                r'(BEFORE_WRITE|AFTER_COMMIT):[1-9][0-9]*', checkpoint):
            raise ValueError('Checkpoint de interrupção inválido')
        template = Path(__file__).parent / 'block_fixtures' / 'fault_launcher.py'
        template_pin = digest(regular(template.parent, template.name))
        session = {'schema': 1, 'container_name': None,
                   'nonce': uuid.uuid4().hex, 'timeout': timeout}
        points = []
        proof = {}
        deadline = time.monotonic() + timeout
        with tempfile.TemporaryDirectory(prefix='udh-observe-', dir=self.control.parent) as temporary:
            directory = Path(temporary).resolve()

            def observe(info):
                sequence = 1
                while time.monotonic() < deadline:
                    for entry in directory.iterdir():
                        if not stat.S_ISREG(entry.lstat().st_mode):
                            raise ValueError('Entrada especial no canal de observação')
                    marker = directory / f'{sequence:06d}.json'
                    if marker.exists():
                        raw = regular(directory, marker.name)
                        if len(raw) > 4096:
                            raise ValueError('Marcador de observação excede limite')
                        record = json.loads(raw)
                        if (not isinstance(record, dict)
                                or record.get('schema') != 1
                                or record.get('container_name') != session['container_name']
                                or record.get('nonce') != session['nonce']
                                or record.get('sequence') != sequence
                                or not re.fullmatch(r'(BEFORE_WRITE|AFTER_COMMIT):[1-9][0-9]*',
                                                    record.get('checkpoint', ''))):
                            raise ValueError('Marcador de observação inválido')
                        kind, number = record['checkpoint'].split(':')
                        expected_number = 1 + sum(
                            point['checkpoint'].startswith(kind + ':') for point in points)
                        expected_keys = {'schema', 'container_name', 'nonce', 'sequence', 'checkpoint'}
                        if kind == 'BEFORE_WRITE':
                            expected_keys.add('sql_sha256')
                            if not re.fullmatch(r'[0-9a-f]{64}', record.get('sql_sha256', '')):
                                raise ValueError('Hash da escrita observado inválido')
                        if set(record) != expected_keys or int(number) != expected_number:
                            raise ValueError('Sequência de checkpoints divergente')
                        points.append(record)
                        if record['checkpoint'] == checkpoint:
                            killed = _command(['podman', 'kill', '--signal=KILL',
                                               session['container_name']], deadline)
                            proof.update(marker=record, kill=killed,
                                         inspected_container_id=info.get('Id'))
                            return
                        pending_ack = directory / f'{sequence:06d}.ack.pending'
                        pending_ack.write_text(session['nonce'])
                        pending_ack.replace(directory / f'{sequence:06d}.ack')
                        sequence += 1
                        continue
                    done = directory / 'done.json'
                    if done.exists():
                        finished = json.loads(regular(directory, 'done.json'))
                        if finished != {'schema': 1, 'container_name': session['container_name'],
                                        'nonce': session['nonce'], 'count': sequence - 1}:
                            raise ValueError('Fim de observação inválido')
                        return
                    time.sleep(0.005)
                raise TimeoutError('Prazo esgotado antes de confirmar observação')

            result = self._run(args=args, seed_core=seed_core, timeout=timeout,
                               on_started=observe, observation=(directory, template, session))
            result.update(checkpoints=points, checkpoint=checkpoint,
                          marker_received=bool(proof.get('marker')),
                          process_killed=bool(proof.get('kill', {}).get('returncode') == 0
                                              and proof.get('kill', {}).get('outcome') is None),
                          fault_template_sha256=template_pin,
                          observation_proof=proof,
                          observation_mode='instrumented-sqlite-trace')
            if digest(regular(template.parent, template.name)) != template_pin:
                result.update(outcome='INFRA_ERROR', integrity_verified=False)
            elif checkpoint is not None:
                kill = proof.get('kill', {})
                if (proof.get('marker', {}).get('checkpoint') == checkpoint
                        and proof.get('inspected_container_id')
                        and kill.get('returncode') == 0 and kill.get('outcome') is None
                        and result.get('runtime_outcome') in ('SIGNAL', 'TERMINATION_UNKNOWN')
                        and result['removed'] and result.get('integrity_verified')):
                    result['outcome'] = 'OBSERVED_INTERRUPT'
                else:
                    result['outcome'] = 'INFRA_ERROR'
            return result

    def start(self, args=(), seed_core=False, timeout=5, on_started=None):
        return self.run(args=args, seed_core=seed_core, timeout=timeout,
                        on_started=on_started)
