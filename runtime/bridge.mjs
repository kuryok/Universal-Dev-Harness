// Ponte defensiva HTTP loopback (127.0.0.1) -> Unix Domain Socket (/ipc/gateway.sock).
// A autoridade real reside em udh/gateway.py (domínio confiável do Core).
import http from 'node:http';

const port = Number(process.env.UDH_BRIDGE_PORT || 43127);
const socketPath = process.env.UDH_GATEWAY_SOCKET || '/ipc/gateway.sock';
const timeoutMs = Number(process.env.UDH_GATEWAY_TIMEOUT_MS || 15000);

if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('Porta da ponte inválida');
}
if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 120000) {
  throw new Error('Tempo limite do gateway inválido');
}

function reject(res, status, code) {
  const payload = JSON.stringify({ error: code });
  if (!res.headersSent) {
    res.writeHead(status, {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload),
      'Connection': 'close',
    });
  }
  res.end(payload);
}

const server = http.createServer((incoming, outgoing) => {
  if (incoming.method !== 'POST') {
    return reject(outgoing, 403, 'method_denied');
  }
  if (incoming.url !== '/v1/chat/completions') {
    return reject(outgoing, 403, 'route_denied');
  }

  const rawHeaders = incoming.rawHeaders || [];
  let purposeCount = 0;
  let contentLengthCount = 0;
  let hasTransferEncoding = false;
  for (let i = 0; i < rawHeaders.length; i += 2) {
    const name = String(rawHeaders[i]).toLowerCase();
    if (name === 'x-udh-purpose') purposeCount += 1;
    if (name === 'content-length') contentLengthCount += 1;
    if (name === 'transfer-encoding') hasTransferEncoding = true;
  }

  if (purposeCount > 1) {
    return reject(outgoing, 403, 'purpose_denied');
  }
  const purpose = incoming.headers['x-udh-purpose'];
  if (purpose !== undefined && purpose !== 'inference') {
    return reject(outgoing, 403, 'purpose_denied');
  }
  if (hasTransferEncoding || contentLengthCount !== 1) {
    return reject(outgoing, 400, 'unsupported_framing');
  }
  const lengthVal = Number(incoming.headers['content-length']);
  if (!Number.isInteger(lengthVal) || lengthVal < 0 || lengthVal > 262144) {
    return reject(outgoing, lengthVal > 262144 ? 413 : 400, lengthVal > 262144 ? 'size_denied' : 'unsupported_framing');
  }

  const headers = {
    ...incoming.headers,
    'x-udh-purpose': 'inference',
    'connection': 'close',
  };

  const upstream = http.request(
    {
      socketPath,
      method: incoming.method,
      path: incoming.url,
      headers,
      timeout: timeoutMs,
    },
    (response) => {
      outgoing.writeHead(response.statusCode || 502, {
        ...response.headers,
        connection: 'close',
      });
      response.pipe(outgoing);
      response.on('aborted', () => outgoing.destroy());
      response.on('error', () => outgoing.destroy());
    },
  );

  upstream.on('timeout', () => upstream.destroy(new Error('Tempo limite do mediador')));
  upstream.on('error', () => {
    if (!outgoing.headersSent) {
      reject(outgoing, 502, 'mediator_unavailable');
    } else {
      outgoing.destroy();
    }
  });
  incoming.on('aborted', () => upstream.destroy());
  incoming.on('error', () => upstream.destroy());
  incoming.pipe(upstream);
});

server.headersTimeout = 5000;
server.requestTimeout = timeoutMs;
server.keepAliveTimeout = 1000;

server.listen(port, '127.0.0.1', () => {
  process.stdout.write('BRIDGE_READY\n');
});
