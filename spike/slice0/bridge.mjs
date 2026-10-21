// Ponte descartável: HTTP apenas no loopback do namespace, encaminhado ao UDS.
import http from 'node:http';
const timeout = Number(process.env.UDH_GATEWAY_TIMEOUT_MS || 4000);
if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 65000) {
  throw new Error('Tempo limite do gateway inválido');
}
const server = http.createServer((incoming, outgoing) => {
  if (incoming.headers['x-udh-purpose'] && incoming.headers['x-udh-purpose'] !== 'inference') {
    outgoing.writeHead(403);
    outgoing.end('Finalidade recusada');
    return;
  }
  const upstream = http.request({
    socketPath: '/ipc/gateway.sock', method: incoming.method,
    path: incoming.url, headers: {...incoming.headers, 'x-udh-purpose': 'inference'},
    timeout,
  }, response => {
    outgoing.writeHead(response.statusCode, response.headers);
    response.pipe(outgoing);
    response.on('aborted', () => outgoing.destroy());
    response.on('error', () => outgoing.destroy());
  });
  upstream.on('timeout', () => upstream.destroy(new Error('Tempo limite do mediador')));
  upstream.on('error', () => {
    if (!outgoing.headersSent) outgoing.writeHead(502);
    outgoing.end('Mediador indisponível');
  });
  incoming.on('aborted', () => upstream.destroy());
  incoming.pipe(upstream);
});
server.listen(43127, '127.0.0.1', () => console.log('BRIDGE_READY'));
