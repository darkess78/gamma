import assert from 'node:assert/strict';
import { once } from 'node:events';
import type { IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import test, { type TestContext } from 'node:test';

import WebSocket, { WebSocketServer, type RawData } from 'ws';

import {
  MinecraftControlClient,
  type MinecraftControlClientOptions,
  type SidecarOutboundMessage,
  validateMinecraftControlToken,
  validateMinecraftControlUrl
} from '../src/control-client.js';

const CONTROL_TOKEN = 'test-control-sentinel-7Qx9';
const SENT_AT = '2026-07-10T18:00:00.000Z';

type TestServer = {
  server: WebSocketServer;
  url: string;
};

function defaultOptions(
  url: string,
  overrides: Partial<MinecraftControlClientOptions> = {}
): MinecraftControlClientOptions {
  let messageNumber = 0;
  return {
    url,
    controlToken: CONTROL_TOKEN,
    hello: {
      sidecarInstanceId: 'sidecar-phase-one',
      sidecarBuild: '0.1.0',
      nodeVersion: '22.22.2',
      minecraftLibraryVersion: 'not-installed',
      pathfinderVersion: 'not-installed'
    },
    now: () => new Date(SENT_AT),
    createMessageId: () => `sidecar-message-${messageNumber++}`,
    ...overrides
  };
}

function client(
  url: string,
  overrides: Partial<MinecraftControlClientOptions> = {}
): MinecraftControlClient {
  return new MinecraftControlClient(defaultOptions(url, overrides));
}

async function startServer(context: TestContext): Promise<TestServer> {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0, perMessageDeflate: false });
  await once(server, 'listening');
  const address = server.address() as AddressInfo;
  context.after(async () => {
    for (const socket of server.clients) socket.terminate();
    if (server.address() !== null) {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
  return { server, url: `ws://127.0.0.1:${address.port}/v1/minecraft/control` };
}

function nextConnection(server: WebSocketServer): Promise<[WebSocket, IncomingMessage]> {
  return once(server, 'connection') as Promise<[WebSocket, IncomingMessage]>;
}

async function nextText(socket: WebSocket): Promise<string> {
  const [data, isBinary] = (await once(socket, 'message')) as [RawData, boolean];
  assert.equal(isBinary, false);
  return rawDataToBuffer(data).toString('utf8');
}

async function nextJson(socket: WebSocket): Promise<Record<string, unknown>> {
  return JSON.parse(await nextText(socket)) as Record<string, unknown>;
}

function sendJson(socket: WebSocket, value: unknown): void {
  socket.send(JSON.stringify(value));
}

function welcome(connectionId = 'connection-one', sequence = 0): Record<string, unknown> {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'welcome',
    message_id: `gamma-welcome-${connectionId}`,
    connection_id: connectionId,
    sent_at: SENT_AT,
    sequence,
    payload: {
      selected_version: 1,
      heartbeat_interval_seconds: 5,
      liveness_timeout_seconds: 15,
      maximum_message_bytes: 65_536,
      command_cache_ttl_seconds: 600,
      command_cache_capacity: 1_000,
      minecraft_chat_output_enabled: false
    }
  };
}

function command(
  sequence: number,
  commandId: string,
  connectionId = 'connection-one',
  name: 'report_status' | 'stop' = 'report_status'
): Record<string, unknown> {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'command',
    message_id: `gamma-command-${commandId}`,
    connection_id: connectionId,
    sent_at: SENT_AT,
    sequence,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: {
      name,
      deadline_at: '2026-07-10T18:01:00Z',
      arguments: name === 'report_status' ? { detail_level: 'standard' } : { reason: null }
    }
  };
}

function emergencyStop(sequence: number, connectionId = 'connection-one'): Record<string, unknown> {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'emergency_stop',
    message_id: 'gamma-emergency-one',
    connection_id: connectionId,
    sent_at: SENT_AT,
    sequence,
    trace_id: 'trace-emergency-one',
    command_id: 'command-emergency-one',
    payload: { reason: 'Immediate stop.' }
  };
}

function shutdown(sequence: number, connectionId = 'connection-one'): Record<string, unknown> {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'shutdown',
    message_id: 'gamma-shutdown-one',
    connection_id: connectionId,
    sent_at: SENT_AT,
    sequence,
    trace_id: 'trace-shutdown-one',
    payload: { reason: 'Gamma shutdown.', leave_minecraft: true }
  };
}

