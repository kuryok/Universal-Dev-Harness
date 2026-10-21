"""Instrumentação protegida do experimento; não faz parte da CLI do produto."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sqlite3
import sys
import time

ROOT = Path('/observe')
SESSION = json.loads((ROOT / 'session.json').read_text())
COUNTERS = {'BEFORE_WRITE': 0, 'AFTER_COMMIT': 0}
SEQUENCE = 0


def barrier(kind, sql=None):
    """Publica o ponto observado e aguarda o reconhecimento do controlador."""
    global SEQUENCE
    COUNTERS[kind] += 1
    SEQUENCE += 1
    record = {'schema': 1, 'container_name': SESSION['container_name'],
              'nonce': SESSION['nonce'], 'sequence': SEQUENCE,
              'checkpoint': f'{kind}:{COUNTERS[kind]}'}
    if sql is not None:
        record['sql_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    path = ROOT / f'{SEQUENCE:06d}.json'
    temporary = ROOT / f'{SEQUENCE:06d}.pending'
    temporary.write_text(json.dumps(record, sort_keys=True) + '\n')
    temporary.rename(path)
    deadline = time.monotonic() + SESSION['timeout']
    acknowledgement = ROOT / f'{SEQUENCE:06d}.ack'
    while not acknowledgement.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Controlador não reconheceu o checkpoint')
        time.sleep(0.005)
    if acknowledgement.read_text() != SESSION['nonce']:
        raise ValueError('Reconhecimento de checkpoint inválido')


def trace(sql):
    statement = re.sub(r'\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*', '', sql,
                       flags=re.S).upper()
    if (re.match(r'(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|REPLACE|REINDEX|VACUUM)\b', statement)
            or re.match(r'PRAGMA\s+(?:\w+\.)?USER_VERSION\s*(?:=|\()', statement)):
        try:
            barrier('BEFORE_WRITE', sql)
        except BaseException:
            # SQLite suprime exceções de trace; falha precisa encerrar o processo.
            os._exit(125)


class ObservedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Callback pertence à instrumentação, nunca à entrada pública do produto.
        super().set_trace_callback(trace)

    def commit(self):
        super().commit()
        barrier('AFTER_COMMIT')

    def set_trace_callback(self, callback):
        raise ValueError('Callback reservado à instrumentação protegida')


original_connect = sqlite3.connect


def connect(*args, **kwargs):
    if 'factory' in kwargs or len(args) > 5:
        raise ValueError('Factory SQLite incompatível com observação protegida')
    return original_connect(*args, factory=ObservedConnection, **kwargs)


sqlite3.connect = connect
try:
    sys.argv = ['/control/operator_launcher.py', *sys.argv[1:]]
    runpy.run_path('/control/operator_launcher.py', run_name='__main__')
finally:
    (ROOT / 'done.json').write_text(json.dumps({
        'schema': 1, 'container_name': SESSION['container_name'],
        'nonce': SESSION['nonce'], 'count': SEQUENCE}, sort_keys=True) + '\n')
