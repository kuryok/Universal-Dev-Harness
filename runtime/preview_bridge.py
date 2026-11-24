"""Mini-ponte reversa Python stdlib para o perfil artifact (--network=none).

Executa o app web promovido em processo-filho separado (127.0.0.1:<porta>)
e expõe um Unix Domain Socket em /preview-ipc/preview.sock (0600),
encaminhando requisições HTTP/1.1 sem abrir qualquer porta TCP no host (B3.6(d)).
"""
import argparse
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

MAX_HEADER_BYTES = 32 * 1024
MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 15.0
MAX_CONCURRENT_CONNS = 16


def _validate_entrypoint(artifact_dir: Path, rel_entrypoint: str) -> Path:
    if not isinstance(rel_entrypoint, str) or not rel_entrypoint.strip():
        raise ValueError('Entrypoint vazio.')
    if rel_entrypoint.startswith('/') or '\\' in rel_entrypoint or '\x00' in rel_entrypoint:
        raise ValueError('Entrypoint deve ser caminho relativo POSIX.')
    rel_path = Path(rel_entrypoint)
    if '..' in rel_path.parts or not rel_path.parts or not rel_entrypoint.endswith('.py'):
        raise ValueError('Entrypoint inválido; esperado arquivo .py relativo dentro de /artifact.')
    if artifact_dir.is_symlink():
        raise ValueError('Diretório de artefato não pode ser symlink.')
    base = artifact_dir.resolve(strict=True)
    current = base
    for part in rel_path.parts:
        current = current / part
        st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f'Symlink proibido no caminho do entrypoint: {part}.')
    st_final = os.lstat(current)
    if not stat.S_ISREG(st_final.st_mode):
        raise ValueError('Entrypoint deve ser arquivo regular.')
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(base):
        raise ValueError('Entrypoint escapa do diretório de artefato.')
    return resolved


def _wait_tcp_ready(port: int, child: subprocess.Popen, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f'Processo do app encerrou prematuramente com código {child.returncode}.')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f'App não ficou pronto em 127.0.0.1:{port} em {timeout_seconds}s.')


def _read_exact(conn: socket.socket, initial: bytes, needed: int) -> bytes:
    chunks = [initial]
    total = len(initial)
    while total < needed:
        piece = conn.recv(min(65536, needed - total))
        if not piece:
            raise ValueError('Conexão encerrada antes do fim do corpo HTTP.')
        chunks.append(piece)
        total += len(piece)
    return b''.join(chunks)


def _read_http_request(conn: socket.socket) -> bytes | None:
    conn.settimeout(SOCKET_TIMEOUT_SECONDS)
    buf = bytearray()
    while b'\r\n\r\n' not in buf:
        piece = conn.recv(4096)
        if not piece:
            return None
        buf.extend(piece)
        if len(buf) > MAX_HEADER_BYTES:
            raise ValueError('Cabeçalho HTTP excede o limite.')

    sep = buf.index(b'\r\n\r\n')
    head_bytes = bytes(buf[:sep])
    body_start = bytes(buf[sep + 4:])

    lines = head_bytes.split(b'\r\n')
    if not lines or len(lines[0].split(b' ')) != 3:
        raise ValueError('Linha de requisição HTTP inválida.')
    request_line = lines[0]

    content_length: int | None = None
    rebuilt_headers: list[bytes] = []
    for raw_line in lines[1:]:
        if b':' not in raw_line:
            raise ValueError('Cabeçalho HTTP malformado.')
        name_b, val_b = raw_line.split(b':', 1)
        name_low = name_b.strip().lower()
        val_clean = val_b.strip()
        if name_low == b'transfer-encoding':
            raise ValueError('Transfer-Encoding na requisição não suportado pela ponte.')
        if name_low == b'content-length':
            if content_length is not None:
                raise ValueError('Content-Length duplicado.')
            try:
                content_length = int(val_clean.decode('ascii'))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError('Content-Length inválido.') from exc
            if content_length < 0 or content_length > MAX_REQUEST_BODY_BYTES:
                raise ValueError('Corpo HTTP excede limite permitido.')
        if name_low in (b'connection', b'keep-alive', b'proxy-connection'):
            continue
        rebuilt_headers.append(name_b.strip() + b': ' + val_clean)

    rebuilt_headers.append(b'Connection: close')
    expected_len = content_length or 0
    if len(body_start) > expected_len:
        body = body_start[:expected_len]
    else:
        body = _read_exact(conn, body_start, expected_len)

    return request_line + b'\r\n' + b'\r\n'.join(rebuilt_headers) + b'\r\n\r\n' + body