async function completeHandshake(
  controlClient: MinecraftControlClient,
  server: WebSocketServer,
  connectionId = 'connection-one'
): Promise<{ socket: WebSocket; request: IncomingMessage; hello: Record<string, unknown> }> {
  const connection = nextConnection(server);
  const connected = controlClient.connect();
  const [socket, request] = await connection;
  const hello = await nextJson(socket);
  sendJson(socket, welcome(connectionId));
  const accepted = await connected;
  assert.equal(accepted.connection_id, connectionId);
  return { socket, request, hello };
}

async function waitFor(
  predicate: () => boolean,
  label: string,
  timeoutMs = 1_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) assert.fail(`Timed out waiting for ${label}`);
    await new Promise<void>((resolve) => setTimeout(resolve, 2));
  }
}

function deferredVoid(): { promise: Promise<void>; resolve: () => void } {
  let resolvePromise!: () => void;
  const promise = new Promise<void>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

test('construction validates only local values and performs no I/O', () => {
  const controlClient = client('ws://127.0.0.1:9/v1/minecraft/control');
  const status = controlClient.status();
  assert.deepEqual(status, {
    state: 'disconnected',
    connectionId: null,
    lastReceivedSequence: null
  });
  assert.equal(Object.isFrozen(status), true);
  assert.equal(JSON.stringify(controlClient).includes(CONTROL_TOKEN), false);
  assert.equal(JSON.stringify(status).includes(CONTROL_TOKEN), false);
});

test('URL policy accepts only exact literal loopback control endpoints', () => {
  for (const value of [
    'ws://127.0.0.1:80/v1/minecraft/control',
    'ws://127.42.9.7:65535/v1/minecraft/control',
    'ws://[::1]:9000/v1/minecraft/control',
    'ws://[0:0:0:0:0:0:0:1]:9000/v1/minecraft/control',
    'ws://[::ffff:127.0.0.1]:9000/v1/minecraft/control',
    'ws://[::ffff:7f2a:102]:9000/v1/minecraft/control'
  ]) {
    assert.equal(validateMinecraftControlUrl(value), value);
  }

  for (const value of [
    'ws://localhost:8000/v1/minecraft/control',
    'ws://localhost.:8000/v1/minecraft/control',
    'ws://127.1:8000/v1/minecraft/control',
    'ws://0177.0.0.1:8000/v1/minecraft/control',
    'ws://0x7f000001:8000/v1/minecraft/control',
    'ws://2130706433:8000/v1/minecraft/control',
    'ws://126.255.255.255:8000/v1/minecraft/control',
    'ws://0.0.0.0:8000/v1/minecraft/control',
    'ws://169.254.1.1:8000/v1/minecraft/control',
    'ws://192.168.1.2:8000/v1/minecraft/control',
    'ws://[::]:8000/v1/minecraft/control',
    'ws://[fe80::1]:8000/v1/minecraft/control',
    'ws://[::ffff:192.168.1.2]:8000/v1/minecraft/control',
    'wss://127.0.0.1:8000/v1/minecraft/control',
    'http://127.0.0.1:8000/v1/minecraft/control',
    'ws://user:pass@127.0.0.1:8000/v1/minecraft/control',
    'ws://127.0.0.1/v1/minecraft/control',
    'ws://127.0.0.1:0/v1/minecraft/control',
    'ws://127.0.0.1:65536/v1/minecraft/control',
    'ws://127.0.0.1:08000/v1/minecraft/control',
    'ws://127.0.0.1:8000/v1/minecraft/control/',
    'ws://127.0.0.1:8000/v1/minecraft/%63ontrol',
    'ws://127.0.0.1:8000/v1/minecraft/control?',
    'ws://127.0.0.1:8000/v1/minecraft/control#',
    ' ws://127.0.0.1:8000/v1/minecraft/control'
  ]) {
    assert.throws(() => validateMinecraftControlUrl(value), /Control URL/u);
  }
});

test('token policy is strict, header-safe, and never includes rejected values in errors', () => {
  validateMinecraftControlToken(CONTROL_TOKEN);
  for (const value of [
    '',
    ' ',
    ' leading',
    'trailing ',
    'internal space',
    'line\nbreak',
    'tab\tvalue',
    `nel\u0085value`,
    `nbsp\u00a0value`,
    `unit\u001cseparator`,
    '非ASCII'
  ]) {
    let rendered = '';
    assert.throws(
      () => validateMinecraftControlToken(value),
      (error: unknown) => {
        rendered = String(error);
        return true;
      }
    );
    assert.equal(rendered.includes(CONTROL_TOKEN), false);
  }
  assert.throws(
    () =>
      client('ws://127.0.0.1:9/v1/minecraft/control', {
        hello: {
          ...defaultOptions('ws://127.0.0.1:9/v1/minecraft/control').hello,
          sidecarInstanceId: `sidecar-${CONTROL_TOKEN}`
        }
      }),
    /Hello identity/u
  );
});

test('message-size and timeout options reject booleans, non-integers, and out-of-range values', () => {
  const url = 'ws://127.0.0.1:9/v1/minecraft/control';
  for (const value of [true, 0, 1.5, 1_048_577]) {
    assert.throws(
      () => client(url, { maximumInboundBytes: value as number }),
      /Maximum inbound bytes/u
    );
  }
  for (const value of [false, 0, 1.5, 300_001]) {
    assert.throws(
      () => client(url, { handshakeTimeoutMs: value as number }),
      /Handshake timeout/u
    );
  }
});

test('client sends one canonical honest hello with the exact bearer header and waits for welcome', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const connection = nextConnection(running.server);
  let resolved = false;
  const connected = controlClient.connect().then((value) => {
    resolved = true;
    return value;
  });
  const [socket, request] = await connection;
  assert.equal(request.url === '/v1/minecraft/control', true);
  assert.equal(request.headers.authorization === `Bearer ${CONTROL_TOKEN}`, true);
  const frames: string[] = [];
  socket.on('message', (data) => frames.push(rawDataToBuffer(data).toString('utf8')));
  await waitFor(() => frames.length === 1, 'hello');
  const hello = JSON.parse(frames[0] ?? '{}') as Record<string, any>;
  assert.equal(hello.type, 'hello');
  assert.equal(hello.sequence, 0);
  assert.equal(hello.connection_id, undefined);
  assert.deepEqual(hello.payload.capabilities, ['companion_v1']);
  assert.equal(hello.payload.minecraft_library_version, 'not-installed');
  assert.equal(hello.payload.pathfinder_version, 'not-installed');
  assert.equal(hello.payload.companion_state, 'DISCONNECTED');
  assert.equal(JSON.stringify(hello).includes(CONTROL_TOKEN), false);
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(frames.length, 1);
  assert.equal(resolved, false);
  assert.equal(controlClient.status().state, 'awaiting_welcome');

  sendJson(socket, welcome());
  const accepted = await connected;
  assert.equal(accepted.payload.selected_version, 1);
  assert.deepEqual(controlClient.status(), {
    state: 'ready',
    connectionId: 'connection-one',
    lastReceivedSequence: 0
  });
  await controlClient.close();
});

