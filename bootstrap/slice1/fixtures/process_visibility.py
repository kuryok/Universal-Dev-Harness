"""Observa o namespace PID sem enviar sinais a qualquer processo."""

import json
import os
from pathlib import Path
import time


def main():
    marker = Path("/work/host-marker").read_bytes().strip()
    if not marker:
        raise ValueError("Marcador host vazio.")
    matches = []
    errors = []
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError as error:
            errors.append({"pid": int(entry.name), "errno": error.errno})
            continue
        if marker in command:
            matches.append(int(entry.name))
    print(json.dumps({"fixture": "process_visibility", "pid": own_pid,
                      "matching_pids": sorted(matches), "read_errors": errors}),
          flush=True)
    deadline = time.monotonic() + 15
    while not Path("/work/release").exists():
        if time.monotonic() >= deadline:
            return 24
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
