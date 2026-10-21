"""Compara receptores controlados no host com tentativas no namespace isolado."""
import json
from pathlib import Path
import socket
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parent
IMAGE = 'docker.io/library/alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc'
MARKER = b'UDH_SYNTHETIC_NETWORK_PROBE\n'
results = []
for family, address in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
    for kind, protocol in ((socket.SOCK_STREAM, 'tcp'), (socket.SOCK_DGRAM, 'udp')):
        row = {'family': family.name, 'protocol': protocol, 'address': address}
        received = []
        with socket.socket(family, kind) as server:
            server.settimeout(3)
            try:
                server.bind((address, 0))
            except OSError as error:
                row.update(status='não executado', error=str(error))
                results.append(row)
                continue
            port = server.getsockname()[1]
            row['port'] = port
            if kind == socket.SOCK_STREAM:
                server.listen(2)

            def receive():
                """Observa os bytes, sem inferir entrega pelo retorno do cliente."""
                try:
                    if kind == socket.SOCK_STREAM:
                        connection, _ = server.accept()
                        with connection:
                            connection.settimeout(3)
                            chunks = bytearray()
                            while len(chunks) < len(MARKER):
                                chunk = connection.recv(256)
                                if not chunk:
                                    break
                                chunks.extend(chunk)
                            received.append(chunks.decode())
                    else:
                        received.append(server.recvfrom(256)[0].decode())
                except socket.timeout:
                    pass

            # Controle positivo: o receptor realmente recebe o mesmo marcador no host.
            control = threading.Thread(target=receive)
            control.start()
            with socket.socket(family, kind) as client:
                client.settimeout(2)
                if kind == socket.SOCK_STREAM:
                    client.connect((address, port))
                    client.sendall(MARKER)
                else:
                    client.sendto(MARKER, (address, port))
            control.join(4)
            row['host_control_received'] = received.copy()
            received.clear()
            name = 'udh-slice0-network-' + uuid.uuid4().hex
            cidfile = ROOT / 'evidence' / (name + '.cid')
            args = ['podman', 'run', '--name', name, '--cidfile', str(cidfile),
                    '--pull=never', '--network=none',
                    '--read-only', '--read-only-tmpfs=false', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', '--userns=keep-id',
                    '--memory=64m', '--pids-limit=32', IMAGE, 'nc', '-v', '-w', '1']
            if kind == socket.SOCK_DGRAM:
                args.append('-u')
            args.extend([address, str(port)])
            try:
                attempt = subprocess.run(args, input=MARKER, capture_output=True, timeout=5)
                row.update(args=args, returncode=attempt.returncode,
                           stdout=attempt.stdout.decode(), stderr=attempt.stderr.decode())
            except subprocess.TimeoutExpired:
                row.update(args=args, returncode=None, stderr='Tempo limite excedido')
            finally:
                if cidfile.exists():
                    container_id = cidfile.read_text().strip()
                    if len(container_id) != 64 or any(c not in '0123456789abcdef' for c in container_id):
                        raise RuntimeError('ID inválido; remoção recusada')
                    cleanup = subprocess.run(['podman', 'rm', '-f', container_id],
                                             capture_output=True, text=True, timeout=10)
                    row['cleanup_returncode'] = cleanup.returncode
                    if cleanup.returncode != 0:
                        raise RuntimeError('Limpeza falhou; cidfile preservado')
                    cidfile.unlink(missing_ok=True)
            # O socket já estava ouvindo; pacotes recebidos ficam na fila do kernel.
            # A janela adicional começa após a tentativa, sem consumir o tempo de startup.
            receive()
            row['container_received_on_host'] = received.copy()
            row['status'] = 'parcial: interpretar diagnóstico e cobertura'
            results.append(row)
            print(family.name, protocol, row['status'], flush=True)
(ROOT / 'evidence/network-probe.json').write_text(
    json.dumps(results, ensure_ascii=False, indent=2) + '\n')
