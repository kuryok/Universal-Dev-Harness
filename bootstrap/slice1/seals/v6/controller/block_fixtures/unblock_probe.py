"""Sonda complementar em contêiner adversário, sem banco administrativo montado."""
from contextlib import redirect_stdout
import io
import json
import runpy
import sqlite3

legacy = io.StringIO()
with redirect_stdout(legacy):
    runpy.run_path('/candidate/legacy_probe.py')
result = json.loads(legacy.getvalue())
attempts = []
original = sqlite3.connect


def forbidden_connect(*args, **kwargs):
    attempts.append(True)
    raise AssertionError('Acesso SQLite anterior à autorização')


sqlite3.connect = forbidden_connect
try:
    namespace = runpy.run_module('udh.operator', run_name='unblock_negative_fixture')
    denied = []
    for context in (None, {'approved': True}, {'schema': 2, 'allowed_operations': ('UNBLOCK_RUN',)}):
        output = io.StringIO()
        with redirect_stdout(output):
            code = namespace['main'](argv=['unblock', '--approved', 'true'], context=context)
        data = json.loads(output.getvalue())
        denied.append(code == 3 and data.get('decision') == 'DENY')
    result['unblock_context_denied_without_sqlite'] = all(denied) and not attempts
finally:
    sqlite3.connect = original
print(json.dumps(result))