test('invalid, duplicate-key, scalar, binary, and pre-welcome messages reject the handshake', async (context) => {
  const cases: Array<{ name: string; frame: string | Buffer; binary?: boolean }> = [
    { name: 'invalid JSON', frame: '{' },
    { name: 'JSON comments', frame: '{/* not JSON */}' },
    { name: 'trailing comma', frame: '{"type":"welcome",}' },
    { name: 'scalar JSON', frame: '42' },
    {
      name: 'top-level duplicate',
      frame: JSON.stringify(welcome()).replace('"type":"welcome"', '"type":"welcome","type":"welcome"')
    },
    {
      name: 'nested escaped duplicate',
      frame: JSON.stringify(welcome()).replace(
        '"selected_version":1',
        '"selected_version":1,"selected\\u005fversion":1'
      )
    },
    { name: 'command before welcome', frame: JSON.stringify(command(0, 'prewelcome')) },
    { name: 'binary welcome', frame: Buffer.from(JSON.stringify(welcome())), binary: true },
    {
      name: 'unknown welcome field',
      frame: JSON.stringify({ ...welcome(), unexpected: true })
    },
    { name: 'nonzero welcome sequence', frame: JSON.stringify(welcome('connection-one', 1)) }
  ];

  for (const item of cases) {
    await context.test(item.name, async (subcontext) => {
      const running = await startServer(subcontext);
      const controlClient = client(running.url);
      const connection = nextConnection(running.server);
      const connected = controlClient.connect();
      const [socket] = await connection;
      await nextText(socket);
      socket.send(item.frame, { binary: item.binary ?? false });
      await assert.rejects(connected, /Control/u);
      await controlClient.waitForDisconnect();
      assert.equal(controlClient.status().state, 'disconnected');
    });
  }
});

