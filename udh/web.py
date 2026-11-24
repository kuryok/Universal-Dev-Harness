"""Servidor loopback único do Core: interface guiada mínima (B1.7) + proxy de preview (B3.6)."""
import argparse
from collections.abc import Sequence
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import secrets
import signal
import socketserver
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .domain import CoreError, require_canonical_uuid, require_sha256, require_text, strict_json_loads
from .executor import TrustedExecutor
from .explanations import (
    explain_action_error,
    explain_delivery_block,
    explain_doctor_report,
    explain_run_block,
)
from .preview import (
    MUTATING_HTTP_METHODS,
    PREVIEW_COOKIE_NAME,
    PREVIEW_HOST_RE,
    PREVIEW_ID_RE,
    PreviewError,
    PreviewManager,
    RESERVED_AUTH_PATH,
)
from .runtime import RuntimeIsolationError, RuntimeManager
from .service import CoreService
from .smoke import SmokeCheck, SmokeRunner, parse_trusted_smoke_checks
from .store import SQLiteStore
from .transitions import AuthorizationError, ConflictError
from .trust_store import AT_VERSION_RE, TrustStore, TrustStoreError, default_store_dir
from .web_ui import APP_CSS, APP_JS, INDEX_HTML

MAX_UI_BODY_BYTES = 256 * 1024
MAX_PREVIEW_BODY_BYTES = 4 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 15.0
MAX_CONCURRENT_REQUESTS = 32
DUPLICATE_CHECK_HEADERS = (
    'Host',
    'Origin',
    'Sec-Fetch-Site',
    'X-UDH-Token',
    'Content-Length',
    'Transfer-Encoding',
)

RUN_DETAIL_RE = re.compile(r'^/api/runs/([0-9a-f-]{36})$')
RUN_ACTION_RE = re.compile(
    r'^/api/runs/([0-9a-f-]{36})/(start|block|unblock|verify|smoke|deliver|preview/start)$'
)
PREVIEW_ACTION_RE = re.compile(r'^/api/previews/([0-9a-f]{24})/(grant|stop)$')


