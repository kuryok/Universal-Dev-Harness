"""Preview web local isolado (B3.6): descritor udh_app.json, UDS anti-TOCTOU e proxy endurecido."""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import struct
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .domain import CoreError, require_canonical_uuid, require_sha256, require_text, strict_json_loads
from .runtime import ArtifactPreviewProcess, DEFAULT_PREVIEW_LIMITS, ResourceLimits, RuntimeManager
from .trust_store import TrustStore, _read_regular_nofollow

PREVIEW_ID_RE = re.compile(r'^[0-9a-f]{24}$')
PREVIEW_HOST_RE = re.compile(r'^p-([0-9a-f]{24})\.localhost:([0-9]{1,5})$')
HTTP_HEADER_NAME_RE = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
ALLOWED_PROXY_METHODS = frozenset({'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'})
MUTATING_HTTP_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
PREVIEW_COOKIE_NAME = '_udh_preview_session'
RESERVED_AUTH_PATH = '/_udh_preview_auth'
DEMO_DATA_LABEL = 'dados de demonstração'

MAX_UDS_HEADER_BYTES = 32 * 1024
MAX_UDS_BODY_BYTES = 8 * 1024 * 1024
MAX_UDS_PATH_BYTES = 100
DEFAULT_GRANT_TTL_SECONDS = 60.0
DEFAULT_PREVIEW_TTL_SECONDS = 300.0
MAX_PREVIEW_TTL_SECONDS = 600.0

HOP_BY_HOP_HEADERS = frozenset({
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'proxy-connection',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
})

STRIPPED_RESPONSE_HEADERS = HOP_BY_HOP_HEADERS | frozenset({
    'content-length',
    'content-security-policy',
    'content-security-policy-report-only',
    'x-content-type-options',
    'referrer-policy',
    'permissions-policy',
    'cache-control',
    'pragma',
    'expires',
    'x-frame-options',
    'refresh',
    'set-cookie2',
    'access-control-allow-origin',
    'access-control-allow-credentials',
    'access-control-allow-headers',
    'access-control-allow-methods',
    'cross-origin-opener-policy',
    'cross-origin-embedder-policy',
    'cross-origin-resource-policy',
})

FORBIDDEN_FORWARD_REQ_HEADERS = HOP_BY_HOP_HEADERS | frozenset({
    'host',
    'content-length',
    'authorization',
    'x-udh-token',
})


class PreviewError(CoreError):
    """Erro de isolamento, contrato ou proxy do preview web local (B3.6)."""

    def __init__(self, message: str, *, reason_code: str = 'PREVIEW_ERROR', status_code: int = 400):
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class AppDescriptor:
    """Encaixe operacional validado de /artifact/udh_app.json (sem smoke_checks confiáveis)."""

    schema_version: int
    app_type: str
    entrypoint: str
    ready_path: str
    docs_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class UdsHttpResponse:
    status: int
    reason: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass
class PreviewSession:
    preview_id: str
    run_id: str
    artifact_digest: str
    hostname: str
    embed_mode: str
    data_label: str
    process: ArtifactPreviewProcess
    ipc_dir: Path
    data_dir: Path
    session_cookie_token: str
    grants: dict[str, float]
    created_at: str
    created_monotonic: float
    expires_monotonic: float
    last_access_monotonic: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _validate_rel_file_in_dir(base_dir: Path, rel_path: str, label: str, *, suffix: str | None = None) -> Path:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise PreviewError(f'{label} deve ser caminho relativo não vazio.', reason_code='INVALID_APP_DESCRIPTOR')
    if rel_path.startswith('/') or '\\' in rel_path or '\x00' in rel_path or '\r' in rel_path or '\n' in rel_path:
        raise PreviewError(f'{label} contém prefixo ou caractere proibido.', reason_code='INVALID_APP_DESCRIPTOR')
    parts = Path(rel_path).parts
    if not parts or '..' in parts or '.' in parts:
        raise PreviewError(f'{label} não pode conter traversal.', reason_code='INVALID_APP_DESCRIPTOR')
    if suffix is not None and not rel_path.endswith(suffix):
        raise PreviewError(f'{label} deve terminar com {suffix}.', reason_code='INVALID_APP_DESCRIPTOR')
    base = base_dir.resolve(strict=True)
    current = base
    for part in parts:
        current = current / part
        try:
            st = os.lstat(current)
        except OSError as exc:
            raise PreviewError(f'{label} ausente no artefato: {rel_path}.', reason_code='INVALID_APP_DESCRIPTOR') from exc
        if stat.S_ISLNK(st.st_mode):
            raise PreviewError(f'{label} contém symlink proibido: {rel_path}.', reason_code='INVALID_APP_DESCRIPTOR')
    st_final = os.lstat(current)
    if not stat.S_ISREG(st_final.st_mode):
        raise PreviewError(f'{label} deve ser arquivo regular: {rel_path}.', reason_code='INVALID_APP_DESCRIPTOR')
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(base):
        raise PreviewError(f'{label} escapa do artefato.', reason_code='INVALID_APP_DESCRIPTOR')
    return resolved


