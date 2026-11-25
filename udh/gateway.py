"""Gateway de inferência stdlib endurecido (UDS + SO_PEERCRED + leitor HTTP/1.1 direto)."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import struct
import tempfile
import threading
import time

from .domain import CoreError, strict_json_loads

DEFAULT_MAX_HEADER_BYTES = 16384
DEFAULT_MAX_BODY_BYTES = 262144
DEFAULT_HEADER_TIMEOUT = 5.0
DEFAULT_BODY_TIMEOUT = 10.0
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MAX_CALLS = 16
DEFAULT_MAX_RESPONSE_BYTES = 1048576
DEFAULT_CALL_TIMEOUT = 60.0
DEFAULT_STUB_MODEL = 'udh-stub-v1'
OPENROUTER_HOST = 'openrouter.ai'
OPENROUTER_MODELS_PATH = '/api/v1/models'
OPENROUTER_CHAT_PATH = '/api/v1/chat/completions'
OPENCODE_ZEN_HOST = 'opencode.ai'
OPENCODE_ZEN_MODELS_PATH = '/zen/v1/models'
OPENCODE_ZEN_CHAT_PATH = '/zen/v1/chat/completions'
OPENCODE_ZEN_PUBLIC_TOKEN = 'public'
MAX_UNIX_SOCKET_BYTES = 100


class GatewayError(CoreError):
    """Erro de configuração ou execução do Gateway de inferência."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class SecretWindowScanner:
    """Detecta vazamento de segredo mesmo quando fatiado entre chunks SSE."""

    def __init__(self, secret: str):
        self._secret_bytes = secret.encode('utf-8') if secret else b''
        self._tail = b''

    def contains_secret(self, chunk: bytes) -> bool:
        if not self._secret_bytes:
            return False
        window = self._tail + chunk
        if self._secret_bytes in window:
            return True
        keep = max(0, len(self._secret_bytes) - 1)
        self._tail = window[-keep:] if keep else b''
        return False


def _is_zero_decimal(raw_value) -> bool:
    if isinstance(raw_value, bool) or raw_value is None:
        return False
    try:
        dec = Decimal(str(raw_value))
    except (InvalidOperation, ValueError):
        return False
    return dec.is_finite() and dec == Decimal(0)


