"""Testes do perfil artifact-only (B3.1), transporte UDS/proxy (B3.6), smoke (B3.4) e entrega (ADR-028)."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from udh.domain import CoreError
from udh.executor import TrustedExecutor
from udh.preview import (
    DEMO_DATA_LABEL,
    MAX_UDS_PATH_BYTES,
    PreviewError,
    PreviewManager,
    UdsHttpResponse,
    allocate_preview_ipc_dir,
    connect_preview_uds,
    harden_preview_response,
    load_app_descriptor,
)
from udh.runtime import (
    BindMount,
    DEFAULT_ARTIFACT_MANIFEST_PATH,
    RuntimeIsolationError,
    RuntimeManager,
    load_artifact_manifest,
)
from udh.service import CoreService
from udh.smoke import SmokeCheck, SmokeRunner, parse_trusted_smoke_checks
from udh.store import SQLiteStore
from udh.transitions import ConflictError
from udh.trust_store import TrustStore

NOTES_APP_PY = """import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ['UDH_DATA_DIR'])
DATA_FILE = DATA_DIR / 'notes.json'


def load_notes():
    if not DATA_FILE.is_file():
        return []
    return json.loads(DATA_FILE.read_text(encoding='utf-8'))


def save_notes(items):
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        return

    def _send(self, status, body_bytes, content_type='text/plain; charset=utf-8', extra=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body_bytes)))
        self.send_header('Connection', 'close')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path == '/health':
            self._send(200, b'OK')
            return
        if self.path == '/':
            html = b'<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head><body><h1>App Notas</h1><script src="/app.js"></script></body></html>'
            self._send(200, html, 'text/html; charset=utf-8')
            return
        if self.path == '/app.css':
            self._send(200, b'body { font-family: sans-serif; }', 'text/css; charset=utf-8')
            return
        if self.path == '/app.js':
            self._send(200, b'window.__notesAppLoaded = true;', 'application/javascript; charset=utf-8')
            return
        if self.path == '/api/notes':
            raw = json.dumps({'notes': load_notes()}, ensure_ascii=False).encode('utf-8')
            self._send(200, raw, 'application/json; charset=utf-8')
            return
        if self.path == '/cookie-echo':
            raw = json.dumps({'cookie': self.headers.get('Cookie', ''), 'auth': self.headers.get('Authorization', ''), 'token': self.headers.get('X-UDH-Token', '')}).encode('utf-8')
            self._send(200, raw, 'application/json; charset=utf-8')
            return
        if self.path == '/hostile-headers':
            self._send(200, b'ok', extra=(
                ('Refresh', '0;url=https://evil.example/'),
                ('Set-Cookie', '_udh_preview_session=hijack; Path=/'),
                ('Set-Cookie', 'app_pref=dark; Domain=.localhost; Path=/'),
                ('Set-Cookie2', 'old=1'),
                ('Content-Security-Policy', "default-src * 'unsafe-inline'"),
            ))
            return
        if self.path == '/bad-redirect':
            self._send(302, b'', extra=(('Location', 'https://evil.example/phish'),))
            return
        self._send(404, b'not found')

    def do_POST(self):
        if self.path == '/api/notes':
            length = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            items = load_notes()
            items.append(str(payload.get('text', '')))
            save_notes(items)
            raw = json.dumps({'notes': items}, ensure_ascii=False).encode('utf-8')
            self._send(201, raw, 'application/json; charset=utf-8')
            return
        self._send(404, b'not found')


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    HTTPServer((host, port), Handler).serve_forever()
"""

NOTES_AT_PY = """import json
from pathlib import Path
import unittest


class NotesAppAcceptanceTest(unittest.TestCase):
    def test_descriptor_and_app_present(self):
        art = Path('/artifact')
        desc = json.loads((art / 'udh_app.json').read_text(encoding='utf-8'))
        self.assertEqual(desc['app_type'], 'web')
        self.assertEqual(desc['entrypoint'], 'app.py')
        self.assertTrue((art / 'app.py').is_file())
        self.assertTrue((art / 'README.txt').is_file())
