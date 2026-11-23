"""Testes complementares do RuntimeManager, Gateway stdlib endurecido e PiAdapter."""
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest

from udh.adapters.pi import PiAdapter, parse_pi_jsonl
from udh.gateway import (
    DEFAULT_STUB_MODEL,
    GatewayConfig,
    GatewayError,
    InferenceGateway,
    SecretWindowScanner,
    build_openrouter_payload,
    verify_openrouter_free_model,
)
from udh.policy import policy_v2_sha256
from udh.runtime import BindMount, RuntimeIsolationError, RuntimeManager, _validate_mounts
from udh.trust_store import TrustStore


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._stream = BytesIO(body)
        self._headers = headers or {}

    def read(self, amt=None):
        return self._stream.read(amt)

    def readline(self, limit=-1):
        return self._stream.readline(limit)

    def getheader(self, name, default=''):
        return self._headers.get(name, default)


class _FakeHTTPSConnection:
    def __init__(self, responses: list[_FakeHTTPResponse]):
        self._responses = list(responses)
        self.requests: list[tuple] = []
        self.sock = None

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers or {}))

    def getresponse(self):
        return self._responses.pop(0)

    def close(self):
        pass


def _raw_uds(sock_path: Path, raw: bytes) -> tuple[int, str]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
        cli.settimeout(4.0)
        cli.connect(str(sock_path))
        cli.sendall(raw)
        buf = bytearray()
        while True:
            chunk = cli.recv(8192)
            if not chunk:
                break
            buf.extend(chunk)
    text = buf.decode('utf-8', errors='replace')
    parts = text.split('\r\n', 1)[0].split(' ')
    return (int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0), text


class RuntimeAndGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runtime_mount_validation_rejects_dangerous_paths_and_artifact_ipc(self):
        ws = self.root / 'ws'
        store = self.root / 'store'
        ipc = self.root / 'ipc'
        for d in (ws, store, ipc):
            d.mkdir()

        # Rejeita caracteres de injeção no destino
        with self.assertRaises(RuntimeIsolationError):
            _validate_mounts((BindMount(ws, '/workspace,readonly=false', readonly=False),), profile='developer')

        # Rejeita montar $HOME
        with self.assertRaises(RuntimeIsolationError):
            _validate_mounts((BindMount(Path.home(), '/workspace', readonly=True),), profile='developer')

        # Rejeita montar /etc
        with self.assertRaises(RuntimeIsolationError):
            _validate_mounts((BindMount(Path('/etc'), '/host-etc', readonly=True),), profile='developer')

        # Rejeita montagem gravável sobre TrustStore
        with self.assertRaises(RuntimeIsolationError):
            _validate_mounts((BindMount(store, '/workspace', readonly=False),), profile='developer', forbidden_rw_roots=(store,))

        # Rejeita /ipc (inclusive variantes não normalizadas //ipc e /./ipc) no perfil artifact
        for forbidden_ipc in ('/ipc', '//ipc', '/./ipc', '/ipc/sub'):
            with self.assertRaises(RuntimeIsolationError):
                _validate_mounts((BindMount(ipc, forbidden_ipc, readonly=True),), profile='artifact')

        # Permite padrão E4 (/udh/agent-config rw + arquivos :ro) no perfil developer
        cfg_file = self.root / 'models.json'
        cfg_file.write_text('{}\n', encoding='utf-8')
        validated = _validate_mounts(
            (
                BindMount(ws, '/workspace', readonly=False),
                BindMount(ipc, '/udh/agent-config', readonly=False),
                BindMount(cfg_file, '/udh/agent-config/models.json', readonly=True),
            ),
            profile='developer',
        )
        self.assertEqual(len(validated), 3)

    def test_gateway_slowloris_concurrency_503_and_framing(self):
        audit = self.root / 'audit.jsonl'
        cfg = GatewayConfig(
            run_id='test-gw',
            policy_sha256=policy_v2_sha256(),
            audit_path=audit,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=False,
            max_concurrency=1,
            max_calls=3,
            header_timeout_seconds=0.8,
            body_timeout_seconds=1.0,
        )
        with InferenceGateway(cfg) as gw:
            assert gw.ipc_dir is not None and gw.socket_path is not None
            self.assertEqual(stat.S_IMODE(os.stat(gw.ipc_dir).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(gw.socket_path).st_mode), 0o600)
            self.assertLessEqual(len(str(gw.socket_path).encode('utf-8')), 100)

            # Slowloris 1 byte a cada 0.2s deve tomar 408 no deadline absoluto
            slow_status = None
            t0 = time.monotonic()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as slow:
                slow.settimeout(3.0)
                slow.connect(str(gw.socket_path))
                slow.sendall(b'POST /v1/chat/completions HTTP/1.1\r\n')
                # Ocupa o único slot para testar 503 concorrente ao mesmo tempo!
                time.sleep(0.05)
                s503, body503 = _raw_uds(gw.socket_path, b'POST /v1/chat/completions HTTP/1.1\r\n\r\n')
                self.assertEqual(s503, 503)
                self.assertIn('concurrency_limit_exceeded', body503)

                for _ in range(6):
                    try:
                        slow.sendall(b'A')
                    except OSError:
                        break
                    time.sleep(0.2)
                try:
                    resp = slow.recv(4096).decode('utf-8', errors='replace')
                    if resp.startswith('HTTP/1.1 '):
                        slow_status = int(resp.split(' ')[1])
                except OSError:
                    pass
            elapsed = time.monotonic() - t0
            self.assertEqual(slow_status, 408)
            self.assertLess(elapsed, 2.5)

            # Transfer-Encoding deve ser recusado com 400
            te_req = (
                f'POST /v1/chat/completions HTTP/1.1\r\n'
                f'Authorization: Bearer {gw.capability_token}\r\n'
                f'X-UDH-Purpose: inference\r\n'
                f'Transfer-Encoding: chunked\r\n\r\n'
            ).encode('utf-8')
            s_te, _ = _raw_uds(gw.socket_path, te_req)
            self.assertEqual(s_te, 400)
            time.sleep(0.05)

            # Requisição válida após liberação do slot funciona (prova release em finally)
            body = json.dumps({'model': DEFAULT_STUB_MODEL, 'stream': True, 'messages': []}).encode('utf-8')
            ok_req = (
                f'POST /v1/chat/completions HTTP/1.1\r\n'
                f'Authorization: Bearer {gw.capability_token}\r\n'
                f'X-UDH-Purpose: inference\r\n'
                f'Content-Length: {len(body)}\r\n\r\n'
            ).encode('utf-8') + body
            s_ok, text_ok = _raw_uds(gw.socket_path, ok_req)
            self.assertEqual(s_ok, 200)
            self.assertIn('UDH_CONFO', text_ok)
            self.assertIn('RMANCE_OK', text_ok)
            self.assertIn('data: [DONE]', text_ok)

    def test_openrouter_free_hardening_allowlist_pricing_and_sliding_secret_scan(self):
        # 1. Payload reconstruído por allowlist descarta provider/route/models do contêiner
        rebuilt = build_openrouter_payload(
            {
                'model': 'acme/test:free',
                'stream': True,
                'messages': [{'role': 'user', 'content': 'oi'}],
                'max_tokens': 999999,
                'provider': {'max_price': {'prompt': 999, 'completion': 999}},
                'route': 'fallback',
                'models': ['paid/model'],
                'tools': [{'type': 'function'}],
            },
            allowed_model='acme/test:free',
            allow_tools=False,
        )
        self.assertEqual(rebuilt['provider'], {'max_price': {'prompt': 0, 'completion': 0}})
        self.assertNotIn('route', rebuilt)
        self.assertNotIn('models', rebuilt)
        self.assertNotIn('tools', rebuilt)
        self.assertLessEqual(rebuilt['max_tokens'], 1024)

        # 2. Catálogo exige :free e todos os campos de pricing == 0 (inclusive request/image)
        free_catalog = json.dumps({
            'data': [
                {'id': 'acme/free-ok:free', 'pricing': {'prompt': '0', 'completion': '0', 'request': '0'}},
                {'id': 'acme/hidden-fee:free', 'pricing': {'prompt': '0', 'completion': '0', 'request': '0.01'}},
                {'id': 'acme/paid-model', 'pricing': {'prompt': '0', 'completion': '0'}},
            ]
        }).encode('utf-8')
        self.assertTrue(verify_openrouter_free_model(
            'acme/free-ok:free',
            connection_factory=lambda: _FakeHTTPSConnection([_FakeHTTPResponse(200, free_catalog)]),
        ))
        self.assertFalse(verify_openrouter_free_model(
            'acme/hidden-fee:free',
            connection_factory=lambda: _FakeHTTPSConnection([_FakeHTTPResponse(200, free_catalog)]),
        ))
        self.assertFalse(verify_openrouter_free_model(
            'acme/paid-model',
            connection_factory=lambda: _FakeHTTPSConnection([_FakeHTTPResponse(200, free_catalog)]),
        ))
        # Redirect 302 recusado
        self.assertFalse(verify_openrouter_free_model(
            'acme/free-ok:free',
            connection_factory=lambda: _FakeHTTPSConnection([_FakeHTTPResponse(302, b'')]),
        ))

        # 3. Janela deslizante detecta segredo fatiado entre chunks SSE
        scanner = SecretWindowScanner('SUPER_SECRET_KEY_12345')
        self.assertFalse(scanner.contains_secret(b'data: {"delta":"hello SUPER_SEC'))
        self.assertTrue(scanner.contains_secret(b'RET_KEY_12345 world"}\n'))

        # 4. Gateway openrouter_free bloqueia resposta com segredo fatiado e registra auditoria sem segredo
        secret = 'SK_OR_SYNTHETIC_SECRET_9999'
        audit = self.root / 'or-audit.jsonl'
        sse_leak = (
            b'data: {"choices":[{"delta":{"content":"part1 SK_OR_SYNTH"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"ETIC_SECRET_9999"}}]}\n'
            b'data: [DONE]\n'
        )
        responses = [
            _FakeHTTPResponse(200, free_catalog),
            _FakeHTTPResponse(200, sse_leak, {'Content-Type': 'text/event-stream'}),
        ]
        cfg = GatewayConfig(
            run_id='test-or',
            policy_sha256=policy_v2_sha256(),
            audit_path=audit,
            backend='openrouter_free',
            allowed_models=('acme/free-ok:free',),
            secret_resolver=lambda: secret,
            openrouter_connection_factory=lambda: _FakeHTTPSConnection([responses.pop(0)]),
        )
        with InferenceGateway(cfg) as gw:
            assert gw.socket_path is not None
            body = json.dumps({'model': 'acme/free-ok:free', 'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]}).encode('utf-8')
            req = (
                f'POST /v1/chat/completions HTTP/1.1\r\n'
                f'Authorization: Bearer {gw.capability_token}\r\n'
                f'X-UDH-Purpose: inference\r\n'
                f'Content-Length: {len(body)}\r\n\r\n'
            ).encode('utf-8') + body
            _status, text = _raw_uds(gw.socket_path, req)
            self.assertNotIn(secret, text)

        audit_text = audit.read_text(encoding='utf-8')
        self.assertIn('secret_in_response_blocked', audit_text)
        self.assertNotIn(secret, audit_text)

    def test_pi_jsonl_parser_strict_stop_reason_and_error_detection(self):
        ok_stream = (
            '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"UDH_CONFORMANCE_OK"}],"stopReason":"stop"}}\n'
            '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}\n'
        )
        parsed_ok = parse_pi_jsonl(ok_stream)
        self.assertEqual(parsed_ok['last_stop_reason'], 'stop')
        self.assertFalse(parsed_ok['has_error_event'])
        self.assertEqual(parsed_ok['assistant_text'], 'UDH_CONFORMANCE_OK')

        # Mesmo com exit 0, stopReason=error deve ser detectado
        err_stream = (
            '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"PARCIAL"}],"stopReason":"error","errorMessage":"socket closed"}}\n'
        )
        parsed_err = parse_pi_jsonl(err_stream)
        self.assertTrue(parsed_err['has_error_event'])
        self.assertEqual(parsed_err['last_stop_reason'], 'error')

        # stopReason=length não é tratado como stop pleno
        len_stream = (
            '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"cortado"}],"stopReason":"length"}}\n'
        )
        parsed_len = parse_pi_jsonl(len_stream)
        self.assertEqual(parsed_len['last_stop_reason'], 'length')

    def test_gateway_peer_denied_body_timeout_bare_lf_and_nonzero_cost_variants(self):
        from unittest.mock import patch

        audit = self.root / 'extra-audit.jsonl'
        cfg = GatewayConfig(
            run_id='test-extra-gw',
            policy_sha256=policy_v2_sha256(),
            audit_path=audit,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            header_timeout_seconds=1.0,
            body_timeout_seconds=0.6,
        )
        with InferenceGateway(cfg) as gw:
            assert gw.socket_path is not None
            body = json.dumps({'model': DEFAULT_STUB_MODEL, 'stream': True, 'messages': []}).encode('utf-8')

            # 1. SO_PEERCRED com UID diferente -> 403 peer_denied
            with patch('udh.gateway.os.getuid', return_value=os.getuid() + 1):
                req = (
                    f'POST /v1/chat/completions HTTP/1.1\r\n'
                    f'Authorization: Bearer {gw.capability_token}\r\n'
                    f'X-UDH-Purpose: inference\r\n'
                    f'Content-Length: {len(body)}\r\n\r\n'
                ).encode('utf-8') + body
                s_peer, text_peer = _raw_uds(gw.socket_path, req)
                self.assertEqual(s_peer, 403)
                self.assertIn('peer_denied', text_peer)

            # 2. LF nu dentro dos cabeçalhos -> 400 malformed_header
            bare_lf = (
                f'POST /v1/chat/completions HTTP/1.1\r\n'
                f'Authorization: Bearer {gw.capability_token}\nX-Injected: 1\r\n'
                f'X-UDH-Purpose: inference\r\n'
                f'Content-Length: 2\r\n\r\n{{}}'
            ).encode('utf-8')
            s_lf, text_lf = _raw_uds(gw.socket_path, bare_lf)
            self.assertEqual(s_lf, 400)
            self.assertIn('malformed_header', text_lf)

            # 3. request_body_timeout (cabeçalhos completos, corpo lento excede body_deadline -> 408)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
                cli.settimeout(4.0)
                cli.connect(str(gw.socket_path))
                head = (
                    f'POST /v1/chat/completions HTTP/1.1\r\n'
                    f'Authorization: Bearer {gw.capability_token}\r\n'
                    f'X-UDH-Purpose: inference\r\n'
                    f'Content-Length: 100\r\n\r\n'
                ).encode('utf-8')
                cli.sendall(head + b'{')
                time.sleep(1.8)
                resp = cli.recv(4096).decode('utf-8', errors='replace')
                self.assertIn('408', resp)
                self.assertIn('request_body_timeout', resp)

        # 4. nonzero_cost_blocked em openrouter_free tanto para float quanto para string!
        free_catalog = json.dumps({
            'data': [{'id': 'acme/free-ok:free', 'pricing': {'prompt': '0', 'completion': '0'}}]
        }).encode('utf-8')
        for bad_cost_json in ('0.05', '"0.05"'):
            or_audit = self.root / f'cost-{bad_cost_json.strip(chr(34))}.jsonl'
            sse = (
                f'data: {{"choices":[{{"delta":{{"content":"ok"}}}}],"usage":{{"cost":{bad_cost_json}}}}}\n'
                f'data: [DONE]\n'
            ).encode('utf-8')
            resps = [
                _FakeHTTPResponse(200, free_catalog),
                _FakeHTTPResponse(200, sse, {'Content-Type': 'text/event-stream'}),
            ]
            or_cfg = GatewayConfig(
                run_id='test-cost',
                policy_sha256=policy_v2_sha256(),
                audit_path=or_audit,
                backend='openrouter_free',
                allowed_models=('acme/free-ok:free',),
                secret_resolver=lambda: 'SK_SYNTHETIC_COST_TEST_12345',
                openrouter_connection_factory=lambda: _FakeHTTPSConnection([resps.pop(0)]),
            )
            with InferenceGateway(or_cfg) as gw:
                assert gw.socket_path is not None
                req_body = json.dumps({'model': 'acme/free-ok:free', 'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]}).encode('utf-8')
                req = (
                    f'POST /v1/chat/completions HTTP/1.1\r\n'
                    f'Authorization: Bearer {gw.capability_token}\r\n'
                    f'X-UDH-Purpose: inference\r\n'
                    f'Content-Length: {len(req_body)}\r\n\r\n'
                ).encode('utf-8') + req_body
                _raw_uds(gw.socket_path, req)
            self.assertIn('nonzero_cost_blocked', or_audit.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
