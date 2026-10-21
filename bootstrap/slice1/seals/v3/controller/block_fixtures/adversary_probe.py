"""Sonda sintética: não tenta sinalizar host nem abrir canais do runtime."""
import json
import os
import subprocess
from pathlib import Path

paths = ('/state', '/control', '/observe', '/core', '/run/podman/podman.sock', '/var/run/docker.sock')
claim = subprocess.run(['/usr/bin/python3', '-B', '-m', 'udh', 'block', '--db', '/state/udh.sqlite', '--approved', 'true'], capture_output=True, text=True, env={**os.environ, 'PYTHONPATH': '/candidate'})
print(json.dumps({'absent': {path: not Path(path).exists() for path in paths},
                  'authority_claims_rejected': claim.returncode in (2, 3) and not claim.stdout,
                  'extra_fds': [entry.name for entry in Path('/proc/self/fd').iterdir()
                                if entry.name.isdigit() and int(entry.name) > 3],
                  'admin_environment_absent': all(key not in os.environ for key in
                    ('UDH_BLOCK_BRIDGE_CONFIG', 'UDH_OPERATOR_STATE_ROOT'))}))
