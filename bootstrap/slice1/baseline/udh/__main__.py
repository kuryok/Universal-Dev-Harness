"""Stub descartável e deliberadamente incorreto para discriminação dos AT."""
import argparse
import json
import sqlite3
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('command')
parser.add_argument('--db', required=True)
parser.add_argument('--spec-id', default='')
parser.add_argument('--spec-version', default='')
parser.add_argument('--request-id', default='')
parser.add_argument('--run-id', default='')
args = parser.parse_args()
if args.command == 'init':
    with sqlite3.connect(args.db) as connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT, request_id TEXT, spec_id TEXT, spec_version TEXT,
                state TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER, run_id TEXT, kind TEXT, created_at TEXT);
            PRAGMA user_version=1;
        ''')
    print(json.dumps({'schema_version': 1}))
elif args.command == 'create':
    # Não persiste: simula resposta sem cumprir comportamento.
    print(json.dumps({
        'run_id': str(uuid.uuid4()), 'request_id': args.request_id,
        'spec_id': args.spec_id, 'spec_version': args.spec_version,
        'state': 'PENDING', 'created_at': '2026-01-01T00:00:00Z',
        'events': [{'sequence': 1, 'kind': 'RUN_CREATED',
                    'created_at': '2026-01-01T00:00:00Z'}],
    }))
else:
    parser.exit(1, 'Comportamento ainda não implementado no stub.\n')