def validate_http_path(path: str, label: str = 'Caminho HTTP') -> str:
    if not isinstance(path, str) or not path.startswith('/'):
        raise PreviewError(f'{label} deve iniciar com "/".', reason_code='INVALID_HTTP_PATH')
    if path.startswith('//') or path.startswith('/\\') or any(ch in path for ch in ('\r', '\n', '\x00', ' ', '\t')):
        raise PreviewError(f'{label} contém sequência proibida.', reason_code='INVALID_HTTP_PATH')
    return path


def load_app_descriptor(artifact_dir: Path) -> AppDescriptor:
    """Lê e valida /artifact/udh_app.json (conteúdo não confiável); ignora qualquer smoke_checks do artefato."""
    base = Path(artifact_dir)
    if base.is_symlink() or not base.is_dir():
        raise PreviewError('Diretório do artefato inválido.', reason_code='INVALID_APP_DESCRIPTOR')
    desc_path = _validate_rel_file_in_dir(base, 'udh_app.json', 'Descritor udh_app.json')
    raw = _read_regular_nofollow(desc_path)
    parsed = strict_json_loads(raw.decode('utf-8'), 'Descritor udh_app.json')
    if not isinstance(parsed, dict):
        raise PreviewError('udh_app.json deve ser um objeto JSON.', reason_code='INVALID_APP_DESCRIPTOR')
    if parsed.get('schema_version') != 1:
        raise PreviewError('schema_version de udh_app.json não suportada (esperado 1).', reason_code='INVALID_APP_DESCRIPTOR')
    app_type = parsed.get('app_type')
    if app_type != 'web':
        raise PreviewError(f'app_type não suportado no preview web: {app_type!r}.', reason_code='UNSUPPORTED_APP_TYPE')

    entrypoint = parsed.get('entrypoint')
    _validate_rel_file_in_dir(base, entrypoint, 'entrypoint', suffix='.py')

    ready_path = validate_http_path(parsed.get('ready_path', ''), 'ready_path')

    raw_docs = parsed.get('docs_files', [])
    if not isinstance(raw_docs, list):
        raise PreviewError('docs_files deve ser uma lista de caminhos relativos.', reason_code='INVALID_APP_DESCRIPTOR')
    docs_files: list[str] = []
    for item in raw_docs:
        _validate_rel_file_in_dir(base, item, 'Arquivo de documentação (docs_files)')
        docs_files.append(item)

    # ADR-021/B3.4: qualquer campo smoke_checks dentro de /artifact/udh_app.json é deliberadamente ignorado.
    return AppDescriptor(
        schema_version=1,
        app_type='web',
        entrypoint=entrypoint,
        ready_path=ready_path,
        docs_files=tuple(docs_files),
    )


def allocate_preview_ipc_dir(prefix: str = 'udh-prev-') -> Path:
    """Aloca diretório 0700 sob XDG_RUNTIME_DIR ou /tmp garantindo socket <= 100 bytes UTF-8."""
    candidates: list[Path] = []
    xdg = os.environ.get('XDG_RUNTIME_DIR')
    if xdg:
        candidates.append(Path(xdg))
    candidates.append(Path('/tmp'))

    for parent in candidates:
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            continue
        candidate_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
        os.chmod(candidate_dir, 0o700)
        resolved_dir = candidate_dir.resolve(strict=True)
        sock_candidate = resolved_dir / 'preview.sock'
        if len(str(sock_candidate).encode('utf-8')) <= MAX_UDS_PATH_BYTES:
            return resolved_dir
        shutil.rmtree(candidate_dir, ignore_errors=True)
    raise PreviewError('Não foi possível alocar diretório IPC curto (<= 100 bytes) para preview.sock.',
                       reason_code='IPC_DIR_UNAVAILABLE', status_code=500)


