"""Mediador SINTÉTICO do spike: Unix socket, sem provedor ou credencial real."""
import argparse
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time

TOKEN = 'UDH_SYNTHETIC_CAPABILITY_ONLY'
MODEL = 'udh-spike'


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(8)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *_args):
        """Não registrar cabeçalhos, corpo ou credenciais."""

    def reject(self, status, verdict):
        self.audit(status, verdict)
        payload = json.dumps({'error': verdict}).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def audit(self, status, verdict):
        record = {'run_id': self.server.run_id, 'status': status,
                  'verdict': verdict, 'peer_uid': self.peer_uid,
                  'time_monotonic': time.monotonic()}
        with self.server.audit.open('a') as output:
            output.write(json.dumps(record) + '\n')

    def dispatch(self):
        self.connection.settimeout(3)
        _pid, self.peer_uid, _gid = struct.unpack(
            '3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if self.peer_uid != os.getuid():
            return self.reject(403, 'peer_denied')
        if self.command != 'POST' or self.path != '/v1/chat/completions':
            return self.reject(403, 'route_denied')
        if len(self.headers.get_all('Authorization', [])) != 1 or self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            return self.reject(401, 'capability_denied')
        if len(self.headers.get_all('X-UDH-Purpose', [])) != 1 or self.headers.get('X-UDH-Purpose') != 'inference':
            return self.reject(403, 'purpose_denied')
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            return self.reject(400, 'unsupported_framing')
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if length < 0 or length > 262144:
                return self.reject(413, 'size_denied')
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self.reject(400, 'incomplete_body')
            body = json.loads(raw)
            if not isinstance(body, dict):
                return self.reject(400, 'invalid_body')
        except (ValueError, OSError):
            return self.reject(400, 'invalid_body')
        if body.get('model') != MODEL or body.get('stream') is not True:
            return self.reject(403, 'model_or_mode_denied')
        if not isinstance(body.get('messages'), list):
            return self.reject(400, 'messages_denied')
        if getattr(self.server, 'forward', None):
            return self.server.forward(self, body)
        self.audit(200, 'synthetic_stream')
        with self.server.audit.open('a') as output:
            output.write(json.dumps({'run_id': self.server.run_id,
                                     'has_tools': bool(body.get('tools')),
                                     'messages_count': len(body.get('messages', []))}) + '\n')
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Connection', 'close')
        self.end_headers()

        def emit(delta, finish=None):
            record = {'id': 'udh-synthetic', 'object': 'chat.completion.chunk',
                      'created': 1, 'model': MODEL,
                      'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            payload = ('data: ' + json.dumps(record) + '\n\n').encode()
            self.wfile.write(f'{len(payload):x}\r\n'.encode() + payload + b'\r\n')
            self.wfile.flush()

        try:
            emit({'role': 'assistant', 'content': ''})
            emit({'content': 'SPIKE_'})
            self.server.first_part.write_text('ready\n')
            if self.server.pause:
                time.sleep(30)  # O coordenador mata o processo neste ponto de corte.
            time.sleep(0.15)
            emit({'content': 'SYNTHETIC_OK'})
            emit({}, 'stop')
            payload = b'data: [DONE]\n\n'
            self.wfile.write(f'{len(payload):x}\r\n'.encode() + payload + b'\r\n0\r\n\r\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.audit(499, 'client_disconnected')
        self.close_connection = True

    do_POST = dispatch
    do_GET = dispatch
    do_DELETE = dispatch
    do_PUT = dispatch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--first-part', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--pause', action='store_true')
    args = parser.parse_args()
    with Server(args.socket, Handler) as server:
        os.chmod(args.socket, 0o600)
        server.audit = Path(args.audit)
        server.first_part = Path(args.first_part)
        server.run_id = args.run_id
        server.pause = args.pause
        server.serve_forever(poll_interval=0.05)


if __name__ == '__main__':
    main()