test('handshake timeout rejects, closes the socket, and leaves no retry timer', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url, { handshakeTimeoutMs: 25 });
  const connection = nextConnection(running.server);
  const connected = controlClient.connect();
  const [socket] = await connection;
  await nextText(socket);
  await assert.rejects(connected, /timed out/u);
  const disconnect = await controlClient.waitForDisconnect();
  assert.equal(disconnect.kind, 'handshake_timeout');
  assert.equal(controlClient.status().state, 'disconnected');
});

test('concurrent connect calls share one handshake and create only one socket', async (context) => {
  const running = await startServer(context);
  let connectionCount = 0;
  running.server.on('connection', () => {
    connectionCount += 1;
  });
  const controlClient = client(running.url);
  const connection = nextConnection(running.server);
  const first = controlClient.connect();
  const second = controlClient.connect();
  assert.equal(first, second);
  const [socket] = await connection;
  await nextText(socket);
  sendJson(socket, welcome());
  const [firstWelcome, secondWelcome] = await Promise.all([first, second]);
  assert.equal(firstWelcome.connection_id, 'connection-one');
  assert.equal(secondWelcome.connection_id, 'connection-one');
  assert.equal(connectionCount, 1);
  await controlClient.close();
});

test('duplicate-key checks cover arrays and deep nesting without exposing parser text', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const connection = nextConnection(running.server);
  const connected = controlClient.connect();
  const [socket] = await connection;
  await nextText(socket);
  const secretKey = 'raw-parser-secret';
  socket.send(
    `{"protocol":"gamma.minecraft","version":1,"type":"welcome","message_id":"m",` +
      `"connection_id":"c","sent_at":"${SENT_AT}","sequence":0,"payload":{` +
      `"selected_version":1,"heartbeat_interval_seconds":5,"liveness_timeout_seconds":15,` +
      `"maximum_message_bytes":65536,"command_cache_ttl_seconds":600,` +
      `"command_cache_capacity":1000,"minecraft_chat_output_enabled":false,` +
      `"array":[{"${secretKey}":1,"${secretKey}":2}]}}`
  );
  let rendered = '';
  await assert.rejects(connected, (error: unknown) => {
    rendered = String(error);
    return true;
  });
  assert.equal(rendered.includes(secretKey), false);
  await controlClient.waitForDisconnect();
});

test('explicit heartbeat and concurrent sidecar sends are canonical, ordered, and monotonic', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const { socket } = await completeHandshake(controlClient, running.server);
  const messages: Record<string, any>[] = [];
  socket.on('message', (data, isBinary) => {
    if (!isBinary) {
      messages.push(JSON.parse(rawDataToBuffer(data).toString('utf8')) as Record<string, any>);
    }
  });
  await Promise.all([
    controlClient.sendHeartbeat('STOPPED'),
    controlClient.sendSidecarMessage({
      type: 'sidecar_status',
      payload: {
        connection_state: 'connected',
        companion_state: 'STOPPED',
        uptime_seconds: 1,
        last_failure: null
      }
    }),
    controlClient.sendSidecarMessage({
      type: 'minecraft_status',
      payload: {
        connection_state: 'disconnected',
        companion_state: 'STOPPED',
        negotiated_version: null,
        dimension: null
      }
    })
  ]);
  await waitFor(() => messages.length === 3, 'three ordered sidecar messages');
  assert.deepEqual(messages.map((message) => message.type), [
    'heartbeat',
    'sidecar_status',
    'minecraft_status'
  ]);
  assert.deepEqual(messages.map((message) => message.sequence), [1, 2, 3]);
  assert.equal((messages[0]?.payload as Record<string, unknown>).last_received_sequence, 0);
  assert.equal((messages[0]?.payload as Record<string, unknown>).companion_state, 'STOPPED');
  assert.equal(messages.every((message) => message.connection_id === 'connection-one'), true);
  await controlClient.close();
});

