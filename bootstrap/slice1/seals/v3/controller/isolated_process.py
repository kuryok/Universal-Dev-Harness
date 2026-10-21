"""Executor transitório: candidato executado apenas no contêiner rootless."""

from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import uuid


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


class IsolatedProcess:
    """Executa um entrypoint explícito, sem importar código do candidato."""

    def __init__(self, candidate: Path, work: Path):
        self.candidate = Path(candidate).resolve(strict=True)
        self.work = Path(work).resolve(strict=True)
        if not self.candidate.is_dir() or not self.work.is_dir():
            raise ValueError("Candidato e dados precisam ser diretórios")
        if (self.candidate == self.work or self.candidate in self.work.parents
                or self.work in self.candidate.parents):
            raise ValueError("Candidato e dados precisam ser diretórios sem sobreposição")
        self.container_name = None

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

    def run(self, entrypoint: str, args=(), timeout: float = 5, on_started=None):
        path = Path(entrypoint)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("Entrypoint precisa ser relativo ao candidato")
        if timeout <= 0:
            raise ValueError("Timeout precisa ser positivo")
        name = "udh-boundary-" + uuid.uuid4().hex
        self.container_name = name
        command = [
            "podman", "create", "--name", name, "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128",
            "--cpus=1", "--memory=384m", "--userns=keep-id",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--volume", "/usr:/usr:ro", "--volume", "/usr/lib:/lib:ro",
            "--volume", "/usr/lib64:/lib64:ro",
            "--volume", f"{self.candidate}:/candidate:ro",
            "--volume", f"{self.work}:/work:rw", "--workdir=/work",
            IMAGE, "/usr/bin/python3", f"/candidate/{path.as_posix()}", *args,
        ]
        result = {"command": command, "container_name": name, "returncode": None,
                  "stdout": "", "stderr": "", "outcome": "INFRA_ERROR",
                  "inspect": None, "removed": False}
        deadline = time.monotonic() + timeout
        attached = None
        try:
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
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
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
        return result

    def start(self, entrypoint: str, args=(), timeout: float = 5, on_started=None):
        return self.run(entrypoint, args=args, timeout=timeout, on_started=on_started)