class _HardenedThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, server_address, handler_cls, app: 'GuidedWebServer'):
        self.app = app
        self._conn_sem = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(server_address, handler_cls)

    def process_request(self, request, client_address):
        if not self._conn_sem.acquire(blocking=False):
            try:
                request.sendall(
                    b'HTTP/1.1 503 Service Unavailable\r\n'
                    b'Content-Type: text/plain; charset=utf-8\r\n'
                    b'Content-Length: 26\r\n'
                    b'Connection: close\r\n\r\n'
                    b'Limite de conexoes ativo.\n'
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._conn_sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(SOCKET_TIMEOUT_SECONDS)
            super().process_request_thread(request, client_address)
        finally:
            self._conn_sem.release()


def _extract_cookie_value(cookie_headers: Sequence[str], target_name: str) -> str | None:
    found: str | None = None
    for header in cookie_headers:
        for part in header.split(';'):
            piece = part.strip()
            if not piece or '=' not in piece:
                continue
            name, val = piece.split('=', 1)
            if name.strip().lower() == target_name.lower():
                found = val.strip()
    return found


class _GuidedRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Nunca registrar cabeçalhos/tokens/cookies em stderr por padrão
        return

    @property
    def app(self) -> 'GuidedWebServer':
        return self.server.app  # type: ignore[attr-defined]

    def _ui_security_headers(self) -> list[tuple[str, str]]:
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "img-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            f"frame-src http://*.localhost:{self.app.port}"
        )
        return [
            ('Content-Security-Policy', csp),
            ('Cross-Origin-Resource-Policy', 'same-origin'),
            ('X-Content-Type-Options', 'nosniff'),
            ('Referrer-Policy', 'no-referrer'),
            ('Cache-Control', 'no-store'),
            ('Connection', 'close'),
        ]

    def _send_bytes(
        self,
        status: int,
        reason: str,
        body: bytes,
        *,
        content_type: str,
        extra_headers: Sequence[tuple[str, str]] = (),
        include_ui_headers: bool = False,
    ) -> None:
        self.send_response(status, reason)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if include_ui_headers:
            for k, v in self._ui_security_headers():
                self.send_header(k, v)
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict, *, reason: str = '') -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self._send_bytes(
            status,
            reason or ('OK' if status < 400 else 'Error'),
            raw,
            content_type='application/json; charset=utf-8',
            include_ui_headers=True,
        )

    def _send_ui_error(
        self,
        status: int,
        reason_code: str,
        message: str,
        *,
        category: str = 'POLICY_VIOLATION',
    ) -> None:
        self._send_json(
            status,
            {
                'error': message,
                'reason_code': reason_code,
                'explanation': explain_action_error(reason_code, message, category=category),
            },
        )

    def _read_body(self, *, max_bytes: int) -> bytes | None:
        te_values = self.headers.get_all('Transfer-Encoding') or []
        if te_values:
            self._send_ui_error(400, 'TRANSFER_ENCODING_DENIED', 'Transfer-Encoding não é aceito neste servidor.')
            return None
        cl_values = self.headers.get_all('Content-Length') or []
        if not cl_values:
            return b''
        try:
            length = int(cl_values[0].strip())
        except ValueError:
            self._send_ui_error(400, 'INVALID_CONTENT_LENGTH', 'Content-Length inválido.')
            return None
        if length < 0 or length > max_bytes:
            self._send_ui_error(413, 'PAYLOAD_TOO_LARGE', 'Corpo da requisição excede o limite permitido.')
            return None
        if length == 0:
            return b''
        data = self.rfile.read(length)
        if len(data) != length:
            self._send_ui_error(400, 'TRUNCATED_BODY', 'Corpo da requisição incompleto.')
            return None
        return data

    def _check_duplicate_headers(self) -> tuple[bool, str | None]:
        hosts = self.headers.get_all('Host') or []
        if len(hosts) != 1:
            return False, 'HOST_REQUIRED_SINGLE'
        for name in DUPLICATE_CHECK_HEADERS:
            if name == 'Host':
                continue
            vals = self.headers.get_all(name) or []
            if len(vals) > 1:
                return False, f'DUPLICATE_{name.upper().replace("-", "_")}'
        return True, None

    def _dispatch(self) -> None:
        self.close_connection = True
        ok_headers, dup_code = self._check_duplicate_headers()
        if not ok_headers:
            if dup_code == 'HOST_REQUIRED_SINGLE':
                self._send_ui_error(421, 'INVALID_HOST', 'Cabeçalho Host ausente ou duplicado.')
            else:
                self._send_ui_error(400, dup_code or 'DUPLICATE_HEADER', 'Cabeçalho duplicado rejeitado.')
            return

        raw_host = (self.headers.get('Host') or '').strip().lower()
        ui_host = f'127.0.0.1:{self.app.port}'
        if raw_host == ui_host:
            self._handle_ui()
            return

        match = PREVIEW_HOST_RE.fullmatch(raw_host)
        if match and int(match.group(2)) == self.app.port:
            preview_id = match.group(1)
            session = self.app.preview_manager.get_session(preview_id)
            if session is None:
                self._send_bytes(
                    421,
                    'Misdirected Request',
                    b'Preview inativo ou desconhecido.',
                    content_type='text/plain; charset=utf-8',
                    extra_headers=(
                        ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"),
                        ('X-Content-Type-Options', 'nosniff'),
                        ('Referrer-Policy', 'no-referrer'),
                        ('Cache-Control', 'no-store'),
                        ('Connection', 'close'),
                    ),
                )
                return
            self._handle_preview(session, f'http://{raw_host}')
            return

        self._send_ui_error(421, 'INVALID_HOST', 'Host não autorizado (proteção contra DNS rebinding).')

    def _handle_ui(self) -> None:
        ui_origin = f'http://127.0.0.1:{self.app.port}'
        origin = self.headers.get('Origin')
        if origin is not None and origin.strip() != ui_origin:
            self._send_ui_error(403, 'ORIGIN_DENIED', 'Origem da requisição não autorizada (B1.7(a)).')
            return

        parsed = urlsplit(self.path)
        route = parsed.path

        if self.command == 'OPTIONS':
            self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Método OPTIONS não habilitado.')
            return

        # Assets estáticos (somente GET/HEAD, sem expor dados ou tokens)
        if route in ('/', '/assets/app.css', '/assets/app.js'):
            if self.command not in ('GET', 'HEAD'):
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Método não permitido para recurso estático.')
                return
            if route == '/':
                self._send_bytes(200, 'OK', INDEX_HTML.encode('utf-8'),
                                 content_type='text/html; charset=utf-8', include_ui_headers=True)
            elif route == '/assets/app.css':
                self._send_bytes(200, 'OK', APP_CSS.encode('utf-8'),
                                 content_type='text/css; charset=utf-8', include_ui_headers=True)
            else:
                self._send_bytes(200, 'OK', APP_JS.encode('utf-8'),
                                 content_type='application/javascript; charset=utf-8', include_ui_headers=True)
            return

        if not route.startswith('/api/'):
            self._send_ui_error(404, 'NOT_FOUND', 'Rota não encontrada.')
            return

        # B1.7(a): validação de Sec-Fetch-Site em /api/*
        sfs = self.headers.get('Sec-Fetch-Site')
        if sfs is not None:
            sfs_clean = sfs.strip().lower()
            if self.command in MUTATING_HTTP_METHODS and sfs_clean != 'same-origin':
                self._send_ui_error(403, 'CROSS_SITE_DENIED', 'Requisição cross-site bloqueada (Sec-Fetch-Site).')
                return
            if self.command in ('GET', 'HEAD') and sfs_clean not in ('same-origin', 'none'):
                self._send_ui_error(403, 'CROSS_SITE_DENIED', 'Requisição cross-site bloqueada (Sec-Fetch-Site).')
                return

        # B1.7(b)-(c): token obrigatório em cabeçalho X-UDH-Token para TODAS as rotas /api/* (nunca cookie)
        provided_token = self.headers.get('X-UDH-Token')
        if provided_token is None or not provided_token.strip():
            self._send_ui_error(401, 'TOKEN_REQUIRED', 'Token de sessão X-UDH-Token ausente.')
            return
        if not hmac.compare_digest(provided_token.strip(), self.app.session_token):
            self._send_ui_error(403, 'TOKEN_INVALID', 'Token de sessão X-UDH-Token inválido.')
            return

        try:
            self._route_api(route)
        except PreviewError as exc:
            self._send_ui_error(exc.status_code, exc.reason_code, str(exc), category='POLICY_VIOLATION')
        except ConflictError as exc:
            self._send_ui_error(409, 'STATE_CONFLICT', str(exc), category='FUNCTIONAL_FAILURE')
        except AuthorizationError as exc:
            self._send_ui_error(403, 'AUTHORIZATION_DENIED', str(exc), category='POLICY_VIOLATION')
        except (CoreError, TrustStoreError, RuntimeIsolationError) as exc:
            self._send_ui_error(400, 'CORE_CONTRACT_ERROR', str(exc), category='FUNCTIONAL_FAILURE')

    def _read_json_body(self) -> dict | None:
        raw = self._read_body(max_bytes=MAX_UI_BODY_BYTES)
        if raw is None:
            return None
        if not raw:
            return {}
        try:
            parsed = strict_json_loads(raw.decode('utf-8'), 'Corpo JSON')
        except (CoreError, UnicodeDecodeError) as exc:
            self._send_ui_error(400, 'INVALID_JSON', str(exc), category='FUNCTIONAL_FAILURE')
            return None
        if not isinstance(parsed, dict):
            self._send_ui_error(400, 'INVALID_JSON', 'Corpo JSON deve ser um objeto.', category='FUNCTIONAL_FAILURE')
            return None
        return parsed

    def _resolve_artifact_digest(self, run_id: str, payload: dict) -> str:
        requested = payload.get('artifact_digest')
        if requested is not None:
            require_sha256(requested, 'Digest do artefato')
            self.app.trust_store.verify_artifact(requested)
            return requested
        delivery = self.app.core_service.delivery_summary(run_id, trust_store=self.app.trust_store)
        current = delivery.get('current_artifact_digest')
        if not current:
            raise ConflictError('Nenhum artefato promovido e íntegro encontrado para esta execução.')
        return current

    def _route_api(self, route: str) -> None:
        if route == '/api/overview':
            if self.command != 'GET':
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Use GET em /api/overview.')
                return
            doctor_raw = self.app.core_service.doctor(
                store_dir=self.app.trust_store.root,
                env_probes=self.app.doctor_env_probes,
            )
            health = explain_doctor_report(doctor_raw)
            counts = self.app.core_service.count_runs_by_state()
            runs = [
                {
                    'run_id': r.run_id,
                    'spec_id': r.spec_id,
                    'spec_version': r.spec_version,
                    'ticket_id': r.ticket_id,
                    'mode': r.mode,
                    'state': r.state,
                    'revision': r.revision,
                    'created_at': r.created_at,
                }
                for r in self.app.core_service.list_runs(limit=100)
            ]
            self._send_json(200, {
                'health': health,
                'counts': counts,
                'runs': runs,
                'has_default_at': self.app.default_at_version is not None,
                'has_trusted_smoke_checks': bool(self.app.trusted_smoke_checks),
            })
            return

        if route == '/api/runs':
            if self.command != 'POST':
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Use POST em /api/runs.')
                return
            body = self._read_json_body()
            if body is None:
                return
            run = self.app.core_service.create(
                body.get('spec_id', ''),
                body.get('spec_version', ''),
                body.get('request_id', ''),
                ticket_id=body.get('ticket_id'),
                mode=body.get('mode'),
            )
            self._send_json(201, {'run': run.to_dict()}, reason='Created')
            return

        m_detail = RUN_DETAIL_RE.fullmatch(route)
        if m_detail:
            if self.command != 'GET':
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Use GET para consultar uma execução.')
                return
            run_id = require_canonical_uuid(m_detail.group(1), 'Identificador da execução')
            run = self.app.core_service.show(run_id)
            delivery = self.app.core_service.delivery_summary(run_id, trust_store=self.app.trust_store)
            self._send_json(200, {
                'run': run.to_dict(),
                'delivery': delivery,
                'block_explanation': explain_run_block(run),
                'delivery_explanation': explain_delivery_block(delivery),
                'previews': self.app.preview_manager.list_sessions_for_run(run_id),
            })
            return

        m_action = RUN_ACTION_RE.fullmatch(route)
        if m_action:
            if self.command != 'POST':
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Ações de execução exigem POST.')
                return
            run_id = require_canonical_uuid(m_action.group(1), 'Identificador da execução')
            action = m_action.group(2)
            body = self._read_json_body()
            if body is None:
                return

            if action == 'start':
                res = self.app.core_service.start(
                    run_id,
                    body.get('expected_revision'),
                    body.get('request_id', ''),
                )
                self._send_json(200, res)
                return
            if action == 'block':
                res = self.app.core_service.block_v4(
                    run_id,
                    body.get('expected_revision'),
                    body.get('request_id', ''),
                    category=body.get('category', 'ADMIN_PAUSE'),
                    reason_code=body.get('reason_code', 'USER_PAUSED'),
                    reason=body.get('reason', ''),
                    next_action=body.get('next_action', 'Revise o motivo e retome quando pronto.'),
                )
                self._send_json(200, res)
                return
            if action == 'unblock':
                res = self.app.core_service.unblock_v4(
                    run_id,
                    body.get('expected_revision'),
                    body.get('request_id', ''),
                    reason=body.get('reason', ''),
                )
                self._send_json(200, res)
                return
            if action == 'verify':
                artifact_digest = self._resolve_artifact_digest(run_id, body)
                at_version = body.get('at_version') or self.app.default_at_version
                if not isinstance(at_version, str) or not AT_VERSION_RE.fullmatch(at_version):
                    raise CoreError('at_version inválido ou não configurado na TrustStore (esperado sha256:<64-hex>).')
                req_id = require_text(body.get('request_id', ''), 'Identificador da requisição')
                res = self.app.executor.verify_run(
                    run_id,
                    request_id=req_id,
                    artifact_digest=artifact_digest,
                    at_version=at_version,
                    use_artifact_image=True,
                )
                self._send_json(200, res)
                return
            if action == 'smoke':
                artifact_digest = self._resolve_artifact_digest(run_id, body)
                if not self.app.trusted_smoke_checks:
                    raise CoreError('SMOKE_SPEC_UNCONFIGURED: contrato oficial de smoke_checks não configurado no Core.')
                req_id = require_text(body.get('request_id', ''), 'Identificador da requisição')
                res = self.app.smoke_runner.run_smoke(
                    run_id,
                    request_id=req_id,
                    artifact_digest=artifact_digest,
                    smoke_checks=self.app.trusted_smoke_checks,
                )
                self._send_json(200, res)
                return
            if action == 'deliver':
                res = self.app.core_service.deliver_run(
                    run_id,
                    body.get('expected_revision'),
                    body.get('request_id', ''),
                    trust_store=self.app.trust_store,
                )
                self._send_json(200, res)
                return
            if action == 'preview/start':
                artifact_digest = self._resolve_artifact_digest(run_id, body)
                embed_mode = body.get('embed_mode', 'tab')
                res = self.app.preview_manager.start_preview(
                    run_id=run_id,
                    artifact_digest=artifact_digest,
                    embed_mode=embed_mode,
                )
                launch_url = f'http://{res["hostname"]}:{self.app.port}{RESERVED_AUTH_PATH}?grant={res["grant"]}'
                out = dict(res)
                out['launch_url'] = launch_url
                self._send_json(200, out)
                return

        m_prev = PREVIEW_ACTION_RE.fullmatch(route)
        if m_prev:
            if self.command != 'POST':
                self._send_ui_error(405, 'METHOD_NOT_ALLOWED', 'Ações de preview exigem POST.')
                return
            preview_id = m_prev.group(1)
            action = m_prev.group(2)
            body = self._read_json_body()
            if body is None:
                return
            if action == 'grant':
                res = self.app.preview_manager.issue_grant(preview_id)
                res['launch_url'] = f'http://{res["hostname"]}:{self.app.port}{RESERVED_AUTH_PATH}?grant={res["grant"]}'
                self._send_json(200, res)
                return
            if action == 'stop':
                res = self.app.preview_manager.stop_preview(preview_id)
                self._send_json(200, res)
                return

        self._send_ui_error(404, 'NOT_FOUND', 'Endpoint de API não encontrado.')

    def _send_preview_error(self, status: int, reason_code: str, message: str) -> None:
        body = f'{reason_code}: {message}\n'.encode('utf-8')
        self._send_bytes(
            status,
            'Forbidden' if status == 403 else 'Error',
            body,
            content_type='text/plain; charset=utf-8',
            extra_headers=(
                ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"),
                ('X-Content-Type-Options', 'nosniff'),
                ('Referrer-Policy', 'no-referrer'),
                ('Cache-Control', 'no-store'),
                ('Connection', 'close'),
            ),
        )

    def _handle_preview(self, session, preview_origin: str) -> None:
        # B1.7(d): bloquear X-UDH-Token ou o valor do token da UI em qualquer cabeçalho no host de preview
        req_headers = list(self.headers.items())
        for name, val in req_headers:
            if name.lower().strip() == 'x-udh-token' or (self.app.session_token and self.app.session_token in val):
                self._send_preview_error(403, 'UI_TOKEN_FORBIDDEN_IN_PREVIEW', 'Token da UI não permitido no preview.')
                return

        parsed = urlsplit(self.path)
        if parsed.path == RESERVED_AUTH_PATH:
            if self.command != 'GET':
                self._send_preview_error(405, 'METHOD_NOT_ALLOWED', 'Autenticação de preview aceita somente GET.')
                return
            qs = parse_qs(parsed.query, keep_blank_values=False)
            grant = (qs.get('grant') or [''])[0]
            cookie_token = self.app.preview_manager.consume_grant(session.preview_id, grant)
            if not cookie_token:
                self._send_preview_error(403, 'PREVIEW_GRANT_INVALID', 'Autorização de preview inválida, expirada ou já utilizada.')
                return
            # Cookie host-only (SEM Domain=), HttpOnly, SameSite=Lax, exclusivo deste subdomínio p-<id>.localhost
            set_cookie = f'{PREVIEW_COOKIE_NAME}={cookie_token}; Path=/; HttpOnly; SameSite=Lax'
            self._send_bytes(
                303,
                'See Other',
                b'',
                content_type='text/plain; charset=utf-8',
                extra_headers=(
                    ('Location', '/'),
                    ('Set-Cookie', set_cookie),
                    ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"),
                    ('X-Content-Type-Options', 'nosniff'),
                    ('Referrer-Policy', 'no-referrer'),
                    ('Cache-Control', 'no-store'),
                    ('Connection', 'close'),
                ),
            )
            return

        cookie_headers = self.headers.get_all('Cookie') or []
        cookie_val = _extract_cookie_value(cookie_headers, PREVIEW_COOKIE_NAME)
        validated_session = self.app.preview_manager.validate_session_cookie(session.preview_id, cookie_val)
        if validated_session is None:
            self._send_preview_error(403, 'PREVIEW_UNAUTHORIZED', 'Sessão de preview ausente ou inválida.')
            return

        if self.command in MUTATING_HTTP_METHODS:
            origin = self.headers.get('Origin')
            if origin is not None and origin.strip().lower() != preview_origin.lower():
                self._send_preview_error(403, 'PREVIEW_ORIGIN_DENIED', 'Origem inválida para ação no preview.')
                return
            sfs = self.headers.get('Sec-Fetch-Site')
            if sfs is not None and sfs.strip().lower() != 'same-origin':
                self._send_preview_error(403, 'PREVIEW_CROSS_SITE_DENIED', 'Requisição cross-site bloqueada no preview.')
                return

        body = self._read_body(max_bytes=MAX_PREVIEW_BODY_BYTES)
        if body is None:
            return

        try:
            resp = self.app.preview_manager.proxy_request(
                validated_session,
                method=self.command,
                path=self.path,
                headers=req_headers,
                body=body,
                ui_token=self.app.session_token,
                preview_origin=preview_origin,
                ui_origin=f'http://127.0.0.1:{self.app.port}',
            )
        except PreviewError as exc:
            self._send_preview_error(exc.status_code, exc.reason_code, str(exc))
            return

        self.send_response(resp.status, resp.reason)
        for name, val in resp.headers:
            self.send_header(name, val)
        self.end_headers()
        if self.command != 'HEAD' and resp.body:
            self.wfile.write(resp.body)

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._dispatch()