test('send is rejected before welcome, after close, and for a runtime-cast wrong direction', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  await assert.rejects(controlClient.sendHeartbeat(), /not ready/u);
  const { socket } = await completeHandshake(controlClient, running.server);
  const wrongDirection = welcome() as unknown as SidecarOutboundMessage;
  await assert.rejects(controlClient.sendSidecarMessage(wrongDirection), /Invalid sidecar/u);
  const received: string[] = [];
  socket.on('message', (data) => received.push(rawDataToBuffer(data).toString('utf8')));
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(received.length, 0);
  await controlClient.close();
  await assert.rejects(controlClient.sendHeartbeat(), /not ready/u);
});

test('caller cannot override generated envelope fields or place the token in a frame', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const { socket } = await completeHandshake(controlClient, running.server);
  const frames: string[] = [];
  socket.on('message', (data) => frames.push(rawDataToBuffer(data).toString('utf8')));

  const forged = {
    type: 'sidecar_status',
    protocol: 'forged',
    version: 99,
    message_id: CONTROL_TOKEN,
    connection_id: 'forged',
    sent_at: 'not-a-time',
    sequence: 999,
    payload: {
      connection_state: 'connected',
      companion_state: 'DISCONNECTED',
      uptime_seconds: 1,
      last_failure: null
    }
  } as unknown as SidecarOutboundMessage;
  await assert.rejects(controlClient.sendSidecarMessage(forged), /Invalid sidecar/u);

  await assert.rejects(
    controlClient.sendSidecarMessage({
      type: 'sidecar_status',
      payload: {
        connection_state: 'connected',
        companion_state: 'DISCONNECTED',
        uptime_seconds: 1,
        last_failure: {
          code: 'INTERNAL_SIDECAR_ERROR',
          safe_detail: `blocked-${CONTROL_TOKEN}`,
          retriable: false
        }
      }
    }),
    /Invalid sidecar/u
  );
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(frames.some((frame) => frame.includes(CONTROL_TOKEN)), false);
  assert.equal(JSON.stringify(controlClient.status()).includes(CONTROL_TOKEN), false);
  await controlClient.close();
});

test('duplicate or token-valued generated message IDs are rejected without transmission', async (context) => {
  const running = await startServer(context);
  let calls = 0;
  const controlClient = client(running.url, {
    createMessageId: () => {
      calls += 1;
      if (calls === 1) return 'hello-id';
      if (calls === 2) return 'duplicate-id';
      return 'duplicate-id';
    }
  });
  const { socket } = await completeHandshake(controlClient, running.server);
  const first = nextJson(socket);
  await controlClient.sendHeartbeat();
  assert.equal((await first).message_id, 'duplicate-id');
  await assert.rejects(controlClient.sendHeartbeat(), /Invalid sidecar/u);
  await controlClient.close();

  const secondRunning = await startServer(context);
  const tokenIdClient = client(secondRunning.url, { createMessageId: () => CONTROL_TOKEN });
  const connection = nextConnection(secondRunning.server);
  const connected = tokenIdClient.connect();
  const [secondSocket] = await connection;
  const observed: string[] = [];
  secondSocket.on('message', (data) => observed.push(rawDataToBuffer(data).toString('utf8')));
  await assert.rejects(connected, /Could not create/u);
  await tokenIdClient.waitForDisconnect();
  assert.equal(observed.some((frame) => frame.includes(CONTROL_TOKEN)), false);
});

test('Gamma direction, session, welcome, and sequence violations close without handler delivery', async (context) => {
  const cases: Array<{ name: string; frame: (connectionId: string) => unknown }> = [
    {
      name: 'sidecar heartbeat from Gamma',
      frame: (connectionId) => ({
        protocol: 'gamma.minecraft',
        version: 1,
        type: 'heartbeat',
        message_id: 'gamma-wrong-direction',
        connection_id: connectionId,
        sent_at: SENT_AT,
        sequence: 1,
        payload: { companion_state: 'IDLE', last_received_sequence: 0 }
      })
    },
    { name: 'second welcome', frame: (connectionId) => welcome(connectionId, 1) },
    { name: 'wrong connection', frame: () => command(1, 'wrong-session', 'old-connection') },
    { name: 'duplicate sequence', frame: (connectionId) => command(0, 'duplicate-seq', connectionId) }
  ];

  for (const item of cases) {
    await context.test(item.name, async (subcontext) => {
      const running = await startServer(subcontext);
      const controlClient = client(running.url);
      const delivered: string[] = [];
      controlClient.setMessageHandler((message) => {
        delivered.push(message.type);
      });
      const { socket } = await completeHandshake(controlClient, running.server);
      sendJson(socket, item.frame('connection-one'));
      const disconnect = await controlClient.waitForDisconnect();
      assert.equal(disconnect.kind, 'protocol_error');
      assert.deepEqual(delivered, []);
    });
  }
});