"""


def write_sample_web_app(target_dir: Path, *, include_hostile_smoke: bool = True) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / 'app.py').write_text(NOTES_APP_PY, encoding='utf-8')
    (target_dir / 'README.txt').write_text('App Notas Python stdlib.\n', encoding='utf-8')
    descriptor = {
        'schema_version': 1,
        'app_type': 'web',
        'entrypoint': 'app.py',
        'ready_path': '/health',
        'docs_files': ['README.txt'],
    }
    if include_hostile_smoke:
        # ADR-021: smoke_checks embutido no artefato deve ser ignorado pelo SmokeRunner
        descriptor['smoke_checks'] = []
    (target_dir / 'udh_app.json').write_text(json.dumps(descriptor, indent=2), encoding='utf-8')


def write_sample_at_suite(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / 'test_notes_app.py').write_text(NOTES_AT_PY, encoding='utf-8')


def trusted_notes_smoke_checks() -> tuple[SmokeCheck, ...]:
    return (
        SmokeCheck(name='home', method='GET', path='/', expected_status=200, expected_substring='App Notas'),
        SmokeCheck(
            name='create_note',
            method='POST',
            path='/api/notes',
            body='{"text":"persistida-b34"}',
            headers=(('Content-Type', 'application/json'),),
            expected_status=201,
            expected_substring='persistida-b34',
        ),
        SmokeCheck(
            name='verify_after_restart',
            method='GET',
            path='/api/notes',
            expected_status=200,
            expected_substring='persistida-b34',
            after_restart=True,
        ),
    )


def _podman_ready_for_artifact() -> bool:
    if not DEFAULT_ARTIFACT_MANIFEST_PATH.is_file():
        return False
    try:
        rm = RuntimeManager()
        manifest = rm.load_artifact_manifest()
        rm.verify_image_present(manifest.image_ref)
        return True
    except (RuntimeIsolationError, OSError):
        return False


class ArtifactPreviewUnitTests(unittest.TestCase):
    def test_artifact_manifest_loads_and_detects_stale_source(self):
        manifest = load_artifact_manifest(DEFAULT_ARTIFACT_MANIFEST_PATH, verify_local_sources=True)
        self.assertEqual(manifest.profile, 'artifact')
        self.assertEqual(manifest.image_repository, 'localhost/udh-runtime-artifact')
        self.assertTrue(manifest.image_ref.startswith('localhost/udh-runtime-artifact@sha256:'))

        with tempfile.TemporaryDirectory() as tmp:
            fake_dir = Path(tmp)
            (fake_dir / 'at_runner.py').write_text('# altered\n', encoding='utf-8')
            (fake_dir / 'preview_bridge.py').write_text('# altered\n', encoding='utf-8')
            fake_manifest = fake_dir / 'artifact-image-manifest.json'
            fake_manifest.write_text(DEFAULT_ARTIFACT_MANIFEST_PATH.read_text(encoding='utf-8'), encoding='utf-8')
            with self.assertRaises(RuntimeIsolationError) as ctx:
                load_artifact_manifest(fake_manifest, verify_local_sources=True)
            self.assertIn('IMAGE_DIGEST_STALE', str(ctx.exception))

    def test_artifact_profile_forbids_ipc_and_agent_config_mounts(self):
        rm = RuntimeManager()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'dir'
            src.mkdir()
            for forbidden in ('/ipc', '/ipc/gateway.sock', '/udh/agent-config', '/udh/agent-config/x.json'):
                with self.assertRaises(RuntimeIsolationError):
                    rm.run_container(
                        profile='artifact',
                        command=['python3', '-V'],
                        mounts=(BindMount(src, forbidden, readonly=True),),
                        image_ref='localhost/udh-runtime-artifact@sha256:' + '0' * 64,
                    )

    def test_app_descriptor_fail_closed_and_ignores_smoke_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp) / 'art'
            write_sample_web_app(art, include_hostile_smoke=True)
            desc = load_app_descriptor(art)
            self.assertEqual(desc.app_type, 'web')
            self.assertEqual(desc.entrypoint, 'app.py')
            self.assertEqual(desc.docs_files, ('README.txt',))
            self.assertFalse(hasattr(desc, 'smoke_checks'))

            # Traversal no entrypoint deve falhar
            (art / 'udh_app.json').write_text(json.dumps({
                'schema_version': 1, 'app_type': 'web', 'entrypoint': '../evil.py', 'ready_path': '/health',
            }), encoding='utf-8')
            with self.assertRaises(PreviewError):
                load_app_descriptor(art)

            # Symlink em docs_files deve falhar
            (art / 'link.txt').symlink_to('/etc/hostname')
            (art / 'udh_app.json').write_text(json.dumps({
                'schema_version': 1, 'app_type': 'web', 'entrypoint': 'app.py',
                'ready_path': '/health', 'docs_files': ['link.txt'],
            }), encoding='utf-8')
            with self.assertRaises(PreviewError):
                load_app_descriptor(art)

    def test_connect_preview_uds_blocks_symlink_toctou_and_host_netns(self):
        ipc_dir = allocate_preview_ipc_dir()
        other_dir = allocate_preview_ipc_dir(prefix='udh-target-')
        try:
            self.assertLessEqual(len(str(ipc_dir / 'preview.sock').encode('utf-8')), MAX_UDS_PATH_BYTES)
            target_sock = other_dir / 'real.sock'
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                srv.bind(str(target_sock))
                srv.listen(1)

                # 1. Symlink em preview.sock deve ser recusado por O_PATH|O_NOFOLLOW
                (ipc_dir / 'preview.sock').symlink_to(target_sock)
                with self.assertRaises(PreviewError) as ctx_sym:
                    connect_preview_uds(ipc_dir, require_isolated_netns=False)
                self.assertEqual(ctx_sym.exception.reason_code, 'PREVIEW_UDS_SYMLINK_OR_MISSING')
                (ipc_dir / 'preview.sock').unlink()

                # 2. Socket real no namespace do HOST deve ser bloqueado por PREVIEW_NETNS_VIOLATION
                host_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    host_srv.bind(str(ipc_dir / 'preview.sock'))
                    host_srv.listen(1)
                    with self.assertRaises(PreviewError) as ctx_ns:
                        connect_preview_uds(ipc_dir, require_isolated_netns=True)
                    self.assertEqual(ctx_ns.exception.reason_code, 'PREVIEW_NETNS_VIOLATION')
                finally:
                    host_srv.close()
            finally:
                srv.close()
        finally:
            shutil.rmtree(ipc_dir, ignore_errors=True)
            shutil.rmtree(other_dir, ignore_errors=True)

    def test_harden_preview_response_csp_cookies_refresh_and_redirects(self):
        preview_origin = 'http://p-0123456789abcdef01234567.localhost:9999'
        ui_origin = 'http://127.0.0.1:9999'

        raw_resp = UdsHttpResponse(
            status=200,
            reason='OK',
            headers=(
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Transfer-Encoding', 'chunked'),
                ('Refresh', '0;url=https://evil.example'),
                ('Set-Cookie2', 'legacy=1'),
                ('Set-Cookie', '_UDH_PREVIEW_SESSION=forged; Path=/'),
                ('Set-Cookie', 'app_theme=dark; Domain=.localhost; Path=/'),
                ('Content-Security-Policy', "default-src * 'unsafe-inline'"),
            ),
            body=b'<h1>ok</h1>',
        )
        hardened = harden_preview_response(raw_resp, preview_origin=preview_origin, ui_origin=ui_origin, allow_iframe=False)
        hmap = {}
        cookies = []
        for k, v in hardened.headers:
            if k.lower() == 'set-cookie':
                cookies.append(v)
            else:
                hmap[k.lower()] = v

        self.assertNotIn('refresh', hmap)
        self.assertNotIn('set-cookie2', hmap)
        self.assertNotIn('transfer-encoding', hmap)
        self.assertIn("style-src 'self'", hmap['content-security-policy'])
        self.assertNotIn('unsafe-inline', hmap['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", hmap['content-security-policy'])
        self.assertEqual(cookies, ['app_theme=dark; Path=/'])

        # Modo iframe libera apenas ui_origin
        hardened_iframe = harden_preview_response(raw_resp, preview_origin=preview_origin, ui_origin=ui_origin, allow_iframe=True)
        csp_iframe = dict((k.lower(), v) for k, v in hardened_iframe.headers)['content-security-policy']
        self.assertIn(f'frame-ancestors {ui_origin}', csp_iframe)

        # Redirects hostis -> 502; relativo e same-origin -> preservados
        for bad_loc in (
            'https://evil.example/x',
            '//evil.example/x',
            '/\\evil.example/x',
            'http://127.0.0.1:9999/api/runs',
            'javascript:alert(1)',
            'data:text/html,<script>alert(1)</script>',
            '/\x01/control',
            '/\x0b/vtab',
        ):
            r = UdsHttpResponse(status=302, reason='Found', headers=(('Location', bad_loc),), body=b'')
            out = harden_preview_response(r, preview_origin=preview_origin, ui_origin=ui_origin, allow_iframe=False)
            self.assertEqual(out.status, 502, f'Deveria bloquear redirect {bad_loc!r}')

        for good_loc in ('/notes', f'{preview_origin}/notes'):
            r = UdsHttpResponse(status=302, reason='Found', headers=(('Location', good_loc),), body=b'')
            out = harden_preview_response(r, preview_origin=preview_origin, ui_origin=ui_origin, allow_iframe=False)
            self.assertEqual(out.status, 302)

    def test_upstream_smuggling_te_cl_and_bad_header_name_rejected(self):
        from udh.preview import _read_http_response_from_uds

        for bad_wire in (
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 5\r\n\r\n0\r\n\r\n',
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n0\r\n\r\n',
            b'HTTP/1.1 200 OK\r\nContent-Length : 2\r\n\r\nok',
        ):
            s1, s2 = socket.socketpair()
            try:
                s1.sendall(bad_wire)
                s1.shutdown(socket.SHUT_WR)
                with self.assertRaises(PreviewError) as ctx:
                    _read_http_response_from_uds(s2, method='GET')
                self.assertEqual(ctx.exception.status_code, 502)
            finally:
                s1.close()
                s2.close()

    def test_parse_trusted_smoke_checks_fail_closed(self):
        with self.assertRaises(CoreError):
            parse_trusted_smoke_checks([])


@unittest.skipUnless(_podman_ready_for_artifact(), 'Podman rootless ou imagem artifact-only indisponível')
class ArtifactPreviewPodmanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='udh-c4-int-')
        self.root = Path(self.tmp.name)
        self.db_path = self.root / 'state.db'
        self.trust = TrustStore(self.root / 'trust_store')
        self.core = CoreService(SQLiteStore(self.db_path))
        self.core.initialize(schema_version=4)
        self.rm = RuntimeManager()

        self.app_src = self.root / 'app_src'
        self.at_src = self.root / 'at_src'
        write_sample_web_app(self.app_src, include_hostile_smoke=True)
        write_sample_at_suite(self.at_src)
        self.at_manifest = self.trust.seal_at_suite(self.at_src, scenarios=('app-notas-basico',))

    def tearDown(self):
        self.tmp.cleanup()

    def test_artifact_image_has_zero_agents_and_enforces_isolation(self):
        manifest = self.rm.load_artifact_manifest()
        work_dir = self.root / 'work'
        work_dir.mkdir()
        art_manifest = self.trust.promote_artifact(self.app_src)
        art_dir, _ = self.trust.verify_artifact(art_manifest['artifact_digest'])
        at_dir, _ = self.trust.verify_sealed_at(self.at_manifest['at_version'])

        res = self.rm.run_artifact(
            command=[
                'python3', '-I', '-B', '-c',
                'import pathlib; '
                'assert not any(pathlib.Path(p).exists() for p in ["/opt/pi","/opt/udh/bin/node","/opt/udh/bin/opencode","/ipc","/udh/agent-config"]); '
                'print("ISOLATED_OK")',
            ],
            artifact_dir=art_dir,
            at_dir=at_dir,
            work_dir=work_dir,
            image_ref=manifest.image_ref,
            forbidden_rw_roots=(self.trust.root,),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('ISOLATED_OK', res.stdout)
        self.assertTrue(res.cleanup_verified)
        self.assertEqual(res.inspection['HostConfig']['NetworkMode'], 'none')
        self.assertTrue(res.inspection['HostConfig']['ReadonlyRootfs'])

    def test_preview_end_to_end_over_uds_with_isolated_netns_and_cleanup(self):
        run = self.core.create('notes-web', '1.0', 'req-prev-1', ticket_id='t-1', mode='STANDARD')
        self.core.start(run.run_id, 0, 'req-start-1')
        promo = self.core.promote_run_artifact(run.run_id, 'req-promo-1', self.app_src, trust_store=self.trust)
        digest = promo['artifact_manifest']['artifact_digest']

        pm = PreviewManager(trust_store=self.trust, runtime_manager=self.rm, require_isolated_netns=True)
        started = pm.start_preview(run_id=run.run_id, artifact_digest=digest, embed_mode='tab')
        pid = started['preview_id']
        try:
            self.assertEqual(started['data_label'], DEMO_DATA_LABEL)
            sess = pm.get_session(pid)
            self.assertIsNotNone(sess)
            insp = sess.process.inspect()
            self.assertIsNotNone(insp)
            self.assertEqual(insp['HostConfig']['NetworkMode'], 'none')
            self.assertTrue(insp['HostConfig']['ReadonlyRootfs'])
            self.assertFalse(insp['HostConfig'].get('PortBindings'))

            preview_origin = f'http://{sess.hostname}:9999'
            ui_origin = 'http://127.0.0.1:9999'
            ui_token = 'secret-ui-token-value-123456'

            # 1. Rota principal + CSP restritiva
            resp_home = pm.proxy_request(
                sess, method='GET', path='/', headers=(), body=b'',
                ui_token=ui_token, preview_origin=preview_origin, ui_origin=ui_origin,
            )
            self.assertEqual(resp_home.status, 200)
            self.assertIn(b'App Notas', resp_home.body)
            hdrs = dict((k.lower(), v) for k, v in resp_home.headers)
            self.assertIn("style-src 'self'", hdrs['content-security-policy'])
            self.assertIn("frame-ancestors 'none'", hdrs['content-security-policy'])

            # 2. Cookie do proxy removido na ida, cookie próprio do app preservado
            resp_ck = pm.proxy_request(
                sess,
                method='GET',
                path='/cookie-echo',
                headers=(('Cookie', '_udh_preview_session=secret; app_pref=blue'),),
                body=b'',
                ui_token=ui_token,
                preview_origin=preview_origin,
                ui_origin=ui_origin,
            )
            ck_json = json.loads(resp_ck.body.decode('utf-8'))
            self.assertEqual(ck_json['cookie'], 'app_pref=blue')
            self.assertEqual(ck_json['token'], '')

            # 3. X-UDH-Token rejeitado com 403 (B1.7(d))
            with self.assertRaises(PreviewError) as ctx_tok:
                pm.proxy_request(
                    sess, method='GET', path='/', headers=(('X-UDH-Token', ui_token),), body=b'',
                    ui_token=ui_token, preview_origin=preview_origin, ui_origin=ui_origin,
                )
            self.assertEqual(ctx_tok.exception.status_code, 403)

            # 4. Resposta hostil do app: Refresh/Set-Cookie2/Domain=/_udh_preview_session sanitizados e redirect externo -> 502
            resp_hostile = pm.proxy_request(
                sess, method='GET', path='/hostile-headers', headers=(), body=b'',
                ui_token=ui_token, preview_origin=preview_origin, ui_origin=ui_origin,
            )
            h_names = [k.lower() for k, _ in resp_hostile.headers]
            self.assertNotIn('refresh', h_names)
            self.assertNotIn('set-cookie2', h_names)
            set_cookies = [v for k, v in resp_hostile.headers if k.lower() == 'set-cookie']
            self.assertEqual(set_cookies, ['app_pref=dark; Path=/'])

            resp_redir = pm.proxy_request(
                sess, method='GET', path='/bad-redirect', headers=(), body=b'',
                ui_token=ui_token, preview_origin=preview_origin, ui_origin=ui_origin,
            )
            self.assertEqual(resp_redir.status, 502)

            container_name = sess.process.container_name
            stopped = pm.stop_preview(pid)
            self.assertTrue(stopped['cleanup_verified'])
            self.assertTrue(stopped['data_discarded'])
            self.assertTrue(stopped['ipc_removed'])
            exists = subprocess.run(['podman', 'container', 'exists', container_name], capture_output=True, check=False)
            self.assertEqual(exists.returncode, 1)
        finally:
            pm.stop_all()

    def test_smoke_runner_restart_persistence_and_adr028_delivery_rules(self):
        run = self.core.create('notes-web', '1.0', 'req-deliv-1', ticket_id='t-deliv', mode='STANDARD')
        self.core.start(run.run_id, 0, 'req-start-d1')

        # Relatório falsificado de agente no workspace não afeta delivery_summary (UX-01/UX-03)
        (self.app_src / 'agent_claim.json').write_text(
            json.dumps({'status': 'DONE', 'delivered': True}), encoding='utf-8',
        )
        promo = self.core.promote_run_artifact(run.run_id, 'req-promo-d1', self.app_src, trust_store=self.trust)
        digest = promo['artifact_manifest']['artifact_digest']

        s1 = self.core.delivery_summary(run.run_id, trust_store=self.trust)
        self.assertEqual(s1['delivery_stage'], 'IMPLEMENTED')
        self.assertFalse(s1['can_deliver'])

        # 1. AT oficial na imagem artifact-only -> TESTED (mas ainda NÃO pode entregar sem smoke!)
        executor = TrustedExecutor(core_service=self.core, trust_store=self.trust, runtime_manager=self.rm)
        at_res = executor.verify_run(
            run.run_id,
            request_id='req-at-d1',
            artifact_digest=digest,
            at_version=self.at_manifest['at_version'],
            use_artifact_image=True,
        )
        self.assertEqual(at_res['report']['verdict'], 'PASS')
        s2 = self.core.delivery_summary(run.run_id, trust_store=self.trust)
        self.assertEqual(s2['delivery_stage'], 'TESTED')
        self.assertFalse(s2['can_deliver'])
        with self.assertRaises(ConflictError):
            self.core.deliver_run(run.run_id, 1, 'req-deliver-too-early', trust_store=self.trust)

        # 2. Smoke oficial B3.4 (com persistência após reinício real; ignora smoke_checks=[] no udh_app.json!)
        smoke_runner = SmokeRunner(core_service=self.core, trust_store=self.trust, runtime_manager=self.rm)
        smoke_res = smoke_runner.run_smoke(
            run.run_id,
            request_id='req-smoke-d1',
            artifact_digest=digest,
            smoke_checks=trusted_notes_smoke_checks(),
        )
        self.assertEqual(smoke_res['report']['verdict'], 'PASS', smoke_res['report'])
        self.assertTrue(smoke_res['report']['restart_verified'])
        self.assertEqual(smoke_res['report']['docs_files_verified'], ['README.txt'])

        s3 = self.core.delivery_summary(run.run_id, trust_store=self.trust)
        self.assertEqual(s3['delivery_stage'], 'ACCEPTED')
        self.assertTrue(s3['can_deliver'])

        # 3. Um SMOKE_RESULT=FAIL posterior no MESMO digest invalida o PASS anterior!
        bad_smoke = smoke_runner.run_smoke(
            run.run_id,
            request_id='req-smoke-fail-d1',
            artifact_digest=digest,
            smoke_checks=(SmokeCheck(name='must_fail', method='GET', path='/nao-existe', expected_status=200),),
        )
        self.assertEqual(bad_smoke['report']['verdict'], 'FAIL')
        s_after_fail = self.core.delivery_summary(run.run_id, trust_store=self.trust)
        self.assertEqual(s_after_fail['delivery_stage'], 'TESTED')
        self.assertFalse(s_after_fail['can_deliver'])
        with self.assertRaises(ConflictError):
            self.core.deliver_run(run.run_id, 1, 'req-deliver-after-fail', trust_store=self.trust)

        # 4. Novo SMOKE_RESULT=PASS restaura ACCEPTED e deliver_run conclui DELIVERED
        smoke_pass2 = smoke_runner.run_smoke(
            run.run_id,
            request_id='req-smoke-pass2-d1',
            artifact_digest=digest,
            smoke_checks=trusted_notes_smoke_checks(),
        )
        self.assertEqual(smoke_pass2['report']['verdict'], 'PASS')
        delivered = self.core.deliver_run(run.run_id, 1, 'req-deliver-ok', trust_store=self.trust)
        self.assertEqual(delivered['delivery']['delivery_stage'], 'DELIVERED')
        self.assertEqual(delivered['delivery']['run_state'], 'DONE')

        # 5. Promover novo digest em outra run invalida evidências antigas (STALE)
        run2 = self.core.create('notes-web', '2.0', 'req-stale-1', mode='STANDARD')
        self.core.start(run2.run_id, 0, 'req-stale-start')
        p1 = self.core.promote_run_artifact(run2.run_id, 'req-stale-p1', self.app_src, trust_store=self.trust)
        d1 = p1['artifact_manifest']['artifact_digest']
        executor.verify_run(
            run2.run_id, request_id='req-stale-at1', artifact_digest=d1,
            at_version=self.at_manifest['at_version'], use_artifact_image=True,
        )
        smoke_runner.run_smoke(
            run2.run_id, request_id='req-stale-sm1', artifact_digest=d1,
            smoke_checks=trusted_notes_smoke_checks(),
        )
        self.assertEqual(self.core.delivery_summary(run2.run_id, trust_store=self.trust)['delivery_stage'], 'ACCEPTED')

        # Altera artefato e promove digest d2 -> evidências de d1 não valem para d2!
        (self.app_src / 'README.txt').write_text('App Notas v2.\n', encoding='utf-8')
        p2 = self.core.promote_run_artifact(run2.run_id, 'req-stale-p2', self.app_src, trust_store=self.trust)
        self.assertNotEqual(p2['artifact_manifest']['artifact_digest'], d1)
        s_stale = self.core.delivery_summary(run2.run_id, trust_store=self.trust)
        self.assertEqual(s_stale['delivery_stage'], 'IMPLEMENTED')
        self.assertTrue(s_stale['has_stale_evidences'])
        self.assertFalse(s_stale['can_deliver'])


if __name__ == '__main__':
    unittest.main()
