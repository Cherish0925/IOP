import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {getEventListeners} from 'node:events';
import {IOP, DataStream} from '../dist/index.js';

async function serving(handler, use) {
  const sockets = new Set();
  const server = createServer(handler);
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try { await use('http://127.0.0.1:' + server.address().port); }
  finally {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
  }
}

test('caller cancellation retains deadlines for JSON, uploads and streamed file bodies', {timeout:10000}, async () => {
  const paths = [];
  await serving((req, res) => {
    paths.push(req.url);
    req.resume();
    res.writeHead(200, {'Content-Type':'application/json'});
    res.write('{'); // Headers arrive, but the body never completes.
  }, async endpoint => {
    const client = new IOP(endpoint, '', 100);
    const run = client.run('saved');
    const stream = new DataStream(run, 'signal');
    const controller = new AbortController();
    const signal = controller.signal;
    for (const task of [
      () => client.request('/v1/context', undefined, signal),
      () => run.upload('data', new Blob(['42']), signal),
      () => stream.append(new Blob(['42']), 0, signal),
      async () => (await client.file('/v1/array.npy', signal)).arrayBuffer(),
      async () => (await run.download('data', signal)).arrayBuffer(),
    ]) {
      await assert.rejects(task, error => ['TimeoutError', 'AbortError'].includes(error.name));
      assert.equal(signal.aborted, false);
    }
    assert.ok(paths.length >= 5);
    assert.ok(paths.every(path => !path.includes('/cancel')));
  });
});

test('a caller can abort sooner and archive transfers keep their caller-controlled lifetime', {timeout:10000}, async () => {
  let received;
  await serving((req, res) => {
    if (req.url.endsWith('/archive')) {
      res.writeHead(200);
      res.write('first');
      setTimeout(() => res.end('second'), 150);
    } else received?.();
  }, async endpoint => {
    const client = new IOP(endpoint, '', 30);
    assert.equal(await (await client.run('saved').archive()).text(), 'firstsecond');
    const caller = new AbortController();
    const arrived = new Promise(resolve => { received = resolve; });
    const reason = new Error('Stop this client wait');
    const pending = new IOP(endpoint, '', 5000).request('/wait', undefined, caller.signal);
    const rejected = assert.rejects(pending, error => error === reason);
    await arrived;
    caller.abort(reason);
    await rejected;
  });
});

test('older hosts compose signals and release listeners after JSON completion', {timeout:10000}, async () => {
  const original = Object.getOwnPropertyDescriptor(AbortSignal, 'any');
  Object.defineProperty(AbortSignal, 'any', {value:undefined, configurable:true});
  try {
    await serving((req, res) => {
      if (req.url === '/hang') return;
      res.setHeader('Content-Type', 'application/json');
      res.end('{"ok":true}');
    }, async endpoint => {
      const controller = new AbortController();
      const client = new IOP(endpoint, '', 200);
      for (let i = 0; i < 20; i++) {
        assert.deepEqual(await client.request('/ok', undefined, controller.signal), {ok:true});
        assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
      }
      await assert.rejects(client.request('/hang', undefined, controller.signal), {name:'TimeoutError'});
      assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
      controller.abort(new Error('Already stopped'));
      await assert.rejects(client.request('/ok', undefined, controller.signal), /Already stopped/);
    });
  } finally {
    if (original) Object.defineProperty(AbortSignal, 'any', original);
    else delete AbortSignal.any;
  }
});