test('byte limits use UTF-8 bytes and distinguish exact-limit parsing from oversized frames', async (context) => {
  await context.test('exact multibyte limit reaches protocol parsing', async (subcontext) => {
    const running = await startServer(subcontext);
    const controlClient = client(running.url, { maximumInboundBytes: 512 });
    const { socket } = await completeHandshake(controlClient, running.server);
    const close = once(socket, 'close') as Promise<[number, Buffer]>;
    const exact = `"${'é'.repeat(255)}"`;
    assert.equal(Buffer.byteLength(exact), 512);
    socket.send(exact);
    const [code] = await close;
    assert.equal(code, 1008);
    await controlClient.waitForDisconnect();
  });

  await context.test('one byte over is rejected by the payload limit', async (subcontext) => {
    const running = await startServer(subcontext);
    const controlClient = client(running.url, { maximumInboundBytes: 512 });
    const { socket } = await completeHandshake(controlClient, running.server);
    const close = once(socket, 'close') as Promise<[number, Buffer]>;
    const oversized = `"${'é'.repeat(255)}a"`;
    assert.equal(Buffer.byteLength(oversized), 513);
    socket.send(oversized);
    const [code] = await close;
    assert.equal(code, 1009);
    await controlClient.waitForDisconnect();
  });
});

test('ordinary async handlers execute in order', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const firstGate = deferredVoid();
  const events: string[] = [];
  controlClient.setMessageHandler(async (message) => {
    if (message.type !== 'command') return;
    events.push(`start:${message.command_id}`);
    if (message.command_id === 'first') await firstGate.promise;
    events.push(`end:${message.command_id}`);
  });
  const { socket } = await completeHandshake(controlClient, running.server);
  sendJson(socket, command(1, 'first'));
  sendJson(socket, command(2, 'second'));
  await waitFor(() => events.length === 1, 'first handler start');
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(events, ['start:first']);
  firstGate.resolve();
  await waitFor(() => events.length === 4, 'ordered handler completion');
  assert.deepEqual(events, ['start:first', 'end:first', 'start:second', 'end:second']);
  await controlClient.close();
});

test('emergency stop bypasses a blocked ordinary handler without fabricating an acknowledgment', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const commandGate = deferredVoid();
  const events: string[] = [];
  controlClient.setMessageHandler(async (message) => {
    events.push(message.type);
    if (message.type === 'command') await commandGate.promise;
  });
  const { socket } = await completeHandshake(controlClient, running.server);
  const outbound: string[] = [];
  socket.on('message', (data) => outbound.push(rawDataToBuffer(data).toString('utf8')));
  sendJson(socket, command(1, 'blocked'));
  await waitFor(() => events.includes('command'), 'command handler');
  sendJson(socket, emergencyStop(2));
  await waitFor(() => events.includes('emergency_stop'), 'emergency handler');
  assert.deepEqual(events, ['command', 'emergency_stop']);
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(outbound.length, 0);
  commandGate.resolve();
  await controlClient.close();
});

test('handler failure closes with a stable reason and never sends the raw exception to Gamma', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const rawFailure = `raw-handler-${CONTROL_TOKEN}`;
  controlClient.setMessageHandler(() => {
    throw new Error(rawFailure);
  });
  const { socket } = await completeHandshake(controlClient, running.server);
  const outbound: string[] = [];
  socket.on('message', (data) => outbound.push(rawDataToBuffer(data).toString('utf8')));
  const closed = once(socket, 'close') as Promise<[number, Buffer]>;
  sendJson(socket, command(1, 'handler-failure'));
  const [code, reason] = await closed;
  assert.equal(code, 1011);
  assert.equal(reason.toString(), 'message handler failed');
  assert.equal(reason.toString().includes(rawFailure), false);
  assert.equal(outbound.some((frame) => frame.includes(rawFailure)), false);
  await controlClient.waitForDisconnect();
});