def _abort_http_connection(conn: http.client.HTTPConnection) -> None:
    sock = getattr(conn, 'sock', None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        conn.close()
    except OSError:
        pass


def list_opencode_zen_free_models(
    *,
    connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
    timeout_seconds: float = 15.0,
) -> list[str]:
    """Consulta https://opencode.ai/zen/v1/models (status 200 estrito) e retorna modelos -free verificados."""
    started = time.monotonic()
    conn = (
        connection_factory()
        if connection_factory is not None
        else http.client.HTTPSConnection(
            OPENCODE_ZEN_HOST, timeout=timeout_seconds, context=ssl.create_default_context(),
        )
    )
    timer = threading.Timer(timeout_seconds, _abort_http_connection, args=(conn,))
    timer.daemon = True
    timer.start()
    try:
        conn.request(
            'GET',
            OPENCODE_ZEN_MODELS_PATH,
            headers={
                'Accept': 'application/json',
                'Accept-Encoding': 'identity',
                'Connection': 'close',
            },
        )
        resp = conn.getresponse()
        if resp.status != 200:
            raise GatewayError(f'Catálogo OpenCode Zen retornou HTTP {resp.status}.')
        data = bytearray()
        deadline = started + timeout_seconds
        while len(data) <= 4194304:
            if time.monotonic() >= deadline:
                raise GatewayError('Timeout ao ler catálogo OpenCode Zen.')
            chunk = resp.read1(min(65536, 4194305 - len(data))) if hasattr(resp, 'read1') else resp.read(4194305 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > 4194304:
            raise GatewayError('Catálogo OpenCode Zen excedeu 4 MiB.')
        payload = strict_json_loads(data.decode('utf-8'), 'Catálogo OpenCode Zen')
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise GatewayError('Formato inesperado no catálogo OpenCode Zen.')
        free_models: set[str] = set()
        for entry in payload['data']:
            if not isinstance(entry, dict):
                continue
            mid = entry.get('id')
            if not isinstance(mid, str) or not mid.endswith('-free'):
                continue
            if 'pricing' in entry and entry['pricing'] is not None:
                pricing = entry['pricing']
                if not isinstance(pricing, dict) or not all(_is_zero_decimal(v) for v in pricing.values()):
                    continue
            free_models.add(mid)
        return sorted(free_models)
    except GatewayError:
        raise
    except (OSError, ValueError, UnicodeError, CoreError, http.client.HTTPException) as error:
        raise GatewayError('Falha ao consultar catálogo gratuito do OpenCode Zen.') from error
    finally:
        timer.cancel()
        conn.close()


def verify_opencode_zen_free_model(
    model_id: str,
    *,
    connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
    timeout_seconds: float = 15.0,
) -> bool:
    """Confirma sufixo -free e presença no catálogo OpenCode Zen sem cobrança declarada."""
    if not isinstance(model_id, str) or not model_id.endswith('-free'):
        return False
    try:
        return model_id in list_opencode_zen_free_models(
            connection_factory=connection_factory,
            timeout_seconds=timeout_seconds,
        )
    except GatewayError:
        return False


def verify_openrouter_free_model(
    model_id: str,
    *,
    connection_factory: Callable[[], http.client.HTTPConnection] | None = None,
) -> bool:
    """Confirma que o modelo existe no catálogo OpenRouter com preço zero em todos os campos."""
    if not isinstance(model_id, str) or not model_id.endswith(':free'):
        return False
    conn = (
        connection_factory()
        if connection_factory is not None
        else http.client.HTTPSConnection(OPENROUTER_HOST, timeout=15, context=ssl.create_default_context())
    )
    try:
        conn.request('GET', OPENROUTER_MODELS_PATH, headers={'Accept': 'application/json'})
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        raw = resp.read(4194305)
        if len(raw) > 4194304:
            return False
        payload = strict_json_loads(raw.decode('utf-8'), 'Catálogo OpenRouter')
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            return False
        entry = next((item for item in payload['data'] if isinstance(item, dict) and item.get('id') == model_id), None)
        if not isinstance(entry, dict):
            return False
        pricing = entry.get('pricing')
        if not isinstance(pricing, dict) or not pricing:
            return False
        if 'prompt' not in pricing or 'completion' not in pricing:
            return False
        return all(_is_zero_decimal(val) for val in pricing.values())
    except (OSError, ValueError, CoreError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _validate_tools_payload(
    body: dict,
    *,
    allow_tools: bool,
    allowed_tool_names: tuple[str, ...] = (),
) -> tuple[int | None, str | None, list[str], list[dict]]:
    """Valida estritamente tools/functions (fail-closed, sem strip silencioso)."""
    if 'functions' in body and body['functions'] is not None:
        return 403, 'tools_denied', [], []
    raw_tools = body.get('tools')
    if raw_tools is None:
        return None, None, [], []
    if not isinstance(raw_tools, list):
        return 400, 'tools_invalid', [], []
    if not raw_tools:
        return None, None, [], []
    if not allow_tools:
        names: list[str] = []
        for entry in raw_tools:
            if isinstance(entry, dict):
                fn = entry.get('function')
                if isinstance(fn, dict) and isinstance(fn.get('name'), str) and fn['name']:
                    names.append(fn['name'])
        return 403, 'tools_denied', sorted(set(names)), []

    allowed_set = set(allowed_tool_names)
    sanitized: list[dict] = []
    extracted_names: list[str] = []
    for entry in raw_tools:
        if not isinstance(entry, dict) or entry.get('type') != 'function':
            return 400, 'tools_invalid', [], []
        fn = entry.get('function')
        if not isinstance(fn, dict):
            return 400, 'tools_invalid', [], []
        name = fn.get('name')
        if not isinstance(name, str) or not name or name.strip() != name:
            return 400, 'tools_invalid', [], []
        extracted_names.append(name)
        rebuilt_fn: dict = {'name': name}
        if 'description' in fn and isinstance(fn['description'], str):
            rebuilt_fn['description'] = fn['description']
        if 'parameters' in fn and isinstance(fn['parameters'], dict):
            rebuilt_fn['parameters'] = fn['parameters']
        sanitized.append({'type': 'function', 'function': rebuilt_fn})

    unique_names = sorted(set(extracted_names))
    if not allowed_set or any(name not in allowed_set for name in unique_names):
        return 403, 'tools_denied', unique_names, []
    return None, None, unique_names, sanitized


def build_openrouter_payload(
    body: dict,
    *,
    allowed_model: str,
    max_tokens_cap: int = 1024,
    allow_tools: bool = False,
    allowed_tool_names: tuple[str, ...] = (),
) -> dict:
    """Reconstrói payload upstream por allowlist estrita (impede bypass de max_price/provider e sem strip silencioso)."""
    sanitized_tools: list[dict] = []
    if allow_tools:
        err_status, err_code, _tool_names, sanitized_tools = _validate_tools_payload(
            body, allow_tools=True, allowed_tool_names=allowed_tool_names,
        )
        if err_status is not None:
            raise GatewayError(f'Payload de ferramentas recusado ({err_status} {err_code}).')
    req_max = body.get('max_tokens')
    if type(req_max) is int and 1 <= req_max <= max_tokens_cap:
        max_tokens = req_max
    else:
        max_tokens = min(256, max_tokens_cap)
    rebuilt = {
        'model': allowed_model,
        'stream': True,
        'messages': body['messages'],
        'max_tokens': max_tokens,
        'provider': {'max_price': {'prompt': 0, 'completion': 0}},
    }
    if sanitized_tools:
        rebuilt['tools'] = sanitized_tools
    return rebuilt


@dataclass(frozen=True)
class GatewayConfig:
    run_id: str
    policy_sha256: str
    audit_path: Path
    backend: str = 'stub'
    allowed_models: tuple[str, ...] = (DEFAULT_STUB_MODEL,)
    allow_tools: bool = False
    allowed_tool_names: tuple[str, ...] = ()
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    max_calls: int = DEFAULT_MAX_CALLS
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    header_timeout_seconds: float = DEFAULT_HEADER_TIMEOUT
    body_timeout_seconds: float = DEFAULT_BODY_TIMEOUT
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT
    stub_response_text: str = 'UDH_CONFORMANCE_OK'
    stub_turns: tuple[dict, ...] = ()
    cut_after_first_chunk: bool = False
    secret_resolver: Callable[[], str | None] | None = None
    openrouter_connection_factory: Callable[[], http.client.HTTPConnection] | None = None
    opencode_zen_connection_factory: Callable[[], http.client.HTTPConnection] | None = None
    ipc_base_dir: Path | None = None
    local_discovery_port: int | None = None
    local_discovery_max_tokens: int = 2048

    def __post_init__(self):
        if not isinstance(self.allowed_tool_names, tuple):
            raise GatewayError('allowed_tool_names deve ser uma tupla imutável de nomes de ferramentas.')
        for tool_name in self.allowed_tool_names:
            if not isinstance(tool_name, str) or not tool_name or tool_name.strip() != tool_name:
                raise GatewayError('Nome inválido em allowed_tool_names.')


def _send_json_response(conn: socket.socket, status: int, reason: str, payload: dict) -> None:
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    header = (
        f'HTTP/1.1 {status} {reason}\r\n'
        f'Content-Type: application/json\r\n'
        f'Content-Length: {len(body)}\r\n'
        f'Connection: close\r\n\r\n'
    ).encode('ascii')
    try:
        conn.setblocking(False)
        try:
            conn.recv(4096)
        except OSError:
            pass
        conn.setblocking(True)
        conn.settimeout(0.5)
        conn.sendall(header + body)
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    except OSError:
        pass


def _read_http_request(
    conn: socket.socket,
    *,
    header_deadline: float,
    body_deadline: float,
    max_header_bytes: int,
    max_body_bytes: int,
    expected_token: str,
) -> tuple[dict | None, int | None, str | None, int]:
    """Lê requisição HTTP/1.1 direto sobre socket.recv() com deadlines absolutos."""
    buf = bytearray()
    header_end = -1
    while header_end == -1:
        remaining = header_deadline - time.monotonic()
        if remaining <= 0:
            return None, 408, 'request_header_timeout', len(buf)
        conn.settimeout(max(0.01, min(remaining, 0.5)))
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            return None, 400, 'connection_reset', len(buf)
        if not chunk:
            return None, 400, 'incomplete_headers', len(buf)
        buf.extend(chunk)
        header_end = buf.find(b'\r\n\r\n')
        if header_end == -1 and len(buf) > max_header_bytes:
            return None, 431, 'headers_too_large', len(buf)
        if header_end != -1 and header_end > max_header_bytes:
            return None, 431, 'headers_too_large', header_end

    raw_head = bytes(buf[:header_end])
    body_buf = bytearray(buf[header_end + 4:])
    try:
        head_text = raw_head.decode('iso-8859-1')
    except UnicodeDecodeError:
        return None, 400, 'invalid_headers', len(raw_head)

    lines = head_text.split('\r\n')
    if not lines or any('\n' in line or '\r' in line for line in lines):
        return None, 400, 'malformed_header', len(raw_head)
    parts = lines[0].split(' ')
    if len(parts) != 3 or parts[2] not in ('HTTP/1.1', 'HTTP/1.0'):
        return None, 400, 'invalid_request_line', len(raw_head)
    method, path, _version = parts

    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if ':' not in line:
            return None, 400, 'malformed_header', len(raw_head)
        name, value = line.split(':', 1)
        if not name or any(ch.isspace() for ch in name):
            return None, 400, 'malformed_header', len(raw_head)
        headers.setdefault(name.lower(), []).append(value.strip())

    if method != 'POST':
        return None, 403, 'method_denied', len(raw_head)
    if path != '/v1/chat/completions':
        return None, 403, 'route_denied', len(raw_head)

    auth_values = headers.get('authorization', [])
    if len(auth_values) != 1 or not auth_values[0].startswith('Bearer '):
        return None, 401, 'capability_denied', len(raw_head)
    presented_token = auth_values[0][7:]
    if not hmac.compare_digest(presented_token, expected_token):
        return None, 401, 'capability_denied', len(raw_head)

    purpose_values = headers.get('x-udh-purpose', [])
    if len(purpose_values) != 1 or purpose_values[0] != 'inference':
        return None, 403, 'purpose_denied', len(raw_head)

    if 'transfer-encoding' in headers:
        return None, 400, 'unsupported_framing', len(raw_head)
    cl_values = headers.get('content-length', [])
    if len(cl_values) != 1 or not re.fullmatch(r'[0-9]+', cl_values[0]):
        return None, 400, 'unsupported_framing', len(raw_head)
    content_length = int(cl_values[0])
    if content_length > max_body_bytes:
        return None, 413, 'size_denied', content_length

    while len(body_buf) < content_length:
        remaining = body_deadline - time.monotonic()
        if remaining <= 0:
            return None, 408, 'request_body_timeout', len(body_buf)
        conn.settimeout(max(0.01, min(remaining, 0.5)))
        try:
            chunk = conn.recv(min(8192, content_length - len(body_buf)))
        except socket.timeout:
            continue
        except OSError:
            return None, 400, 'incomplete_body', len(body_buf)
        if not chunk:
            return None, 400, 'incomplete_body', len(body_buf)
        body_buf.extend(chunk)

    raw_body = bytes(body_buf[:content_length])
    try:
        parsed = strict_json_loads(raw_body.decode('utf-8'), 'Corpo da requisição')
    except (UnicodeDecodeError, CoreError):
        return None, 400, 'invalid_body', len(raw_body)
    if not isinstance(parsed, dict):
        return None, 400, 'invalid_body', len(raw_body)
    return parsed, None, None, len(raw_body)


class _UDSGatewayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: str, gateway: 'InferenceGateway'):
        self.gateway = gateway
        super().__init__(server_address, _GatewayHandler)

    def process_request(self, request, client_address):
        if not self.gateway._slots.acquire(blocking=False):
            try:
                request.setblocking(False)
                try:
                    request.recv(4096)
                except OSError:
                    pass
                request.setblocking(True)
                request.settimeout(0.2)
                _send_json_response(request, 503, 'Service Unavailable', {'error': 'concurrency_limit_exceeded'})
                try:
                    request.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                self.gateway._audit(status=503, verdict='concurrency_limit_exceeded', peer_uid=None)
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.gateway._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.gateway._slots.release()


class _GatewayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.gateway._handle_connection(self.request)


class InferenceGateway:
    """Gateway de inferência UDS (função do Core, sem microserviço permanente)."""

    def __init__(self, config: GatewayConfig):
        if config.backend not in ('stub', 'openrouter_free', 'local_discovery', 'opencode_zen_free'):
            raise GatewayError(f'Backend de gateway inválido: {config.backend}.')
        if config.backend == 'local_discovery' and (
                type(config.local_discovery_port) is not int or not 1 <= config.local_discovery_port <= 65535
                or type(config.local_discovery_max_tokens) is not int or not 1 <= config.local_discovery_max_tokens <= 2048
                or config.allow_tools or config.allowed_tool_names or len(config.allowed_models) != 1):
            raise GatewayError('Inferência local exige porta válida, um modelo e nenhuma ferramenta.')
        if config.backend == 'opencode_zen_free' and (
                type(config.local_discovery_max_tokens) is not int or not 1 <= config.local_discovery_max_tokens <= 2048
                or config.allow_tools or config.allowed_tool_names or len(config.allowed_models) != 1
                or not isinstance(config.allowed_models[0], str) or not config.allowed_models[0].endswith('-free')):
            raise GatewayError('Backend opencode_zen_free exige um único modelo -free e nenhuma ferramenta.')
        if not config.allowed_models:
            raise GatewayError('Allowlist de modelos não pode ser vazia.')
        for tool_name in config.allowed_tool_names:
            if not isinstance(tool_name, str) or not tool_name or tool_name.strip() != tool_name:
                raise GatewayError('Nome inválido em allowed_tool_names.')
        self.config = config
        self.capability_token = secrets.token_urlsafe(32)
        self._slots = threading.BoundedSemaphore(config.max_concurrency)
        self._budget_lock = threading.Lock()
        self._audit_lock = threading.Lock()
        self._stub_turn_lock = threading.Lock()
        self._stub_remaining_turns: list[dict] = [dict(t) for t in config.stub_turns]
        self._calls = 0
        self._secret: str | None = None
        self.ipc_dir: Path | None = None
        self.socket_path: Path | None = None
        self._server: _UDSGatewayServer | None = None
        self._thread: threading.Thread | None = None
        self.first_chunk_sent = threading.Event()

    def _choose_ipc_dir(self) -> Path:
        base_candidates: list[Path] = []
        if self.config.ipc_base_dir is not None:
            base_candidates.append(Path(self.config.ipc_base_dir))
        else:
            xdg = os.environ.get('XDG_RUNTIME_DIR')
            if xdg and Path(xdg).is_dir():
                base_candidates.append(Path(xdg))
            base_candidates.append(Path('/tmp'))

        for base in base_candidates:
            candidate = Path(tempfile.mkdtemp(prefix='udh-ipc-', dir=str(base)))
            os.chmod(candidate, 0o700)
            sock_path = candidate / 'gateway.sock'
            if len(str(sock_path).encode('utf-8')) <= MAX_UNIX_SOCKET_BYTES:
                return candidate
            shutil.rmtree(candidate, ignore_errors=True)
        raise GatewayError('Não foi possível alocar caminho UDS curto (<= 100 bytes).')

    def start(self) -> Path:
        if self._server is not None:
            assert self.socket_path is not None
            return self.socket_path

        self.config.audit_path.parent.mkdir(parents=True, exist_ok=True)

        if self.config.backend == 'openrouter_free':
            if self.config.secret_resolver is None:
                raise GatewayError('Backend openrouter_free exige resolvedor explícito de credencial.')
            secret = self.config.secret_resolver()
            if not isinstance(secret, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]{8,1024}=*', secret):
                raise GatewayError('Credencial OpenRouter ausente ou inválida.')
            for model in self.config.allowed_models:
                if not verify_openrouter_free_model(
                    model, connection_factory=self.config.openrouter_connection_factory
                ):
                    raise GatewayError(f'Modelo {model} não comprovado como gratuito (:free, preço zero).')
            self._secret = secret
        elif self.config.backend == 'opencode_zen_free':
            if self.config.secret_resolver is None:
                raise GatewayError('Backend opencode_zen_free exige resolvedor explícito de credencial.')
            secret = self.config.secret_resolver()
            if not isinstance(secret, str) or (
                secret != OPENCODE_ZEN_PUBLIC_TOKEN
                and not re.fullmatch(r'[A-Za-z0-9._~+/-]{8,1024}=*', secret)
            ):
                raise GatewayError('Credencial OpenCode Zen ausente ou inválida.')
            cat_timeout = min(15.0, float(self.config.call_timeout_seconds))
            for model in self.config.allowed_models:
                if not verify_opencode_zen_free_model(
                    model,
                    connection_factory=self.config.opencode_zen_connection_factory,
                    timeout_seconds=cat_timeout,
                ):
                    raise GatewayError(f'Modelo {model} não comprovado como gratuito (-free) no catálogo OpenCode Zen.')
            self._secret = secret

        self.ipc_dir = self._choose_ipc_dir()
        self.socket_path = self.ipc_dir / 'gateway.sock'
        self._server = _UDSGatewayServer(str(self.socket_path), self)
        os.chmod(self.socket_path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self._thread.start()
        return self.socket_path

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self.ipc_dir is not None:
            shutil.rmtree(self.ipc_dir, ignore_errors=True)
            self.ipc_dir = None
            self.socket_path = None
        self._secret = None

    def __enter__(self) -> 'InferenceGateway':
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()

    def _audit(
        self,
        *,
        status: int,
        verdict: str,
        peer_uid: int | None,
        model: str | None = None,
        has_tools: bool = False,
        tool_names: list[str] | None = None,
        request_bytes: int = 0,
        response_bytes: int = 0,
        stream_events: int = 0,
        usage: dict | None = None,
        elapsed_seconds: float = 0.0,
    ) -> None:
        record = {
            'timestamp': _utc_now(),
            'run_id': self.config.run_id,
            'policy_sha256': self.config.policy_sha256,
            'purpose': 'inference',
            'backend': self.config.backend,
            'status': status,
            'verdict': verdict,
            'peer_uid': peer_uid,
            'model': model,
            'has_tools': has_tools,
            'tool_names': list(tool_names) if tool_names else [],
            'request_bytes': request_bytes,
            'response_bytes': response_bytes,
            'stream_events': stream_events,
            'usage': usage or {},
            'elapsed_seconds': round(elapsed_seconds, 4),
        }
        line = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n'
        with self._audit_lock:
            with self.config.audit_path.open('a', encoding='utf-8') as handle:
                handle.write(line)

    def _handle_connection(self, conn: socket.socket) -> None:
        started = time.monotonic()
        try:
            _pid, peer_uid, _gid = struct.unpack(
                '3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
        except OSError:
            _send_json_response(conn, 403, 'Forbidden', {'error': 'peer_denied'})
            self._audit(status=403, verdict='peer_denied', peer_uid=None, elapsed_seconds=time.monotonic() - started)
            return

        if peer_uid != os.getuid():
            _send_json_response(conn, 403, 'Forbidden', {'error': 'peer_denied'})
            self._audit(status=403, verdict='peer_denied', peer_uid=peer_uid, elapsed_seconds=time.monotonic() - started)
            return

        body, err_status, err_code, req_bytes = _read_http_request(
            conn,
            header_deadline=started + self.config.header_timeout_seconds,
            body_deadline=started + self.config.header_timeout_seconds + self.config.body_timeout_seconds,
            max_header_bytes=self.config.max_header_bytes,
            max_body_bytes=self.config.max_body_bytes,
            expected_token=self.capability_token,
        )
        if err_status is not None or body is None:
            status = err_status or 400
            verdict = err_code or 'invalid_body'
            self._audit(
                status=status, verdict=verdict, peer_uid=peer_uid,
                request_bytes=req_bytes, elapsed_seconds=time.monotonic() - started,
            )
            _send_json_response(conn, status, 'Error', {'error': verdict})
            return

        model = body.get('model')
        if not isinstance(model, str) or model not in self.config.allowed_models:
            _send_json_response(conn, 403, 'Forbidden', {'error': 'model_denied'})
            self._audit(
                status=403, verdict='model_denied', peer_uid=peer_uid,
                request_bytes=req_bytes, elapsed_seconds=time.monotonic() - started,
            )
            return
        if body.get('stream') is not True:
            _send_json_response(conn, 403, 'Forbidden', {'error': 'stream_required'})
            self._audit(
                status=403, verdict='stream_required', peer_uid=peer_uid, model=model,
                request_bytes=req_bytes, elapsed_seconds=time.monotonic() - started,
            )
            return
        if not isinstance(body.get('messages'), list):
            _send_json_response(conn, 400, 'Bad Request', {'error': 'messages_denied'})
            self._audit(
                status=400, verdict='messages_denied', peer_uid=peer_uid, model=model,
                request_bytes=req_bytes, elapsed_seconds=time.monotonic() - started,
            )
            return
        tools_err_status, tools_err_code, tool_names, _sanitized_tools = _validate_tools_payload(
            body,
            allow_tools=self.config.allow_tools,
            allowed_tool_names=self.config.allowed_tool_names,
        )
        has_tools = bool(tool_names) or bool(body.get('tools')) or ('functions' in body and body['functions'] is not None)
        if tools_err_status is not None:
            reason_phrase = 'Bad Request' if tools_err_status == 400 else 'Forbidden'
            _send_json_response(conn, tools_err_status, reason_phrase, {'error': tools_err_code})
            self._audit(
                status=tools_err_status, verdict=tools_err_code or 'tools_denied', peer_uid=peer_uid, model=model,
                has_tools=has_tools, tool_names=tool_names, request_bytes=req_bytes,
                elapsed_seconds=time.monotonic() - started,
            )
            return

        with self._budget_lock:
            if self._calls >= self.config.max_calls:
                _send_json_response(conn, 429, 'Too Many Requests', {'error': 'run_budget_exceeded'})
                self._audit(
                    status=429, verdict='run_budget_exceeded', peer_uid=peer_uid, model=model,
                    has_tools=has_tools, tool_names=tool_names, request_bytes=req_bytes,
                    elapsed_seconds=time.monotonic() - started,
                )
                return
            self._calls += 1

        if self.config.backend == 'stub':
            self._serve_stub(
                conn, peer_uid=peer_uid, model=model, has_tools=has_tools,
                tool_names=tool_names, req_bytes=req_bytes, started=started,
            )
        elif self.config.backend == 'local_discovery':
            self._serve_local_discovery(conn, body=body, peer_uid=peer_uid, model=model,
                                        req_bytes=req_bytes, started=started)
        elif self.config.backend == 'opencode_zen_free':
            self._serve_opencode_zen_free(conn, body=body, peer_uid=peer_uid, model=model,
                                          req_bytes=req_bytes, started=started)
        else:
            self._serve_openrouter_free(
                conn, body=body, peer_uid=peer_uid, model=model,
                has_tools=has_tools, tool_names=tool_names, req_bytes=req_bytes, started=started,
            )

    @staticmethod
    def _sanitize_discovery_messages(body: dict) -> list[dict] | None:
        messages: list[dict] = []
        for item in body['messages']:
            if (type(item) is not dict or item.get('role') not in ('system', 'user', 'assistant')
                    or 'tool_calls' in item or 'function_call' in item):
                return None
            text = item.get('content')
            if type(text) is list and text and all(
                type(part) is dict and set(part) == {'type', 'text'}
                and part['type'] == 'text' and type(part['text']) is str
                for part in text
            ):
                text = ''.join(part['text'] for part in text)
            if type(text) is not str:
                return None
            messages.append({'role': item['role'], 'content': text})
        return messages if messages and len(messages) == len(body['messages']) else None

    def _serve_opencode_zen_free(self, conn, *, body, peer_uid, model, req_bytes, started):
        assert self._secret is not None
        status, verdict, resp_bytes, usage = 400, 'zen_messages_denied', 0, {}
        messages = self._sanitize_discovery_messages(body)
        cap = self.config.local_discovery_max_tokens
        requested = body.get('max_completion_tokens', body.get('max_tokens', cap))
        if messages is None or type(requested) is not int or requested < 1:
            _send_json_response(conn, 400, 'Bad Request', {'error': verdict})
            self._audit(status=status, verdict=verdict, peer_uid=peer_uid, model=model,
                        request_bytes=req_bytes, elapsed_seconds=time.monotonic() - started)
            return

        payload = json.dumps(
            {'model': model, 'stream': False, 'max_tokens': min(requested, cap), 'messages': messages},
            separators=(',', ':'), ensure_ascii=False,
        ).encode('utf-8')
        upstream = (
            self.config.opencode_zen_connection_factory()
            if self.config.opencode_zen_connection_factory is not None
            else http.client.HTTPSConnection(
                OPENCODE_ZEN_HOST, timeout=self.config.call_timeout_seconds,
                context=ssl.create_default_context(),
            )
        )
        scan_secret = self._secret != OPENCODE_ZEN_PUBLIC_TOKEN
        raw_scanner = SecretWindowScanner(self._secret) if scan_secret else None
        timer = threading.Timer(self.config.call_timeout_seconds, _abort_http_connection, args=(upstream,))
        timer.daemon = True
        timer.start()
        status, verdict = 502, 'zen_inference_failed'
        try:
            try:
                upstream.request(
                    'POST',
                    OPENCODE_ZEN_CHAT_PATH,
                    body=payload,
                    headers={
                        'Authorization': 'Bearer ' + self._secret,
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'Accept-Encoding': 'identity',
                        'Connection': 'close',
                    },
                )
                response = upstream.getresponse()
                if response.status != 200:
                    verdict = f'provider_http_{response.status}'
                    _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                    return
                deadline = started + self.config.call_timeout_seconds
                data = bytearray()
                while len(data) <= self.config.max_response_bytes:
                    if time.monotonic() >= deadline:
                        verdict = 'response_budget_exceeded'
                        _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                        return
                    chunk_lim = min(8192, self.config.max_response_bytes - len(data) + 1)
                    chunk = response.read1(chunk_lim) if hasattr(response, 'read1') else response.read(chunk_lim)
                    if not chunk:
                        break
                    if raw_scanner is not None and raw_scanner.contains_secret(chunk):
                        verdict = 'secret_in_response_blocked'
                        _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                        return
                    data.extend(chunk)
                if len(data) > self.config.max_response_bytes:
                    verdict = 'response_budget_exceeded'
                    _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                    return
                parsed = strict_json_loads(data.decode('utf-8'), 'Resposta OpenCode Zen')
                if not isinstance(parsed, dict):
                    verdict = 'zen_invalid_response'
                    _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                    return
                if scan_secret:
                    parsed_scanner = SecretWindowScanner(self._secret)
                    if parsed_scanner.contains_secret(json.dumps(parsed, ensure_ascii=False).encode('utf-8')):
                        verdict = 'secret_in_response_blocked'
                        _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                        return
                cand_usage = parsed.get('usage')
                if isinstance(cand_usage, dict):
                    if 'cost' in cand_usage and cand_usage['cost'] is not None:
                        if not _is_zero_decimal(cand_usage['cost']):
                            verdict = 'nonzero_cost_blocked'
                            _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                            return
                    usage = {
                        k: cand_usage[k]
                        for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost')
                        if isinstance(cand_usage.get(k), (int, float)) and not isinstance(cand_usage.get(k), bool)
                    }
                choices = parsed.get('choices')
                if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
                    verdict = 'zen_invalid_response'
                    _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                    return
                choice = choices[0]
                message = choice.get('message')
                if (choice.get('finish_reason') != 'stop' or type(message) is not dict
                        or message.get('role') != 'assistant'
                        or type(message.get('content')) is not str or not message['content'].strip()
                        or message.get('function_call') is not None
                        or message.get('tool_calls') not in (None, [])):
                    verdict = 'zen_incomplete_or_tools'
                    _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                    return
            except (OSError, http.client.HTTPException, ValueError, UnicodeError, CoreError, RecursionError):
                _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                return
            finally:
                timer.cancel()
                upstream.close()

            # Encaminha exclusivamente message['content'] em SSE UTF-8 (nunca reasoning/reasoning_content).
            frames = []
            for delta, stop in (({'role': 'assistant', 'content': message['content']}, None), ({}, 'stop')):
                chunk_obj = {'id': 'udh-zen-chatcmpl', 'object': 'chat.completion.chunk', 'created': 1, 'model': model,
                             'choices': [{'index': 0, 'delta': delta, 'finish_reason': stop}]}
                frames.append(('data: ' + json.dumps(chunk_obj, separators=(',', ':'), ensure_ascii=False) + '\n\n').encode('utf-8'))
            frames.append(b'data: [DONE]\n\n')
            if sum(map(len, frames)) > self.config.max_response_bytes:
                verdict = 'response_budget_exceeded'
                _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                return
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n'
                         b'Connection: close\r\n\r\n')
            for frame in frames:
                resp_bytes += self._write_chunk(conn, frame)
            conn.sendall(b'0\r\n\r\n')
            status, verdict = 200, 'zen_stream_complete'
        except OSError:
            status, verdict = 499, 'client_disconnected'
        finally:
            self._audit(status=status, verdict=verdict, peer_uid=peer_uid, model=model,
                        request_bytes=req_bytes, response_bytes=resp_bytes, usage=usage,
                        elapsed_seconds=time.monotonic() - started)

    def _serve_local_discovery(self, conn, *, body, peer_uid, model, req_bytes, started):
        status, verdict, resp_bytes = 400, 'local_messages_denied', 0
        try:
            messages = []
            for item in body['messages']:
                if (type(item) is not dict or item.get('role') not in ('system', 'user', 'assistant')
                        or 'tool_calls' in item or 'function_call' in item):
                    break
                text = item.get('content')
                # O SDK do Pi envia partes de texto no turno de usuário.
                if type(text) is list and text and all(type(part) is dict and set(part) == {'type', 'text'}
                                                       and part['type'] == 'text' and type(part['text']) is str
                                                       for part in text):
                    text = ''.join(part['text'] for part in text)
                if type(text) is not str:
                    break
                messages.append({'role': item['role'], 'content': text})
            cap = self.config.local_discovery_max_tokens
            requested = body.get('max_completion_tokens', body.get('max_tokens', cap))
            if not messages or len(messages) != len(body['messages']) or type(requested) is not int or requested < 1:
                _send_json_response(conn, 400, 'Bad Request', {'error': verdict})
                return
            payload = json.dumps({'model': model, 'stream': False, 'max_tokens': min(requested, cap),
                                  'messages': messages})
            upstream = http.client.HTTPConnection('127.0.0.1', self.config.local_discovery_port,
                                                 timeout=self.config.call_timeout_seconds)
            status, verdict = 502, 'local_inference_failed'
            try:
                upstream.request('POST', '/v1/chat/completions', payload,
                                 {'Content-Type': 'application/json', 'Connection': 'close'})
                response = upstream.getresponse()
                if response.status != 200:
                    raise GatewayError('Servidor de inferência local recusou a chamada.')
                deadline = started + self.config.call_timeout_seconds
                data = bytearray()
                while len(data) <= self.config.max_response_bytes:
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    chunk = response.read1(min(8192, self.config.max_response_bytes - len(data) + 1))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > self.config.max_response_bytes:
                    raise GatewayError('Resposta local excedeu o limite.')
                parsed = strict_json_loads(data.decode('utf-8'), 'Resposta local')
                choices = parsed.get('choices') if type(parsed) is dict else None
                if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
                    raise GatewayError('Resposta local inválida.')
                choice = choices[0]
                message = choice.get('message')
                if (choice.get('finish_reason') != 'stop' or type(message) is not dict
                        or message.get('role') != 'assistant' or type(message.get('content')) is not str
                        or 'function_call' in message or message.get('tool_calls') not in (None, [])):
                    raise GatewayError('Resposta local incompleta ou com ferramentas.')
            except (OSError, http.client.HTTPException, ValueError, RecursionError):
                _send_json_response(conn, status, 'Bad Gateway', {'error': verdict})
                return
            finally:
                upstream.close()

            frames = []
            for delta, stop in (({'role': 'assistant', 'content': message['content']}, None), ({}, 'stop')):
                chunk = {'id': 'udh-local-chatcmpl', 'object': 'chat.completion.chunk', 'created': 1, 'model': model,
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': stop}]}
                frames.append(('data: ' + json.dumps(chunk, separators=(',', ':')) + '\n\n').encode('ascii'))
            frames.append(b'data: [DONE]\n\n')
            if sum(map(len, frames)) > self.config.max_response_bytes:
                _send_json_response(conn, status, 'Bad Gateway', {'error': verdict})
                return
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n'
                         b'Connection: close\r\n\r\n')
            for frame in frames:
                resp_bytes += self._write_chunk(conn, frame)
            conn.sendall(b'0\r\n\r\n')
            status, verdict = 200, 'local_stream_complete'
        except OSError:
            status, verdict = 499, 'client_disconnected'
        finally:
            self._audit(status=status, verdict=verdict, peer_uid=peer_uid, model=model,
                        request_bytes=req_bytes, response_bytes=resp_bytes, elapsed_seconds=time.monotonic() - started)

    def _write_chunk(self, conn: socket.socket, data: bytes) -> int:
        frame = f'{len(data):x}\r\n'.encode('ascii') + data + b'\r\n'
        conn.sendall(frame)
        return len(data)

    def _pop_stub_turn(self, *, has_tools: bool) -> dict | None:
        with self._stub_turn_lock:
            for idx, turn in enumerate(self._stub_remaining_turns):
                req_tools = turn.get('require_tools')
                if req_tools is None or bool(req_tools) == bool(has_tools):
                    return self._stub_remaining_turns.pop(idx)
        return None

    def _serve_stub(
        self,
        conn: socket.socket,
        *,
        peer_uid: int,
        model: str,
        has_tools: bool,
        tool_names: list[str],
        req_bytes: int,
        started: float,
    ) -> None:
        headers = (
            'HTTP/1.1 200 OK\r\n'
            'Content-Type: text/event-stream\r\n'
            'Transfer-Encoding: chunked\r\n'
            'Connection: close\r\n\r\n'
        ).encode('ascii')
        resp_bytes = 0
        events = 0
        verdict = 'stub_stream_complete'
        turn = self._pop_stub_turn(has_tools=has_tools)
        cut_after_first = bool(turn.get('cut_after_first_chunk')) if turn is not None else self.config.cut_after_first_chunk
        tool_calls = turn.get('tool_calls') if turn is not None else None
        text = str(turn.get('text', self.config.stub_response_text)) if turn is not None else self.config.stub_response_text
        try:
            conn.sendall(headers)

            def emit(delta: dict, finish_reason=None):
                nonlocal resp_bytes, events
                chunk_obj = {
                    'id': 'udh-stub-chatcmpl',
                    'object': 'chat.completion.chunk',
                    'created': 1,
                    'model': model,
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
                }
                payload = ('data: ' + json.dumps(chunk_obj, separators=(',', ':'), ensure_ascii=False) + '\n\n').encode('utf-8')
                resp_bytes += self._write_chunk(conn, payload)
                events += 1

            if isinstance(tool_calls, list) and tool_calls:
                emit({'role': 'assistant', 'content': ''})
                for idx, call in enumerate(tool_calls):
                    call_id = str(call.get('id') or f'call_udh_{idx + 1}')
                    fn_name = str(call['name'])
                    raw_args = call.get('arguments', {})
                    args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False)
                    emit({
                        'tool_calls': [{
                            'index': idx,
                            'id': call_id,
                            'type': 'function',
                            'function': {'name': fn_name, 'arguments': args_str},
                        }]
                    })
                self.first_chunk_sent.set()
                if cut_after_first:
                    verdict = 'stub_stream_cut'
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                emit({}, 'tool_calls')
                resp_bytes += self._write_chunk(conn, b'data: [DONE]\n\n')
                conn.sendall(b'0\r\n\r\n')
            else:
                midpoint = max(1, len(text) // 2)
                emit({'role': 'assistant', 'content': ''})
                emit({'content': text[:midpoint]})
                self.first_chunk_sent.set()

                if cut_after_first:
                    verdict = 'stub_stream_cut'
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

                emit({'content': text[midpoint:]})
                emit({}, 'stop')
                resp_bytes += self._write_chunk(conn, b'data: [DONE]\n\n')
                conn.sendall(b'0\r\n\r\n')
        except OSError:
            verdict = 'client_disconnected'
        finally:
            self._audit(
                status=200 if verdict == 'stub_stream_complete' else 499,
                verdict=verdict,
                peer_uid=peer_uid,
                model=model,
                has_tools=has_tools,
                tool_names=tool_names,
                request_bytes=req_bytes,
                response_bytes=resp_bytes,
                stream_events=events,
                usage={'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'cost': 0},
                elapsed_seconds=time.monotonic() - started,
            )

    def _serve_openrouter_free(
        self,
        conn: socket.socket,
        *,
        body: dict,
        peer_uid: int,
        model: str,
        has_tools: bool,
        tool_names: list[str],
        req_bytes: int,
        started: float,
    ) -> None:
        assert self._secret is not None
        upstream_payload = json.dumps(
            build_openrouter_payload(
                body,
                allowed_model=model,
                allow_tools=self.config.allow_tools,
                allowed_tool_names=self.config.allowed_tool_names,
            ),
            separators=(',', ':'), ensure_ascii=False,
        ).encode('utf-8')
        upstream = (
            self.config.openrouter_connection_factory()
            if self.config.openrouter_connection_factory is not None
            else http.client.HTTPSConnection(OPENROUTER_HOST, timeout=30, context=ssl.create_default_context())
        )
        scanner = SecretWindowScanner(self._secret)
        content_scanner = SecretWindowScanner(self._secret)
        sent_headers = False
        resp_bytes = 0
        events = 0
        usage: dict = {}
        verdict = 'upstream_incomplete'
        status_out = 502

        def _abort_upstream():
            sock = upstream.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                upstream.close()
            except OSError:
                pass

        timer = threading.Timer(self.config.call_timeout_seconds, _abort_upstream)
        timer.daemon = True
        timer.start()
        try:
            upstream.request(
                'POST', OPENROUTER_CHAT_PATH, body=upstream_payload,
                headers={
                    'Authorization': 'Bearer ' + self._secret,
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream',
                },
            )
            resp = upstream.getresponse()
            if resp.status != 200:
                verdict = f'provider_http_{resp.status}'
                _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                return
            if 'text/event-stream' not in resp.getheader('Content-Type', ''):
                verdict = 'provider_not_streaming'
                _send_json_response(conn, 502, 'Bad Gateway', {'error': verdict})
                return

            conn.sendall(
                b'HTTP/1.1 200 OK\r\n'
                b'Content-Type: text/event-stream\r\n'
                b'Transfer-Encoding: chunked\r\n'
                b'Connection: close\r\n\r\n'
            )
            sent_headers = True
            status_out = 200

            while True:
                line = resp.readline(65537)
                if not line:
                    break
                resp_bytes += len(line)
                if (len(line) > 65536 or resp_bytes > self.config.max_response_bytes
                        or time.monotonic() - started > self.config.call_timeout_seconds):
                    verdict = 'response_budget_exceeded'
                    break
                if scanner.contains_secret(line):
                    verdict = 'secret_in_response_blocked'
                    break
                if line.startswith(b'data: '):
                    value = line[6:].strip()
                    if value == b'[DONE]':
                        verdict = 'real_stream_complete'
                        self._write_chunk(conn, line + b'\n')
                        conn.sendall(b'0\r\n\r\n')
                        break
                    parsed = strict_json_loads(value.decode('utf-8'), 'Evento SSE upstream')
                    if not isinstance(parsed, dict):
                        verdict = 'provider_invalid_event'
                        break
                    if self._secret in json.dumps(parsed, ensure_ascii=False):
                        verdict = 'secret_in_response_blocked'
                        break
                    for choice in parsed.get('choices', []) if isinstance(parsed.get('choices'), list) else ():
                        if isinstance(choice, dict) and isinstance(choice.get('delta'), dict):
                            for text_key in ('content', 'reasoning', 'reasoning_content'):
                                piece = choice['delta'].get(text_key)
                                if isinstance(piece, str) and content_scanner.contains_secret(piece.encode('utf-8')):
                                    verdict = 'secret_in_response_blocked'
                                    break
                        if verdict == 'secret_in_response_blocked':
                            break
                    if verdict == 'secret_in_response_blocked':
                        break
                    if 'error' in parsed:
                        verdict = 'provider_stream_error'
                        break
                    cand_usage = parsed.get('usage')
                    if isinstance(cand_usage, dict):
                        if 'cost' in cand_usage and cand_usage['cost'] is not None:
                            if not _is_zero_decimal(cand_usage['cost']):
                                verdict = 'nonzero_cost_blocked'
                                break
                        usage = {
                            k: cand_usage[k]
                            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cost')
                            if isinstance(cand_usage.get(k), (int, float)) and not isinstance(cand_usage.get(k), bool)
                        }
                    events += 1
                self._write_chunk(conn, line)

            if verdict != 'real_stream_complete':
                status_out = 502
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        except (OSError, ValueError, CoreError, http.client.HTTPException):
            verdict = 'upstream_or_client_error'
            status_out = 502
            if sent_headers:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            else:
                _send_json_response(conn, 502, 'Bad Gateway', {'error': 'upstream_unavailable'})
        finally:
            timer.cancel()
            upstream.close()
            self._audit(
                status=status_out,
                verdict=verdict,
                peer_uid=peer_uid,
                model=model,
                has_tools=has_tools,
                tool_names=tool_names,
                request_bytes=req_bytes,
                response_bytes=resp_bytes,
                stream_events=events,
                usage=usage,
                elapsed_seconds=time.monotonic() - started,
            )
