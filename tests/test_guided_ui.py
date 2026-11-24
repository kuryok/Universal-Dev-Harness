"""Testes da interface web guiada local (B1.7), UX-01, UX-03, ADR-025 e fluxo completo com preview B3.6."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import unittest
from urllib.parse import urlsplit

from tests.test_artifact_preview import (
    _podman_ready_for_artifact,
    trusted_notes_smoke_checks,
    write_sample_at_suite,
    write_sample_web_app,
)
from udh.domain import CoreError
from udh.runtime import RuntimeManager
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.trust_store import TrustStore
from udh.web import GuidedWebServer
from udh.web_ui import APP_CSS, APP_JS, INDEX_HTML


def _raw_http(port: int, raw_request: bytes) -> tuple[int, dict[str, list[str]], bytes]:
    with socket.create_connection(('127.0.0.1', port), timeout=5.0) as sock:
        sock.settimeout(5.0)
        sock.sendall(raw_request)
        chunks = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            chunks.append(piece)
    raw = b''.join(chunks)
    head, _, body = raw.partition(b'\r\n\r\n')
    lines = head.decode('latin-1').split('\r\n')
    status = int(lines[0].split(' ', 2)[1])
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            headers.setdefault(k.strip().lower(), []).append(v.strip())
    return status, headers, body


def _http_req(
    port: int,
    method: str,
    path: str,
    *,
    host: str | None = None,
    headers: dict[str, str] | None = None,
    body: bytes = b'',
) -> tuple[int, dict[str, list[str]], bytes]:
    hdrs = dict(headers or {})
    hdrs.setdefault('Host', host or f'127.0.0.1:{port}')
    hdrs['Connection'] = 'close'
    if body or method in ('POST', 'PUT', 'PATCH'):
        hdrs['Content-Length'] = str(len(body))
    req = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in hdrs.items()) + '\r\n'
    return _raw_http(port, req.encode('latin-1') + body)


class GuidedUISecurityAndUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='udh-c4-ui-')
        self.root = Path(self.tmp.name)
        self.db_path = self.root / 'state.db'
        self.trust = TrustStore(self.root / 'trust_store')
        self.core = CoreService(SQLiteStore(self.db_path))
        self.core.initialize(schema_version=4)
        self.token = 'test-session-token-0123456789abcdef'
        self.fake_probes = {
            'system': 'Linux',
            'machine': 'x86_64',
            'release': '6.12.0',
            'is_wsl2': False,
            'cgroup_v2_controllers': ['cpu', 'memory', 'pids'],
            'unprivileged_userns_clone': '1',
            'uid': 1000,
            'username': 'tester',
            'has_subuid': True,
            'has_subgid': True,
            'podman': {'available': True, 'binary': '/usr/bin/podman', 'rootless': True, 'cgroup_version': 'v2', 'podman_version': '5.4.2'},
        }
        self.server = GuidedWebServer(
            core_service=self.core,
            trust_store=self.trust,
            session_token=self.token,
            doctor_env_probes=self.fake_probes,
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaises(CoreError):
            GuidedWebServer(core_service=self.core, trust_store=self.trust, host='0.0.0.0')

    def test_static_assets_offline_no_innerhtml_no_cdn_no_token_leak(self):
        self.assertNotIn('innerHTML', APP_JS)
        self.assertNotIn('outerHTML', APP_JS)
        self.assertNotIn('insertAdjacentHTML', APP_JS)
        self.assertNotIn('http://', INDEX_HTML)
        self.assertNotIn('https://', INDEX_HTML)
        self.assertNotIn('https://', APP_CSS)

        for path, expected_ct in (
            ('/', 'text/html'),
            ('/assets/app.css', 'text/css'),
            ('/assets/app.js', 'application/javascript'),
        ):
            status, headers, body = _http_req(self.server.port, 'GET', path)
            self.assertEqual(status, 200)
            self.assertIn(expected_ct, headers['content-type'][0])
            self.assertNotIn('set-cookie', headers)
            self.assertNotIn('access-control-allow-origin', headers)
            self.assertEqual(headers.get('cross-origin-resource-policy'), ['same-origin'])
            self.assertIn("frame-ancestors 'none'", headers['content-security-policy'][0])
            self.assertNotIn(self.token.encode('utf-8'), body)

    def test_dns_rebinding_and_duplicate_headers_blocked(self):
        port = self.server.port
        for bad_host in (f'evil.example:{port}', f'localhost:{port}', '127.0.0.1:65530', f'p-{"a"*24}.localhost:{port}'):
            status, _h, _b = _http_req(port, 'GET', '/', host=bad_host)
            self.assertEqual(status, 421, f'Host inválido deveria retornar 421: {bad_host}')

        # Host ausente -> 421
        st_no_host, _, _ = _raw_http(port, b'GET / HTTP/1.1\r\nConnection: close\r\n\r\n')
        self.assertEqual(st_no_host, 421)

        # Host duplicado -> 421
        st_dup_host, _, _ = _raw_http(
            port,
            f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nHost: evil.example:{port}\r\nConnection: close\r\n\r\n'.encode(),
        )
        self.assertEqual(st_dup_host, 421)

        # X-UDH-Token duplicado -> 400
        st_dup_tok, _, _ = _raw_http(
            port,
            (
                f'GET /api/overview HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                f'X-UDH-Token: {self.token}\r\nX-UDH-Token: {self.token}\r\nConnection: close\r\n\r\n'
            ).encode(),
        )
        self.assertEqual(st_dup_tok, 400)

        # Transfer-Encoding rejeitado -> 400
        st_te, _, _ = _raw_http(
            port,
            (
                f'POST /api/runs HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                f'X-UDH-Token: {self.token}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n0\r\n\r\n'
            ).encode(),
        )
        self.assertEqual(st_te, 400)

    def test_origin_and_sec_fetch_site_csrf_blocked_on_all_ui_routes(self):
        port = self.server.port
        preview_origin = f'http://p-{"b"*24}.localhost:{port}'
        for bad_origin in ('http://evil.example', 'null', preview_origin):
            for method, path in (
                ('GET', '/'),
                ('GET', '/assets/app.js'),
                ('GET', '/api/overview'),
                ('POST', '/api/runs'),
                ('OPTIONS', '/api/runs'),
            ):
                status, headers, _ = _http_req(
                    port, method, path,
                    headers={'Origin': bad_origin, 'X-UDH-Token': self.token},
                    body=b'{}' if method == 'POST' else b'',
                )
                self.assertEqual(status, 403, f'{method} {path} com Origin={bad_origin} deveria retornar 403')
                self.assertNotIn('access-control-allow-origin', headers)

        # Sec-Fetch-Site cross-site bloqueado em GET e POST /api/*
        st_get, _, _ = _http_req(
            port, 'GET', '/api/overview',
            headers={'X-UDH-Token': self.token, 'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertEqual(st_get, 403)

        st_post_none, _, _ = _http_req(
            port, 'POST', '/api/runs',
            headers={'X-UDH-Token': self.token, 'Sec-Fetch-Site': 'none'},
            body=b'{}',
        )
        self.assertEqual(st_post_none, 403)

    def test_token_required_on_get_and_post_api_never_cookie(self):
        port = self.server.port
        # Sem token em GET e POST -> 401
        st1, _, _ = _http_req(port, 'GET', '/api/overview')
        self.assertEqual(st1, 401)
        st1_post, _, _ = _http_req(port, 'POST', '/api/runs', body=b'{}')
        self.assertEqual(st1_post, 401)

        # Cookie tentando autenticar -> 401 (UI não aceita cookie!)
        st2, _, _ = _http_req(port, 'GET', '/api/overview', headers={'Cookie': f'session={self.token}'})
        self.assertEqual(st2, 401)

        # Token errado -> 403
        st3, _, _ = _http_req(port, 'GET', '/api/overview', headers={'X-UDH-Token': 'wrong-token-123456'})
        self.assertEqual(st3, 403)

        # Token correto em cabeçalho -> 200 e zero Set-Cookie
        st4, headers4, body4 = _http_req(port, 'GET', '/api/overview', headers={'X-UDH-Token': self.token})
        self.assertEqual(st4, 200)
        self.assertNotIn('set-cookie', headers4)
        payload = json.loads(body4.decode('utf-8'))
        self.assertIn('health', payload)
        self.assertIn('counts', payload)

    def test_ux01_real_counts_without_truncation_and_adr025_block_explanations(self):
        port = self.server.port
        hdr = {'X-UDH-Token': self.token, 'Origin': f'http://127.0.0.1:{port}', 'Sec-Fetch-Site': 'same-origin'}

        r1 = self.core.create('proj-a', '1.0', 'req-c1', mode='FAST')
        r2 = self.core.create('proj-b', '1.0', 'req-c2', mode='STANDARD')
        r3 = self.core.create('proj-c', '1.0', 'req-c3', mode='DEEP')
        r4 = self.core.create('proj-d', '1.0', 'req-c4', mode='STANDARD')
        self.core.start(r2.run_id, 0, 'req-s2')
        self.core.block_v4(
            r3.run_id, 0, 'req-b3',
            category='POLICY_VIOLATION',
            reason_code='EGRESS_DENIED',
            reason='Tentativa de acesso externo bloqueada pela política.',
            next_action='Remova a dependência externa ou use dublê local.',
        )
        self.core.block_v4(
            r4.run_id, 0, 'req-b4',
            category='FUNCTIONAL_FAILURE',
            reason_code='SMOKE_CHECK_FAILED',
            reason='Rota principal retornou erro 500.',
            next_action='Corrija a rota principal e rode o smoke novamente.',
        )

        # list_runs com limit menor que total não trunca count_runs_by_state
        self.assertEqual(len(self.core.list_runs(limit=2)), 2)
        self.assertEqual(self.core.count_runs_by_state(), {'PENDING': 1, 'IN_PROGRESS': 1, 'BLOCKED': 2, 'DONE': 0})

        st_ov, _, body_ov = _http_req(port, 'GET', '/api/overview', headers=hdr)
        self.assertEqual(st_ov, 200)
        ov = json.loads(body_ov.decode('utf-8'))
        self.assertEqual(ov['counts'], {'PENDING': 1, 'IN_PROGRESS': 1, 'BLOCKED': 2, 'DONE': 0})
        self.assertEqual(len(ov['runs']), 4)

        # Detalhe da execução bloqueada exibe reason_code real, os 4 campos obrigatórios ADR-025 e allows_bypass=False
        st_det, _, body_det = _http_req(port, 'GET', f'/api/runs/{r3.run_id}', headers=hdr)
        self.assertEqual(st_det, 200)
        det = json.loads(body_det.decode('utf-8'))
        blk = det['block_explanation']
        self.assertEqual(blk['category'], 'POLICY_VIOLATION')
        self.assertEqual(blk['reason_code'], 'EGRESS_DENIED')
        self.assertIn('EGRESS_DENIED', blk['what_was_verified'])
        self.assertFalse(blk['allows_bypass'])
        for field in ('what_happened', 'why_it_matters', 'what_was_verified', 'safe_next_action'):
            self.assertTrue(blk[field].strip())

        st_det4, _, body_det4 = _http_req(port, 'GET', f'/api/runs/{r4.run_id}', headers=hdr)
        self.assertEqual(st_det4, 200)
        blk4 = json.loads(body_det4.decode('utf-8'))['block_explanation']
        self.assertEqual(blk4['category'], 'FUNCTIONAL_FAILURE')
        self.assertEqual(blk4['reason_code'], 'SMOKE_CHECK_FAILED')
        self.assertFalse(blk4['allows_bypass'])

        # Desbloqueio retorna para PENDING (nunca DONE/bypass)
        st_unb, _, _ = _http_req(
            port, 'POST', f'/api/runs/{r3.run_id}/unblock',
            headers=hdr,
            body=json.dumps({'expected_revision': 1, 'request_id': 'req-unb-3', 'reason': 'Ajuste revisado.'}).encode(),
        )
        self.assertEqual(st_unb, 200)
        self.assertEqual(self.core.show(r3.run_id).state, 'PENDING')


@unittest.skipUnless(_podman_ready_for_artifact(), 'Podman rootless ou imagem artifact-only indisponível')
class GuidedUIEndToEndPodmanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='udh-c4-e2e-')
        self.root = Path(self.tmp.name)
        self.db_path = self.root / 'state.db'
        self.trust = TrustStore(self.root / 'trust_store')
        self.core = CoreService(SQLiteStore(self.db_path))
        self.core.initialize(schema_version=4)

        self.app_src = self.root / 'app_src'
        self.at_src = self.root / 'at_src'
        write_sample_web_app(self.app_src, include_hostile_smoke=True)
        write_sample_at_suite(self.at_src)
        self.at_manifest = self.trust.seal_at_suite(self.at_src, scenarios=('app-notas-ui',))
        self.token = 'e2e-session-token-abcdef0123456789'

        self.server = GuidedWebServer(
            core_service=self.core,
            trust_store=self.trust,
            runtime_manager=RuntimeManager(),
            session_token=self.token,
            default_at_version=self.at_manifest['at_version'],
            trusted_smoke_checks=trusted_notes_smoke_checks(),
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def test_full_guided_journey_verify_smoke_preview_isolation_and_deliver(self):
        port = self.server.port
        ui_hdr = {
            'X-UDH-Token': self.token,
            'Origin': f'http://127.0.0.1:{port}',
            'Sec-Fetch-Site': 'same-origin',
            'Content-Type': 'application/json',
        }

        # 1. Criar e iniciar execução pela UI
        st_cr, _, b_cr = _http_req(
            port, 'POST', '/api/runs', headers=ui_hdr,
            body=json.dumps({
                'spec_id': 'app-notas', 'spec_version': '1.0', 'request_id': 'e2e-create',
                'ticket_id': 'entrega-1', 'mode': 'STANDARD',
            }).encode(),
        )
        self.assertEqual(st_cr, 201)
        run_id = json.loads(b_cr.decode())['run']['run_id']

        st_st, _, _ = _http_req(
            port, 'POST', f'/api/runs/{run_id}/start', headers=ui_hdr,
            body=json.dumps({'expected_revision': 0, 'request_id': 'e2e-start'}).encode(),
        )
        self.assertEqual(st_st, 200)

        # 2. Promover artefato oficialmente (Core)
        self.core.promote_run_artifact(run_id, 'e2e-promo', self.app_src, trust_store=self.trust)

        # 3. Verificar AT oficial (imagem artifact-only)
        st_ver, _, b_ver = _http_req(
            port, 'POST', f'/api/runs/{run_id}/verify', headers=ui_hdr,
            body=json.dumps({'request_id': 'e2e-verify'}).encode(),
        )
        self.assertEqual(st_ver, 200, b_ver.decode())
        self.assertEqual(json.loads(b_ver.decode())['report']['verdict'], 'PASS')

        # Tentar entregar sem smoke -> 409
        st_del_early, _, _ = _http_req(
            port, 'POST', f'/api/runs/{run_id}/deliver', headers=ui_hdr,
            body=json.dumps({'expected_revision': 1, 'request_id': 'e2e-del-early'}).encode(),
        )
        self.assertEqual(st_del_early, 409)

        # 4. Smoke test oficial B3.4 (com reinício real)
        st_smk, _, b_smk = _http_req(
            port, 'POST', f'/api/runs/{run_id}/smoke', headers=ui_hdr,
            body=json.dumps({'request_id': 'e2e-smoke'}).encode(),
        )
        self.assertEqual(st_smk, 200, b_smk.decode())
        smk_report = json.loads(b_smk.decode())['report']
        self.assertEqual(smk_report['verdict'], 'PASS')
        self.assertTrue(smk_report['restart_verified'])

        # 5. Botão "Ver o app": inicia preview isolado em subdomínio p-<id>.localhost
        st_prev, _, b_prev = _http_req(
            port, 'POST', f'/api/runs/{run_id}/preview/start', headers=ui_hdr,
            body=json.dumps({'embed_mode': 'tab'}).encode(),
        )
        self.assertEqual(st_prev, 200, b_prev.decode())
        prev_data = json.loads(b_prev.decode())
        pid = prev_data['preview_id']
        preview_host = f'{prev_data["hostname"]}:{port}'
        parsed_launch = urlsplit(prev_data['launch_url'])
        auth_path = f'{parsed_launch.path}?{parsed_launch.query}'

        # 5a. Acesso ao preview sem cookie -> 403
        st_unauth, _, _ = _http_req(port, 'GET', '/', host=preview_host)
        self.assertEqual(st_unauth, 403)

        # 5b. Troca do grant one-time -> 303 + cookie host-only (SEM Domain=), HttpOnly, SameSite=Lax
        st_auth, h_auth, _ = _http_req(port, 'GET', auth_path, host=preview_host)
        self.assertEqual(st_auth, 303)
        self.assertEqual(h_auth['location'], ['/'])
        set_cookie = h_auth['set-cookie'][0]
        self.assertIn('_udh_preview_session=', set_cookie)
        self.assertIn('HttpOnly', set_cookie)
        self.assertIn('SameSite=Lax', set_cookie)
        self.assertNotIn('Domain=', set_cookie)
        self.assertNotIn('domain=', set_cookie.lower())
        preview_cookie = set_cookie.split(';', 1)[0]

        # 5c. Reuso do mesmo grant -> 403
        st_reuse, _, _ = _http_req(port, 'GET', auth_path, host=preview_host)
        self.assertEqual(st_reuse, 403)

        # 5d. Requisição autenticada no preview -> 200 com CSP restritiva
        st_home, h_home, b_home = _http_req(
            port, 'GET', '/', host=preview_host, headers={'Cookie': preview_cookie},
        )
        self.assertEqual(st_home, 200)
        self.assertIn(b'App Notas', b_home)
        self.assertIn("frame-ancestors 'none'", h_home['content-security-policy'][0])
        self.assertIn("style-src 'self'", h_home['content-security-policy'][0])

        # 5e. X-UDH-Token e valor do token em outro cabeçalho (ex. Referer) rejeitados no host de preview (B1.7(d))
        st_leak, _, _ = _http_req(
            port, 'GET', '/', host=preview_host,
            headers={'Cookie': preview_cookie, 'X-UDH-Token': self.token},
        )
        self.assertEqual(st_leak, 403)
        st_leak_ref, _, _ = _http_req(
            port, 'GET', '/', host=preview_host,
            headers={'Cookie': preview_cookie, 'Referer': f'http://127.0.0.1:{port}/?x={self.token}'},
        )
        self.assertEqual(st_leak_ref, 403)

        # 5e2. Preview B em modo iframe: cookie do preview A é rejeitado no preview B (403) e CSP libera ui_origin
        st_prev_b, _, b_prev_b = _http_req(
            port, 'POST', f'/api/runs/{run_id}/preview/start', headers=ui_hdr,
            body=json.dumps({'embed_mode': 'iframe'}).encode(),
        )
        self.assertEqual(st_prev_b, 200, b_prev_b.decode())
        prev_b = json.loads(b_prev_b.decode())
        pid_b = prev_b['preview_id']
        preview_host_b = f'{prev_b["hostname"]}:{port}'
        parsed_b = urlsplit(prev_b['launch_url'])
        # Cookie do preview A não funciona no preview B
        st_cross_ck, _, _ = _http_req(
            port, 'GET', '/', host=preview_host_b, headers={'Cookie': preview_cookie},
        )
        self.assertEqual(st_cross_ck, 403)
        # /_udh_preview_auth e resposta em modo iframe liberam frame-ancestors http://127.0.0.1:<port>
        st_auth_b, h_auth_b, _ = _http_req(port, 'GET', f'{parsed_b.path}?{parsed_b.query}', host=preview_host_b)
        self.assertEqual(st_auth_b, 303)
        self.assertIn(f'frame-ancestors http://127.0.0.1:{port}', h_auth_b['content-security-policy'][0])
        cookie_b = h_auth_b['set-cookie'][0].split(';', 1)[0]
        st_home_b, h_home_b, _ = _http_req(port, 'GET', '/', host=preview_host_b, headers={'Cookie': cookie_b})
        self.assertEqual(st_home_b, 200)
        self.assertIn(f'frame-ancestors http://127.0.0.1:{port}', h_home_b['content-security-policy'][0])
        _http_req(port, 'POST', f'/api/previews/{pid_b}/stop', headers=ui_hdr, body=b'{}')

        # 5f. CSRF em mutação no preview bloqueado; same-origin no preview aceito
        st_prev_csrf, _, _ = _http_req(
            port, 'POST', '/api/notes', host=preview_host,
            headers={'Cookie': preview_cookie, 'Origin': 'http://evil.example', 'Content-Type': 'application/json'},
            body=b'{"text":"csrf"}',
        )
        self.assertEqual(st_prev_csrf, 403)

        st_prev_post, _, b_prev_post = _http_req(
            port, 'POST', '/api/notes', host=preview_host,
            headers={
                'Cookie': preview_cookie,
                'Origin': f'http://{preview_host}',
                'Sec-Fetch-Site': 'same-origin',
                'Content-Type': 'application/json',
            },
            body=b'{"text":"nota-demo"}',
        )
        self.assertEqual(st_prev_post, 201)
        self.assertIn(b'nota-demo', b_prev_post)

        # 5g. Novo grant one-time via POST /api/previews/<pid>/grant (fallback iframe -> aba)
        st_grant2, _, b_grant2 = _http_req(port, 'POST', f'/api/previews/{pid}/grant', headers=ui_hdr, body=b'{}')
        self.assertEqual(st_grant2, 200)
        g2_parsed = urlsplit(json.loads(b_grant2.decode())['launch_url'])
        st_auth2, _, _ = _http_req(port, 'GET', f'{g2_parsed.path}?{g2_parsed.query}', host=preview_host)
        self.assertEqual(st_auth2, 303)
        st_auth2_reuse, _, _ = _http_req(port, 'GET', f'{g2_parsed.path}?{g2_parsed.query}', host=preview_host)
        self.assertEqual(st_auth2_reuse, 403)

        # 5h. Encerrar preview -> contêiner e volume descartados; host retorna 421
        st_stop, _, b_stop = _http_req(port, 'POST', f'/api/previews/{pid}/stop', headers=ui_hdr, body=b'{}')
        self.assertEqual(st_stop, 200)
        self.assertTrue(json.loads(b_stop.decode())['cleanup_verified'])
        st_after_stop, _, _ = _http_req(port, 'GET', '/', host=preview_host, headers={'Cookie': preview_cookie})
        self.assertEqual(st_after_stop, 421)

        # 6. Concluir entrega -> DELIVERED
        st_del, _, b_del = _http_req(
            port, 'POST', f'/api/runs/{run_id}/deliver', headers=ui_hdr,
            body=json.dumps({'expected_revision': 1, 'request_id': 'e2e-deliver'}).encode(),
        )
        self.assertEqual(st_del, 200, b_del.decode())
        del_payload = json.loads(b_del.decode())
        self.assertEqual(del_payload['delivery']['delivery_stage'], 'DELIVERED')
        self.assertEqual(del_payload['delivery']['run_state'], 'DONE')


if __name__ == '__main__':
    unittest.main()