test('shutdown is delivered, awaits its handler, and then closes cleanly', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const shutdownGate = deferredVoid();
  let delivered = false;
  controlClient.setMessageHandler(async (message) => {
    if (message.type !== 'shutdown') return;
    delivered = true;
    await shutdownGate.promise;
  });
  const { socket } = await completeHandshake(controlClient, running.server);
  let closed = false;
  const closePromise = (once(socket, 'close') as Promise<[number, Buffer]>).then((value) => {
    closed = true;
    return value;
  });
  sendJson(socket, shutdown(1));
  await waitFor(() => delivered, 'shutdown delivery');
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(closed, false);
  shutdownGate.resolve();
  const [code, reason] = await closePromise;
  assert.equal(code, 1000);
  assert.equal(reason.toString(), 'shutdown received');
  const disconnect = await controlClient.waitForDisconnect();
  assert.equal(disconnect.kind, 'local');
});

test('clean and abrupt remote closes clean up without automatic reconnect', async (context) => {
  await context.test('clean close', async (subcontext) => {
    const running = await startServer(subcontext);
    let connections = 0;
    running.server.on('connection', () => {
      connections += 1;
    });
    const controlClient = client(running.url);
    const { socket } = await completeHandshake(controlClient, running.server);
    socket.close(1000, 'peer-controlled-text');
    const disconnect = await controlClient.waitForDisconnect();
    assert.equal(disconnect.kind, 'clean');
    await new Promise<void>((resolve) => setTimeout(resolve, 30));
    assert.equal(connections, 1);
    assert.equal(JSON.stringify(controlClient.status()).includes('peer-controlled-text'), false);
  });

  await context.test('abrupt close', async (subcontext) => {
    const running = await startServer(subcontext);
    let connections = 0;
    running.server.on('connection', () => {
      connections += 1;
    });
    const controlClient = client(running.url);
    const { socket } = await completeHandshake(controlClient, running.server);
    socket.terminate();
    const disconnect = await controlClient.waitForDisconnect();
    assert.equal(disconnect.kind, 'abrupt');
    await new Promise<void>((resolve) => setTimeout(resolve, 30));
    assert.equal(connections, 1);
  });
});

test('close before welcome rejects connect and repeated close is idempotent', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const connection = nextConnection(running.server);
  const connected = controlClient.connect();
  await connection;
  const firstClose = controlClient.close();
  const secondClose = controlClient.close();
  await assert.rejects(connected, /closed locally/u);
  await Promise.all([firstClose, secondClose]);
  await controlClient.close();
  assert.equal(controlClient.status().state, 'disconnected');
});

test('explicit reconnect creates a fresh session, resets sequences, and replays nothing', async (context) => {
  const running = await startServer(context);
  const controlClient = client(running.url);
  const first = await completeHandshake(controlClient, running.server, 'connection-first');
  const firstHeartbeat = nextJson(first.socket);
  await controlClient.sendHeartbeat();
  assert.equal((await firstHeartbeat).sequence, 1);
  await controlClient.close();

  const secondConnection = nextConnection(running.server);
  const connectedAgain = controlClient.connect();
  const [secondSocket] = await secondConnection;
  const secondHello = await nextJson(secondSocket);
  assert.equal(secondHello.type, 'hello');
  assert.equal(secondHello.sequence, 0);
  const unexpected: string[] = [];
  secondSocket.on('message', (data) => unexpected.push(rawDataToBuffer(data).toString('utf8')));
  sendJson(secondSocket, welcome('connection-second'));
  const secondWelcome = await connectedAgain;
  assert.equal(secondWelcome.connection_id, 'connection-second');
  await new Promise<void>((resolve) => setTimeout(resolve, 15));
  assert.equal(unexpected.length, 0);
  assert.deepEqual(controlClient.status(), {
    state: 'ready',
    connectionId: 'connection-second',
    lastReceivedSequence: 0
  });
  await controlClient.close();
});

function rawDataToBuffer(data: RawData): Buffer {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.concat(data);
  return Buffer.from(data);
}