def allocate_demo_data_dir(prefix: str = 'udh-demo-data-') -> Path:
    """Aloca volume descartável 0700 separado do IPC para dados de demonstração (B3.6(e))."""
    base = Path('/tmp')
    data_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=str(base)))
    os.chmod(data_dir, 0o700)
    return data_dir.resolve(strict=True)


def connect_preview_uds(
    ipc_dir: Path,
    *,
    timeout: float = 10.0,
    require_isolated_netns: bool = True,
) -> socket.socket:
    """Conecta ao preview.sock sem TOCTOU (O_DIRECTORY|O_NOFOLLOW + O_PATH|O_NOFOLLOW + SO_PEERCRED + ns/net)."""
    o_path = getattr(os, 'O_PATH', 0o10000000)
    try:
        dirfd = os.open(str(ipc_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PreviewError(f'Diretório IPC inválido ou symlink: {exc}',
                           reason_code='PREVIEW_UDS_INVALID', status_code=502) from exc

    conn: socket.socket | None = None
    try:
        dir_stat = os.fstat(dirfd)
        try:
            sock_fd = os.open('preview.sock', o_path | os.O_NOFOLLOW, dir_fd=dirfd)
        except OSError as exc:
            raise PreviewError(f'Socket preview.sock ausente ou symlink rejeitado: {exc}',
                               reason_code='PREVIEW_UDS_SYMLINK_OR_MISSING', status_code=502) from exc
        try:
            st = os.fstat(sock_fd)
            if stat.S_ISLNK(st.st_mode):
                raise PreviewError('Symlink proibido em preview.sock (TOCTOU bloqueado).',
                                   reason_code='PREVIEW_UDS_SYMLINK_OR_MISSING', status_code=502)
            if not stat.S_ISSOCK(st.st_mode):
                raise PreviewError('preview.sock não é um Unix Domain Socket.',
                                   reason_code='PREVIEW_UDS_INVALID', status_code=502)
            if st.st_uid != os.getuid():
                raise PreviewError('UID do preview.sock diverge do usuário atual.',
                                   reason_code='PREVIEW_UDS_UID_MISMATCH', status_code=502)
            if st.st_dev != dir_stat.st_dev:
                raise PreviewError('preview.sock reside em dispositivo distinto do diretório IPC.',
                                   reason_code='PREVIEW_UDS_DEV_MISMATCH', status_code=502)

            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.settimeout(timeout)
            conn.connect(f'/proc/self/fd/{sock_fd}')
        finally:
            os.close(sock_fd)
    except PreviewError:
        if conn is not None:
            conn.close()
        raise
    except OSError as exc:
        if conn is not None:
            conn.close()
        raise PreviewError(f'Falha ao conectar no preview.sock: {exc}',
                           reason_code='PREVIEW_UDS_CONNECT_FAILED', status_code=502) from exc
    finally:
        os.close(dirfd)

    assert conn is not None
    try:
        cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
        peer_pid, peer_uid, _peer_gid = struct.unpack('3i', cred)
        if peer_uid != os.getuid() or peer_pid <= 0:
            raise PreviewError('SO_PEERCRED inválido no socket de preview.',
                               reason_code='PREVIEW_PEERCRED_DENIED', status_code=502)
        if require_isolated_netns:
            host_netns = os.readlink('/proc/self/ns/net')
            peer_netns = os.readlink(f'/proc/{peer_pid}/ns/net')
            if peer_netns == host_netns:
                raise PreviewError('Par UDS compartilha namespace de rede do host (SSRF bloqueado).',
                                   reason_code='PREVIEW_NETNS_VIOLATION', status_code=502)
    except PreviewError:
        conn.close()
        raise
    except OSError as exc:
        conn.close()
        raise PreviewError(f'Não foi possível verificar SO_PEERCRED/ns/net do preview: {exc}',
                           reason_code='PREVIEW_NETNS_UNVERIFIED', status_code=502) from exc
    return conn


def _sanitize_request_cookie_header(raw_cookie: str) -> str:
    kept: list[str] = []
    for part in raw_cookie.split(';'):
        piece = part.strip()
        if not piece or '=' not in piece:
            continue
        name, _val = piece.split('=', 1)
        if name.strip().lower() == PREVIEW_COOKIE_NAME:
            continue
        kept.append(piece)
    return '; '.join(kept)


def _decode_chunked_body(initial: bytes, conn: socket.socket) -> bytes:
    buf = bytearray(initial)
    decoded = bytearray()

    def _ensure(min_bytes: int) -> None:
        while len(buf) < min_bytes:
            piece = conn.recv(16384)
            if not piece:
                raise PreviewError('Corpo chunked truncado na resposta do preview.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
            buf.extend(piece)
            if len(decoded) + len(buf) > MAX_UDS_BODY_BYTES + 65536:
                raise PreviewError('Resposta chunked excede o limite permitido.',
                                   reason_code='PREVIEW_RESPONSE_TOO_LARGE', status_code=502)

    while True:
        while b'\r\n' not in buf:
            piece = conn.recv(4096)
            if not piece:
                raise PreviewError('Cabeçalho de chunk truncado.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
            buf.extend(piece)
            if len(buf) > 4096:
                raise PreviewError('Linha de tamanho de chunk inválida.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        crlf = buf.index(b'\r\n')
        size_line = bytes(buf[:crlf]).split(b';', 1)[0].strip()
        del buf[:crlf + 2]
        try:
            chunk_size = int(size_line.decode('ascii'), 16)
        except (UnicodeDecodeError, ValueError) as exc:
            raise PreviewError('Tamanho de chunk hexadecimal inválido.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502) from exc
        if chunk_size < 0 or len(decoded) + chunk_size > MAX_UDS_BODY_BYTES:
            raise PreviewError('Corpo chunked excede o limite permitido.',
                               reason_code='PREVIEW_RESPONSE_TOO_LARGE', status_code=502)
        if chunk_size == 0:
            break
        _ensure(chunk_size + 2)
        decoded.extend(buf[:chunk_size])
        if bytes(buf[chunk_size:chunk_size + 2]) != b'\r\n':
            raise PreviewError('Delimitador CRLF ausente após chunk.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        del buf[:chunk_size + 2]
    return bytes(decoded)


def _read_http_response_from_uds(conn: socket.socket, *, method: str) -> UdsHttpResponse:
    buf = bytearray()
    while b'\r\n\r\n' not in buf:
        piece = conn.recv(4096)
        if not piece:
            raise PreviewError('Resposta HTTP do preview vazia ou truncada.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        buf.extend(piece)
        if len(buf) > MAX_UDS_HEADER_BYTES:
            raise PreviewError('Cabeçalhos da resposta do preview excedem o limite.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)

    sep = buf.index(b'\r\n\r\n')
    head_raw = bytes(buf[:sep])
    body_initial = bytes(buf[sep + 4:])

    lines = head_raw.split(b'\r\n')
    if not lines:
        raise PreviewError('Linha de status HTTP ausente.', reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
    try:
        status_line = lines[0].decode('latin-1')
    except UnicodeDecodeError as exc:
        raise PreviewError('Linha de status inválida.', reason_code='PREVIEW_BAD_UPSTREAM', status_code=502) from exc
    parts = status_line.split(' ', 2)
    if len(parts) < 2 or not parts[0].startswith('HTTP/1.'):
        raise PreviewError('Protocolo HTTP inválido na resposta do preview.',
                           reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
    try:
        status_code = int(parts[1])
    except ValueError as exc:
        raise PreviewError('Código de status HTTP inválido.',
                           reason_code='PREVIEW_BAD_UPSTREAM', status_code=502) from exc
    if status_code < 100 or status_code > 599:
        raise PreviewError('Código de status HTTP fora da faixa.',
                           reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
    reason = parts[2] if len(parts) == 3 else ''

    parsed_headers: list[tuple[str, str]] = []
    content_length: int | None = None
    is_chunked = False
    for raw_line in lines[1:]:
        if b':' not in raw_line:
            raise PreviewError('Cabeçalho malformado na resposta do preview.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        name_b, val_b = raw_line.split(b':', 1)
        if not HTTP_HEADER_NAME_RE.fullmatch(name_b):
            raise PreviewError('Nome de cabeçalho fora do padrão RFC 9110 na resposta do preview.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        name = name_b.decode('ascii')
        val = val_b.decode('latin-1').strip()
        if any((ord(ch) < 0x20 and ch != '\t') or ord(ch) == 0x7f for ch in val):
            raise PreviewError('Valor de cabeçalho com caractere de controle proibido na resposta do preview.',
                               reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        low = name.lower()
        if low == 'content-length':
            try:
                cl = int(val)
            except ValueError as exc:
                raise PreviewError('Content-Length inválido na resposta.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502) from exc
            if cl < 0 or cl > MAX_UDS_BODY_BYTES:
                raise PreviewError('Content-Length excede o limite permitido.',
                                   reason_code='PREVIEW_RESPONSE_TOO_LARGE', status_code=502)
            if content_length is not None and content_length != cl:
                raise PreviewError('Content-Length duplicado e conflitante.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
            content_length = cl
        elif low == 'transfer-encoding':
            if val.lower() == 'chunked':
                is_chunked = True
            else:
                raise PreviewError('Transfer-Encoding não suportado na resposta.',
                                   reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
        parsed_headers.append((name, val))

    if is_chunked and content_length is not None:
        raise PreviewError('Resposta ambígua com Transfer-Encoding e Content-Length.',
                           reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)

    if method == 'HEAD' or status_code in (204, 304) or (100 <= status_code < 200):
        body = b''
    elif is_chunked:
        body = _decode_chunked_body(body_initial, conn)
    elif content_length is not None:
        if len(body_initial) > content_length:
            body = body_initial[:content_length]
        else:
            chunks = [body_initial]
            total = len(body_initial)
            while total < content_length:
                piece = conn.recv(min(16384, content_length - total))
                if not piece:
                    raise PreviewError('Corpo HTTP truncado na resposta do preview.',
                                       reason_code='PREVIEW_BAD_UPSTREAM', status_code=502)
                chunks.append(piece)
                total += len(piece)
            body = b''.join(chunks)
    else:
        chunks = [body_initial]
        total = len(body_initial)
        while True:
            piece = conn.recv(16384)
            if not piece:
                break
            total += len(piece)
            if total > MAX_UDS_BODY_BYTES:
                raise PreviewError('Resposta do preview excede o limite de tamanho.',
                                   reason_code='PREVIEW_RESPONSE_TOO_LARGE', status_code=502)
            chunks.append(piece)
        body = b''.join(chunks)

    return UdsHttpResponse(status=status_code, reason=reason, headers=tuple(parsed_headers), body=body)


def uds_http_request(
    ipc_dir: Path,
    *,
    method: str,
    path: str,
    headers: Iterable[tuple[str, str]] = (),
    body: bytes = b'',
    timeout: float = 10.0,
    require_isolated_netns: bool = True,
) -> UdsHttpResponse:
    """Envia requisição HTTP/1.1 limpa sobre preview.sock endurecido (anti-TOCTOU + SO_PEERCRED/ns/net)."""
    method_up = method.upper()
    if method_up not in ALLOWED_PROXY_METHODS:
        raise PreviewError(f'Método HTTP não permitido no preview: {method_up}.',
                           reason_code='METHOD_NOT_ALLOWED', status_code=405)
    clean_path = validate_http_path(path, 'Caminho de requisição do preview')

    forward_headers: list[tuple[str, str]] = [('Host', '127.0.0.1'), ('Connection', 'close')]
    for name, val in headers:
        if any(ch in name for ch in ('\r', '\n', ':')) or '\r' in val or '\n' in val:
            continue
        low = name.lower().strip()
        if low in FORBIDDEN_FORWARD_REQ_HEADERS:
            continue
        if low == 'cookie':
            cleaned_cookie = _sanitize_request_cookie_header(val)
            if cleaned_cookie:
                forward_headers.append(('Cookie', cleaned_cookie))
            continue
        forward_headers.append((name.strip(), val.strip()))
    if body or method_up in ('POST', 'PUT', 'PATCH'):
        forward_headers.append(('Content-Length', str(len(body))))

    req_head = f'{method_up} {clean_path} HTTP/1.1\r\n' + ''.join(
        f'{k}: {v}\r\n' for k, v in forward_headers
    ) + '\r\n'
    payload = req_head.encode('latin-1') + body

    conn = connect_preview_uds(ipc_dir, timeout=timeout, require_isolated_netns=require_isolated_netns)
    with conn:
        conn.settimeout(timeout)
        conn.sendall(payload)
        return _read_http_response_from_uds(conn, method=method_up)


def _sanitize_app_set_cookie(raw_value: str) -> str | None:
    if not raw_value or '\r' in raw_value or '\n' in raw_value:
        return None
    parts = [p.strip() for p in raw_value.split(';') if p.strip()]
    if not parts or '=' not in parts[0]:
        return None
    cookie_name = parts[0].split('=', 1)[0].strip().lower()
    if not cookie_name or cookie_name == PREVIEW_COOKIE_NAME:
        return None
    filtered_attrs = [parts[0]]
    for attr in parts[1:]:
        attr_key = attr.split('=', 1)[0].strip().lower()
        if attr_key == 'domain':
            continue
        filtered_attrs.append(attr)
    return '; '.join(filtered_attrs)


def _is_safe_preview_location(location: str, preview_origin: str) -> bool:
    if not location or any(ord(ch) <= 0x20 or ord(ch) == 0x7f or ch == '\\' for ch in location):
        return False
    low = location.lower()
    if low.startswith('//') or low.startswith('javascript:') or low.startswith('data:'):
        return False
    if location.startswith('/'):
        return True
    parsed = urlsplit(location)
    if parsed.scheme in ('http', 'https'):
        target_origin = f'{parsed.scheme}://{parsed.netloc}'.lower()
        return target_origin == preview_origin.lower()
    return False


def harden_preview_response(
    resp: UdsHttpResponse,
    *,
    preview_origin: str,
    ui_origin: str,
    allow_iframe: bool,
) -> UdsHttpResponse:
    """Aplica CSP restritiva, sanitiza Set-Cookie/Location e bloqueia fuga de origem (B3.6)."""
    cleaned_headers: list[tuple[str, str]] = []
    for name, val in resp.headers:
        low = name.lower()
        if low in STRIPPED_RESPONSE_HEADERS:
            continue
        if low == 'set-cookie':
            sanitized = _sanitize_app_set_cookie(val)
            if sanitized is not None:
                cleaned_headers.append(('Set-Cookie', sanitized))
            continue
        if low == 'location' and 300 <= resp.status < 400:
            if not _is_safe_preview_location(val, preview_origin):
                err_body = b'Redirecionamento externo bloqueado pelo proxy isolado do UDH (preview_redirect_blocked).'
                return UdsHttpResponse(
                    status=502,
                    reason='Bad Gateway',
                    headers=(
                        ('Content-Type', 'text/plain; charset=utf-8'),
                        ('Content-Length', str(len(err_body))),
                        ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"),
                        ('X-Content-Type-Options', 'nosniff'),
                        ('Referrer-Policy', 'no-referrer'),
                        ('Cache-Control', 'no-store'),
                        ('Connection', 'close'),
                    ),
                    body=err_body,
                )
        cleaned_headers.append((name, val))

    frame_ancestors = ui_origin if allow_iframe else "'none'"
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        f"frame-ancestors {frame_ancestors}"
    )
    cleaned_headers.extend([
        ('Content-Security-Policy', csp),
        ('X-Content-Type-Options', 'nosniff'),
        ('Referrer-Policy', 'no-referrer'),
        ('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'),
        ('Cache-Control', 'no-store'),
        ('Content-Length', str(len(resp.body))),
        ('Connection', 'close'),
    ])
    return UdsHttpResponse(
        status=resp.status,
        reason=resp.reason,
        headers=tuple(cleaned_headers),
        body=resp.body,
    )


class PreviewManager:
    """Gerencia sessões de preview isolado (B3.6) no perfil artifact com limpeza garantida."""

    def __init__(
        self,
        *,
        trust_store: TrustStore,
        runtime_manager: RuntimeManager | None = None,
        require_isolated_netns: bool = True,
    ):
        self.trust_store = trust_store
        self.runtime_manager = runtime_manager or RuntimeManager()
        self.require_isolated_netns = require_isolated_netns
        self._lock = threading.Lock()
        self._sessions: dict[str, PreviewSession] = {}

    def _evict_expired_locked(self) -> list[PreviewSession]:
        now = time.monotonic()
        expired_ids = [
            pid for pid, sess in self._sessions.items()
            if now >= sess.expires_monotonic
        ]
        evicted = [self._sessions.pop(pid) for pid in expired_ids]
        return evicted

    @staticmethod
    def _cleanup_session_resources(session: PreviewSession) -> bool:
        cleanup_ok = False
        try:
            cleanup_ok = session.process.stop()
        finally:
            shutil.rmtree(session.ipc_dir, ignore_errors=True)
            shutil.rmtree(session.data_dir, ignore_errors=True)
        return cleanup_ok

    def cleanup_expired(self) -> int:
        with self._lock:
            evicted = self._evict_expired_locked()
        for sess in evicted:
            self._cleanup_session_resources(sess)
        return len(evicted)

    def start_preview(
        self,
        *,
        run_id: str,
        artifact_digest: str,
        embed_mode: str = 'tab',
        ttl_seconds: float = DEFAULT_PREVIEW_TTL_SECONDS,
        ready_timeout: float = 12.0,
        limits: ResourceLimits | None = None,
    ) -> dict:
        require_canonical_uuid(run_id, 'Identificador da execução')
        require_sha256(artifact_digest, 'Digest do artefato')
        if embed_mode not in ('tab', 'iframe'):
            raise PreviewError('embed_mode inválido; use "tab" ou "iframe".', reason_code='INVALID_EMBED_MODE')
        if ttl_seconds <= 0 or ttl_seconds > MAX_PREVIEW_TTL_SECONDS:
            raise PreviewError('TTL do preview fora da faixa permitida (1..600s).', reason_code='INVALID_TTL')

        self.cleanup_expired()

        artifact_dir, _manifest = self.trust_store.verify_artifact(artifact_digest)
        descriptor = load_app_descriptor(artifact_dir)

        preview_id = secrets.token_hex(12)
        hostname = f'p-{preview_id}.localhost'
        ipc_dir = allocate_preview_ipc_dir()
        data_dir = allocate_demo_data_dir()

        proc: ArtifactPreviewProcess | None = None
        try:
            effective_limits = limits or ResourceLimits(
                memory_mb=DEFAULT_PREVIEW_LIMITS.memory_mb,
                pids_limit=DEFAULT_PREVIEW_LIMITS.pids_limit,
                cpus=DEFAULT_PREVIEW_LIMITS.cpus,
                timeout_seconds=min(float(ttl_seconds), MAX_PREVIEW_TTL_SECONDS),
            )
            proc = self.runtime_manager.start_artifact_preview(
                artifact_dir=artifact_dir,
                data_dir=data_dir,
                ipc_dir=ipc_dir,
                entrypoint=descriptor.entrypoint,
                preview_mode='demo',
                limits=effective_limits,
                ready_timeout=ready_timeout,
                forbidden_rw_roots=(self.trust_store.root,),
            )
            # Valida prontidão HTTP pelo caminho real sobre o UDS endurecido antes de expor ao usuário
            ready_resp = uds_http_request(
                ipc_dir,
                method='GET',
                path=descriptor.ready_path,
                timeout=min(ready_timeout, 10.0),
                require_isolated_netns=self.require_isolated_netns,
            )
            if ready_resp.status < 200 or ready_resp.status >= 400:
                raise PreviewError(
                    f'App não respondeu prontidão saudável em {descriptor.ready_path} (HTTP {ready_resp.status}).',
                    reason_code='PREVIEW_NOT_READY',
                    status_code=502,
                )
        except BaseException:
            try:
                if proc is not None:
                    proc.stop()
            finally:
                shutil.rmtree(ipc_dir, ignore_errors=True)
                shutil.rmtree(data_dir, ignore_errors=True)
            raise

        now_mono = time.monotonic()
        grant = secrets.token_urlsafe(24)
        session = PreviewSession(
            preview_id=preview_id,
            run_id=run_id,
            artifact_digest=artifact_digest,
            hostname=hostname,
            embed_mode=embed_mode,
            data_label=DEMO_DATA_LABEL,
            process=proc,
            ipc_dir=ipc_dir,
            data_dir=data_dir,
            session_cookie_token=secrets.token_urlsafe(32),
            grants={grant: now_mono + DEFAULT_GRANT_TTL_SECONDS},
            created_at=_utc_now(),
            created_monotonic=now_mono,
            expires_monotonic=now_mono + ttl_seconds,
            last_access_monotonic=now_mono,
        )
        with self._lock:
            self._sessions[preview_id] = session

        return {
            'preview_id': preview_id,
            'run_id': run_id,
            'artifact_digest': artifact_digest,
            'hostname': hostname,
            'embed_mode': embed_mode,
            'data_label': DEMO_DATA_LABEL,
            'grant': grant,
            'created_at': session.created_at,
            'ttl_seconds': ttl_seconds,
        }

    def list_sessions_for_run(self, run_id: str) -> list[dict]:
        self.cleanup_expired()
        with self._lock:
            result = []
            for sess in self._sessions.values():
                if sess.run_id == run_id:
                    result.append({
                        'preview_id': sess.preview_id,
                        'run_id': sess.run_id,
                        'artifact_digest': sess.artifact_digest,
                        'hostname': sess.hostname,
                        'embed_mode': sess.embed_mode,
                        'data_label': sess.data_label,
                        'created_at': sess.created_at,
                    })
            return result

    def get_session(self, preview_id: str) -> PreviewSession | None:
        if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
            return None
        self.cleanup_expired()
        with self._lock:
            return self._sessions.get(preview_id)

    def issue_grant(self, preview_id: str) -> dict:
        """Emite novo grant one-time para preview ativo (suporta fallback limpo iframe -> aba separada)."""
        if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
            raise PreviewError('preview_id inválido.', reason_code='PREVIEW_NOT_FOUND', status_code=404)
        self.cleanup_expired()
        now_mono = time.monotonic()
        with self._lock:
            sess = self._sessions.get(preview_id)
            if sess is None:
                raise PreviewError('Preview não encontrado ou expirado.', reason_code='PREVIEW_NOT_FOUND', status_code=404)
            # Limpa grants expirados acumulados
            sess.grants = {g: exp for g, exp in sess.grants.items() if exp > now_mono}
            grant = secrets.token_urlsafe(24)
            sess.grants[grant] = now_mono + DEFAULT_GRANT_TTL_SECONDS
            return {
                'preview_id': sess.preview_id,
                'hostname': sess.hostname,
                'grant': grant,
                'data_label': sess.data_label,
                'embed_mode': sess.embed_mode,
            }

    def consume_grant(self, preview_id: str, grant: str) -> str | None:
        """Valida e consome grant one-time uma única vez, retornando o token de cookie host-only do preview."""
        if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
            return None
        if not isinstance(grant, str) or not grant:
            return None
        self.cleanup_expired()
        now_mono = time.monotonic()
        with self._lock:
            sess = self._sessions.get(preview_id)
            if sess is None:
                return None
            matched_key: str | None = None
            matched_exp = 0.0
            for candidate, exp in list(sess.grants.items()):
                if exp <= now_mono:
                    sess.grants.pop(candidate, None)
                    continue
                if hmac.compare_digest(candidate, grant):
                    matched_key = candidate
                    matched_exp = exp
            if matched_key is None or matched_exp <= now_mono:
                return None
            sess.grants.pop(matched_key, None)
            sess.last_access_monotonic = now_mono
            return sess.session_cookie_token

    def validate_session_cookie(self, preview_id: str, cookie_value: str | None) -> PreviewSession | None:
        if not cookie_value or not isinstance(cookie_value, str):
            return None
        sess = self.get_session(preview_id)
        if sess is None:
            return None
        if not hmac.compare_digest(sess.session_cookie_token, cookie_value):
            return None
        sess.last_access_monotonic = time.monotonic()
        return sess

    def proxy_request(
        self,
        session: PreviewSession,
        *,
        method: str,
        path: str,
        headers: Sequence[tuple[str, str]],
        body: bytes,
        ui_token: str,
        preview_origin: str,
        ui_origin: str,
    ) -> UdsHttpResponse:
        # B1.7(d): bloquear qualquer tentativa de enviar X-UDH-Token ou o valor do token da UI ao preview
        for name, val in headers:
            if name.lower().strip() == 'x-udh-token':
                raise PreviewError('X-UDH-Token é estritamente proibido no host de preview (B1.7(d)).',
                                   reason_code='UI_TOKEN_FORBIDDEN_IN_PREVIEW', status_code=403)
            if ui_token and ui_token in val:
                raise PreviewError('Token de sessão da UI nunca pode ser exposto ao preview (B1.7(d)).',
                                   reason_code='UI_TOKEN_FORBIDDEN_IN_PREVIEW', status_code=403)

        upstream_resp = uds_http_request(
            session.ipc_dir,
            method=method,
            path=path,
            headers=headers,
            body=body,
            timeout=15.0,
            require_isolated_netns=self.require_isolated_netns,
        )
        return harden_preview_response(
            upstream_resp,
            preview_origin=preview_origin,
            ui_origin=ui_origin,
            allow_iframe=(session.embed_mode == 'iframe'),
        )

    def stop_preview(self, preview_id: str) -> dict:
        if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
            raise PreviewError('preview_id inválido.', reason_code='PREVIEW_NOT_FOUND', status_code=404)
        with self._lock:
            sess = self._sessions.pop(preview_id, None)
        if sess is None:
            raise PreviewError('Preview não encontrado ou já encerrado.', reason_code='PREVIEW_NOT_FOUND', status_code=404)
        cleanup_ok = self._cleanup_session_resources(sess)
        return {
            'preview_id': preview_id,
            'stopped': True,
            'cleanup_verified': cleanup_ok,
            'data_discarded': not sess.data_dir.exists(),
            'ipc_removed': not sess.ipc_dir.exists(),
        }

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            self._cleanup_session_resources(sess)