def _send_bridge_error(conn: socket.socket, status: int, reason: str, message: str) -> None:
    body = message.encode('utf-8')
    payload = (
        f'HTTP/1.1 {status} {reason}\r\n'
        'Content-Type: text/plain; charset=utf-8\r\n'
        f'Content-Length: {len(body)}\r\n'
        'Connection: close\r\n\r\n'
    ).encode('ascii') + body
    try:
        conn.sendall(payload)
    except OSError:
        pass


def _handle_client(client: socket.socket, internal_port: int, sem: threading.Semaphore) -> None:
    try:
        try:
            raw_req = _read_http_request(client)
        except (ValueError, TimeoutError, socket.timeout, OSError) as exc:
            _send_bridge_error(client, 400, 'Bad Request', f'Requisicao invalida na ponte: {exc}')
            return
        if raw_req is None:
            return

        try:
            upstream = socket.create_connection(('127.0.0.1', internal_port), timeout=5.0)
        except OSError:
            _send_bridge_error(client, 502, 'Bad Gateway', 'App indisponivel no namespace interno.')
            return

        with upstream:
            upstream.settimeout(SOCKET_TIMEOUT_SECONDS)
            upstream.sendall(raw_req)
            total_relayed = 0
            while True:
                chunk = upstream.recv(16384)
                if not chunk:
                    break
                total_relayed += len(chunk)
                if total_relayed > MAX_RESPONSE_BYTES:
                    break
                client.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass
        sem.release()


def _terminate_child(child: subprocess.Popen | None) -> None:
    if child is None:
        return
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Ponte reversa UDS <-> loopback interno do perfil artifact.')
    parser.add_argument('--socket', required=True, dest='socket_path')
    parser.add_argument('--artifact', required=True, dest='artifact_dir')
    parser.add_argument('--data', required=True, dest='data_dir')
    parser.add_argument('--entrypoint', required=True)
    parser.add_argument('--internal-port', type=int, default=8000)
    parser.add_argument('--ready-timeout', type=float, default=10.0)
    args = parser.parse_args(argv)

    if args.internal_port < 1024 or args.internal_port > 65535:
        print('Porta interna fora da faixa 1024..65535.', file=sys.stderr)
        return 2

    artifact_dir = Path(args.artifact_dir)
    data_dir = Path(args.data_dir)
    socket_path = Path(args.socket_path)

    try:
        entrypoint_abs = _validate_entrypoint(artifact_dir, args.entrypoint)
    except (ValueError, OSError) as exc:
        print(f'Entrypoint rejeitado: {exc}', file=sys.stderr)
        return 2

    child_env = {
        'PATH': '/usr/bin:/bin',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1',
        'HOST': '127.0.0.1',
        'PORT': str(args.internal_port),
        'UDH_ARTIFACT_DIR': str(artifact_dir.resolve()),
        'UDH_DATA_DIR': str(data_dir.resolve()),
        'HOME': str(data_dir.resolve()),
        'TMPDIR': str(data_dir.resolve()),
        'UDH_PREVIEW_MODE': os.environ.get('UDH_PREVIEW_MODE', 'demo'),
    }

    stop_event = threading.Event()
    server_sock: socket.socket | None = None
    child: subprocess.Popen | None = None

    def _on_signal(_signum, _frame):
        stop_event.set()
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        child = subprocess.Popen(
            ['python3', '-I', '-B', str(entrypoint_abs)],
            cwd=str(data_dir.resolve()),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=sys.stderr,
            stderr=sys.stderr,
            start_new_session=True,
        )
        _wait_tcp_ready(args.internal_port, child, args.ready_timeout)

        tmp_socket_path = socket_path.parent / f'.{socket_path.name}.tmp.{os.getpid()}'
        for candidate in (tmp_socket_path, socket_path):
            if candidate.exists() or candidate.is_symlink():
                candidate.unlink()
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(str(tmp_socket_path))
        os.chmod(tmp_socket_path, 0o600)
        server_sock.listen(64)
        os.replace(tmp_socket_path, socket_path)
        server_sock.settimeout(0.5)

        sem = threading.Semaphore(MAX_CONCURRENT_CONNS)
        while not stop_event.is_set():
            if child.poll() is not None:
                print(f'App encerrou inesperadamente (rc={child.returncode}).', file=sys.stderr)
                return 1
            try:
                client, _ = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop_event.is_set():
                    break
                raise
            if not sem.acquire(blocking=False):
                _send_bridge_error(client, 503, 'Service Unavailable', 'Limite de conexoes da ponte atingido.')
                try:
                    client.close()
                except OSError:
                    pass
                continue
            worker = threading.Thread(
                target=_handle_client,
                args=(client, args.internal_port, sem),
                daemon=True,
            )
            worker.start()
        return 0
    except Exception as exc:
        print(f'Falha na ponte de preview: {exc}', file=sys.stderr)
        return 1
    finally:
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError:
                pass
        _terminate_child(child)
        try:
            if socket_path.exists() or socket_path.is_symlink():
                socket_path.unlink()
        except OSError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