class GuidedWebServer:
    """Servidor loopback único (127.0.0.1) integrando interface guiada B1.7 e proxy isolado B3.6."""

    def __init__(
        self,
        *,
        core_service: CoreService,
        trust_store: TrustStore,
        runtime_manager: RuntimeManager | None = None,
        host: str = '127.0.0.1',
        port: int = 0,
        session_token: str | None = None,
        default_at_version: str | None = None,
        trusted_smoke_checks: Sequence[SmokeCheck | dict] | None = None,
        doctor_env_probes: dict | None = None,
        require_isolated_netns: bool = True,
    ):
        if host != '127.0.0.1':
            raise CoreError('Interface web local (B1.7) permite bind exclusivamente em 127.0.0.1.')
        if type(port) is not int or port < 0 or port > 65535:
            raise CoreError('Porta inválida (esperado 0..65535).')
        if session_token is not None:
            if not isinstance(session_token, str) or len(session_token) < 16:
                raise CoreError('session_token deve conter ao menos 16 caracteres.')
            self.session_token = session_token
        else:
            self.session_token = secrets.token_urlsafe(32)

        self.host = host
        self.core_service = core_service
        self.trust_store = trust_store
        self.runtime_manager = runtime_manager or RuntimeManager()
        self.executor = TrustedExecutor(
            core_service=core_service,
            trust_store=trust_store,
            runtime_manager=self.runtime_manager,
        )
        self.smoke_runner = SmokeRunner(
            core_service=core_service,
            trust_store=trust_store,
            runtime_manager=self.runtime_manager,
            require_isolated_netns=require_isolated_netns,
        )
        self.preview_manager = PreviewManager(
            trust_store=trust_store,
            runtime_manager=self.runtime_manager,
            require_isolated_netns=require_isolated_netns,
        )
        self.default_at_version = default_at_version
        self.trusted_smoke_checks = (
            parse_trusted_smoke_checks(trusted_smoke_checks) if trusted_smoke_checks is not None else None
        )
        self.doctor_env_probes = doctor_env_probes

        self._httpd: _HardenedThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = port

    @property
    def ui_url(self) -> str:
        return f'http://127.0.0.1:{self.port}/'

    @property
    def entry_url(self) -> str:
        return f'http://127.0.0.1:{self.port}/#token={self.session_token}'

    def start(self) -> str:
        if self._httpd is not None:
            return self.entry_url
        httpd = _HardenedThreadingHTTPServer((self.host, self.port), _GuidedRequestHandler, self)
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self._thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True)
        self._thread.start()
        return self.entry_url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            finally:
                httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.preview_manager.stop_all()

    def __enter__(self) -> 'GuidedWebServer':
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Iniciar interface web guiada local do UDH (B1.7 + B3.6).')
    parser.add_argument('--db', required=True, help='caminho do banco SQLite v4')
    parser.add_argument('--store', default=None, help='caminho da TrustStore (padrão XDG)')
    parser.add_argument('--port', type=int, default=0, help='porta local em 127.0.0.1 (padrão: 0 efêmera)')
    args = parser.parse_args(argv)

    store = SQLiteStore(args.db)
    core = CoreService(store)
    if not Path(args.db).exists():
        core.initialize(schema_version=4)
    trust = TrustStore(args.store or default_store_dir())

    server = GuidedWebServer(core_service=core, trust_store=trust, host='127.0.0.1', port=args.port)
    stop_event = threading.Event()

    def _handle_sig(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    url = server.start()
    print(f'Painel guiado UDH pronto em: {url}', flush=True)
    try:
        while not stop_event.is_set():
            stop_event.wait(0.5)
    finally:
        server.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
