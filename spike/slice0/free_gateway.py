"""Gateway do spike: resolve credencial somente no processo externo aos agentes."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from decimal import Decimal
import http.client
import socket
import ssl
import threading
import time

from gateway_fixture import Handler, Server

HOST = 'openrouter.ai'
UPSTREAM_MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'
PROMPT = 'Responda exatamente UDH_REAL_PROBE_OK, sem outros textos.'


def load_credential():
    """Usa o resolvedor oficial, capturando sua saída só na memória do gateway."""
    try:
        result = subprocess.run(
            ['/home/debian/.local/bin/pi', 'auth', 'check', '--provider', 'openrouter',
             '--json', '--credentials', '--no-refresh'],
            capture_output=True, text=True, timeout=15, cwd='/tmp',
            env=dict(os.environ, PI_OFFLINE='1', PI_TELEMETRY='0'))
    except (OSError, subprocess.TimeoutExpired):
        return None, 'resolver_unavailable'
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
        key = payload.get('credentials', '')
    except ValueError:
        key = ''
    if not isinstance(key, str):
        return None, 'unavailable'
    if not re.fullmatch(r'[A-Za-z0-9._~+/-]{8,1024}=*', key):
        return None, 'unavailable'
    return key, 'pi-native-resolver'


def verify_free_model():
    """Confirma no catálogo público que o identificador fixo continua gratuito."""
    connection = http.client.HTTPSConnection(HOST, timeout=15, context=ssl.create_default_context())
    try:
        connection.request('GET', '/api/v1/models', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(4194305)
        if response.status != 200 or len(raw) > 4194304:
            return False
        payload = json.loads(raw)
        model = next((item for item in payload.get('data', [])
                      if item.get('id') == UPSTREAM_MODEL), None)
        return bool(model) and all(Decimal(model['pricing'][field]) == 0
                                   for field in ('prompt', 'completion'))
    finally:
        connection.close()


def forward(handler, _body):
    """Envia somente o prompt de experimento, sem contexto/código do agente."""
    server = handler.server
    phase = json.loads(server.phase_file.read_text())
    agent = phase.get('agent')
    if agent not in ('pi', 'opencode', 'pi-cut'):
        return handler.reject(403, 'phase_denied')
    with server.budget_lock:
        if server.calls >= 4:
            return handler.reject(429, 'run_budget_exceeded')
        server.calls += 1
    payload = json.dumps({'model': UPSTREAM_MODEL, 'stream': True,
                         'messages': [{'role': 'user', 'content': PROMPT}],
                         'max_tokens': 256,
                         'reasoning': {'enabled': False},
                         'provider': {'max_price': {'prompt': 0, 'completion': 0}}}).encode()
    connection = http.client.HTTPSConnection(HOST, timeout=30, context=ssl.create_default_context())
    started = time.monotonic()
    chunks = 0
    byte_count = 0
    model_name = None
    usage = {}
    verdict = 'upstream_incomplete'
    sent_headers = False

    def deadline():
        """Interrompe o socket externo após o orçamento absoluto da chamada."""
        if connection.sock:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    timer = threading.Timer(60, deadline)
    timer.daemon = True
    timer.start()
    try:
        connection.request('POST', '/api/v1/chat/completions', body=payload, headers={
            'Authorization': 'Bearer ' + server.secret,
            'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
        response = connection.getresponse()
        if response.status != 200:
            return handler.reject(502, 'provider_http_' + str(response.status))
        if 'text/event-stream' not in response.getheader('Content-Type', ''):
            return handler.reject(502, 'provider_not_streaming')
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream')
        handler.send_header('Transfer-Encoding', 'chunked')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        sent_headers = True
        while True:
            line = response.readline(65537)
            byte_count += len(line)
            if len(line) > 65536 or byte_count > 1048576 or time.monotonic() - started > 60:
                verdict = 'response_budget_exceeded'
                break
            if not line:
                break
            if server.secret.encode() in line:
                verdict = 'secret_in_response_blocked'
                break
            publish_text = False
            if line.startswith(b'data: '):
                value = line[6:].strip()
                if value != b'[DONE]':
                    parsed = json.loads(value)
                    if server.secret in json.dumps(parsed, ensure_ascii=False):
                        verdict = 'secret_in_response_blocked'
                        break
                    if 'error' in parsed:
                        verdict = 'provider_stream_error'
                        break
                    candidate = parsed.get('model')
                    if isinstance(candidate, str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,180}', candidate):
                        model_name = candidate
                    candidate_usage = parsed.get('usage')
                    if isinstance(candidate_usage, dict):
                        usage = {field: candidate_usage[field] for field in
                                 ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost')
                                 if isinstance(candidate_usage.get(field), (int, float))}
                    chunks += 1
                    has_text = any(isinstance(choice, dict) and
                                   bool(choice.get('delta', {}).get('content'))
                                   for choice in parsed.get('choices', []))
                    publish_text = has_text
                else:
                    verdict = 'real_stream_complete'
                    line += b'\n'
            handler.wfile.write(f'{len(line):x}\r\n'.encode() + line + b'\r\n')
            handler.wfile.flush()
            if publish_text:
                if server.pause or phase.get('pause') is True:
                    # Fecha o evento SSE antes de sinalizar o ponto de corte.
                    handler.wfile.write(b'1\r\n\n\r\n')
                    handler.wfile.flush()
                if not server.first_part.exists():
                    server.first_part.write_text('ready\n')
                if server.pause or phase.get('pause') is True:
                    time.sleep(30)
            if verdict == 'real_stream_complete':
                handler.wfile.write(b'0\r\n\r\n')
                handler.wfile.flush()
                break
        if verdict != 'real_stream_complete':
            handler.connection.shutdown(socket.SHUT_RDWR)
    except (OSError, ValueError, http.client.HTTPException):
        verdict = 'upstream_or_client_error'
        if sent_headers:
            try:
                handler.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        else:
            handler.reject(502, 'upstream_unavailable')
    finally:
        timer.cancel()
        connection.close()
        handler.close_connection = True
        record = {'run_id': server.run_id, 'verdict': verdict,
                  'agent': agent,
                  'upstream_host': HOST, 'requested_model': UPSTREAM_MODEL,
                  'returned_model': model_name, 'stream_data_events': chunks,
                  'response_bytes': byte_count, 'usage': usage,
                  'elapsed_seconds': round(time.monotonic() - started, 3)}
        with server.audit.open('a') as output:
            output.write(json.dumps(record) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--socket')
    parser.add_argument('--audit')
    parser.add_argument('--first-part')
    parser.add_argument('--ready')
    parser.add_argument('--run-id')
    parser.add_argument('--phase-file')
    parser.add_argument('--pause', action='store_true')
    parser.add_argument('--scan', nargs='*')
    args = parser.parse_args()
    if args.status:
        key, source = load_credential()
        print(json.dumps({'credential_available': bool(key), 'source': source}))
        return
    if args.scan is not None:
        key, _source = load_credential()
        root = Path(__file__).resolve().parent
        paths = [Path(path).resolve() for path in args.scan]
        if any(not path.is_relative_to(root) for path in paths):
            raise RuntimeError('Arquivo fora da raiz de evidências')
        safe = bool(key) and all(key.encode() not in path.read_bytes() for path in paths)
        print(json.dumps({'credential_available': bool(key), 'secret_absent': safe}))
        return
    if not all((args.socket, args.audit, args.first_part, args.ready, args.run_id, args.phase_file)):
        parser.error('Parâmetros do gateway incompletos')
    key, source = load_credential()
    if not key or not verify_free_model():
        Path(args.ready).write_text(json.dumps({'ready': False, 'credential_available': bool(key)}))
        return
    with Server(args.socket, Handler) as server:
        os.chmod(args.socket, 0o600)
        server.secret = key
        server.audit = Path(args.audit)
        server.first_part = Path(args.first_part)
        server.run_id = args.run_id
        server.pause = args.pause
        server.phase_file = Path(args.phase_file)
        server.calls = 0
        server.budget_lock = threading.Lock()
        server.forward = forward
        Path(args.ready).write_text(json.dumps({'ready': True, 'credential_available': True,
                                              'source': source, 'zero_price_verified': True,
                                              'model': UPSTREAM_MODEL}))
        server.serve_forever(poll_interval=0.05)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'gateway_error_type': type(error).__name__}))
        raise SystemExit(1)
