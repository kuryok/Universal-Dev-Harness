// Ensaios executados no mesmo perfil de runtime do Pi, sem rede externa disponível.
import net from 'node:net';
import dgram from 'node:dgram';
import dns from 'node:dns';
const results = [];
async function tcpControl() {
  const server = net.createServer(socket => socket.on('data', data => {
    results.push({case: 'tcp-loopback-control', received: data.toString()});
    socket.end();
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await new Promise((resolve, reject) => {
    const client = net.connect(server.address().port, '127.0.0.1', () => client.end('UDH_CONTROL'));
    client.on('close', resolve); client.on('error', reject);
  });
  await new Promise(resolve => server.close(resolve));
}
async function udpControl() {
  const server = dgram.createSocket('udp4');
  await new Promise(resolve => server.bind(0, '127.0.0.1', resolve));
  const received = new Promise(resolve => server.once('message', data => {
    results.push({case: 'udp-loopback-control', received: data.toString()}); resolve();
  }));
  server.send('UDH_CONTROL', server.address().port, '127.0.0.1');
  await received;
  server.close();
}
async function tcpProbe(host, port) {
  await new Promise(resolve => {
    const client = net.connect({host, port});
    client.setTimeout(1000);
    let done = false;
    function finish(result) {
      if (done) return; done = true;
      results.push({case: 'tcp', host, port, result}); client.destroy(); resolve();
    }
    client.once('connect', () => finish('UNEXPECTED_CONNECTION'));
    client.once('error', error => finish(error.code));
    client.once('timeout', () => finish('TIMEOUT'));
  });
}
async function udpProbe(type, host, port) {
  await new Promise(resolve => {
    const client = dgram.createSocket(type);
    let done = false;
    function finish(result) {
      if (done) return; done = true;
      results.push({case: type, host, port, result}); client.close(); resolve();
    }
    client.once('error', error => finish(error.code));
    client.send('UDH_SYNTHETIC', port, host, error => finish(error ? error.code : 'SEND_ACCEPTED_NOT_DELIVERY'));
  });
}
await tcpControl();
await udpControl();
for (const host of ['192.0.2.1', '2001:db8::1']) {
  for (const port of [53, 80, 443, 8443]) await tcpProbe(host, port);
}
await udpProbe('udp4', '192.0.2.1', 53);
await udpProbe('udp6', '2001:db8::1', 53);
const resolver = new dns.Resolver({timeout: 500, tries: 1});
await new Promise(resolve => resolver.resolve4('udh-spike.invalid', (error, addresses) => {
  results.push({case: 'dns', result: error ? error.code : addresses}); resolve();
}));
console.log(JSON.stringify(results));
