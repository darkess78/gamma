import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import type { IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import test, { type TestContext } from 'node:test';

import WebSocket, { WebSocketServer, type RawData } from 'ws';

import type { MinecraftSignalTarget } from '../src/index.js';
import { installSignalHandlers, runMinecraftSidecarMain } from '../src/index.js';
import { loadMinecraftSidecarConfig } from '../src/config.js';
import {
  MinecraftSidecarRuntime,
  type MinecraftRuntimeTimer
} from '../src/runtime.js';

const TOKEN = 'runtime-control-token-4Vn8';
const NOW = '2026-07-10T18:00:00.000Z';

type RunningServer = { server: WebSocketServer; url: string };

class JsonQueue {
  readonly #values: Record<string, any>[] = [];
  readonly #waiters: Array<(value: Record<string, any>) => void> = [];

  constructor(socket: WebSocket) {
    socket.on('message', (data, isBinary) => {
      if (isBinary) return;
      const value = JSON.parse(rawDataToBuffer(data).toString('utf8')) as Record<string, any>;
      const waiter = this.#waiters.shift();
      if (waiter === undefined) this.#values.push(value);
      else waiter(value);
    });
  }

  get length(): number {
    return this.#values.length;
  }

  async next(): Promise<Record<string, any>> {
    const value = this.#values.shift();
    if (value !== undefined) return value;
    return new Promise<Record<string, any>>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Timed out waiting for sidecar message')), 1_000);
      this.#waiters.push((message) => {
        clearTimeout(timer);
        resolve(message);
      });
    });
  }
}

class FakeIntervals {
  callback: (() => void) | undefined;
  milliseconds: number | undefined;
  unrefCount = 0;
  clearCount = 0;
  readonly handle: MinecraftRuntimeTimer = {
    unref: () => {
      this.unrefCount += 1;
    }
  };

  readonly set = (callback: () => void, milliseconds: number): MinecraftRuntimeTimer => {
    assert.equal(this.callback, undefined);
    this.callback = callback;
    this.milliseconds = milliseconds;
    return this.handle;
  };

  readonly clear = (handle: MinecraftRuntimeTimer): void => {
    assert.equal(handle, this.handle);
    this.clearCount += 1;
    this.callback = undefined;
  };

  fire(): void {
    assert.notEqual(this.callback, undefined);
    this.callback?.();
  }
}

async function startServer(context: TestContext): Promise<RunningServer> {
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

function config(url: string) {
  return loadMinecraftSidecarConfig({
    SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL: url,
    SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
    SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'runtime-sidecar',
    SHANA_MINECRAFT_HEARTBEAT_SECONDS: '5'
  });
}

function sendJson(socket: WebSocket, value: unknown): void {
  socket.send(JSON.stringify(value));
}

function welcome(connectionId = 'runtime-connection', heartbeatSeconds = 2) {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'welcome',
    message_id: `welcome-${connectionId}`,
    connection_id: connectionId,
    sent_at: NOW,
    sequence: 0,
    payload: {
      selected_version: 1,
      heartbeat_interval_seconds: heartbeatSeconds,
      liveness_timeout_seconds: 15,
      maximum_message_bytes: 65_536,
      command_cache_ttl_seconds: 600,
      command_cache_capacity: 1_000,
      minecraft_chat_output_enabled: false
    }
  };
}

function gammaCommand(
  sequence: number,
  commandId: string,
  name: string,
  args: Record<string, unknown>,
  deadline = '2026-07-10T18:01:00Z',
  sentAt = NOW
) {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'command',
    message_id: `message-${commandId}`,
    connection_id: 'runtime-connection',
    sent_at: sentAt,
    sequence,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: { name, deadline_at: deadline, arguments: args }
  };
}

async function startRuntime(
  context: TestContext,
  options: { delayedWelcome?: boolean } = {}
) {
  const running = await startServer(context);
  const intervals = new FakeIntervals();
  let monotonic = 1_000;
  const runtime = new MinecraftSidecarRuntime(config(running.url), {
    now: () => new Date(NOW),
    monotonicNowMs: () => monotonic,
    setInterval: intervals.set,
    clearInterval: intervals.clear
  });
  const connection = nextConnection(running.server);
  const run = runtime.run();
  const [socket, request] = await connection;
  const queue = new JsonQueue(socket);
  const hello = await queue.next();
  if (!options.delayedWelcome) sendJson(socket, welcome());
  return {
    running,
    runtime,
    run,
    socket,
    request,
    queue,
    hello,
    intervals,
    advance: (milliseconds: number) => {
      monotonic += milliseconds;
    }
  };
}

async function consumeStartup(queue: JsonQueue): Promise<Record<string, any>[]> {
  return [await queue.next(), await queue.next(), await queue.next()];
}

test('runtime construction and module import perform no I/O; run sends honest startup state', async (context) => {
  const running = await startServer(context);
  let connections = 0;
  running.server.on('connection', () => {
    connections += 1;
  });
  const intervals = new FakeIntervals();
  const runtime = new MinecraftSidecarRuntime(config(running.url), {
    now: () => new Date(NOW),
    monotonicNowMs: () => 1_000,
    setInterval: intervals.set,
    clearInterval: intervals.clear
  });
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(connections, 0);
  assert.equal(intervals.callback, undefined);

  const connection = nextConnection(running.server);
  const firstRun = runtime.run();
  const secondRun = runtime.run();
  assert.equal(firstRun, secondRun);
  const [socket, request] = await connection;
  const queue = new JsonQueue(socket);
  const hello = await queue.next();
  assert.equal(request.headers.authorization === `Bearer ${TOKEN}`, true);
  assert.equal(hello.type, 'hello');
  assert.equal(hello.payload.minecraft_library_version, 'not-installed');
  assert.equal(hello.payload.pathfinder_version, 'not-installed');
  assert.equal(hello.payload.companion_state, 'DISCONNECTED');
  assert.equal(JSON.stringify(hello).includes(TOKEN), false);
  assert.equal(intervals.callback, undefined);

  sendJson(socket, welcome());
  const startup = await consumeStartup(queue);
  assert.deepEqual(startup.map((message) => message.type), [
    'sidecar_status',
    'minecraft_status',
    'state_snapshot'
  ]);
  assert.deepEqual(startup.map((message) => message.sequence), [1, 2, 3]);
  assert.equal(startup[0]?.payload.companion_state, 'DISCONNECTED');
  assert.equal(startup[1]?.payload.connection_state, 'disconnected');
  assert.equal(startup[2]?.payload.owner_present, false);
  assert.equal(intervals.milliseconds, 2_000);
  assert.equal(intervals.unrefCount, 1);

  intervals.fire();
  intervals.fire();
  const heartbeat = await queue.next();
  assert.equal(heartbeat.type, 'heartbeat');
  assert.equal(heartbeat.sequence, 4);
  assert.equal(heartbeat.payload.last_received_sequence, 0);
  await new Promise<void>((resolve) => setTimeout(resolve, 10));
  assert.equal(queue.length, 0);
  await runtime.shutdown('requested');
  const exit = await firstRun;
  assert.equal(exit.category, 'requested');
  assert.equal(intervals.clearCount, 1);
  assert.deepEqual(runtime.status(), {
    lifecycle: 'stopped',
    controlReady: false,
    companionState: 'DISCONNECTED',
    minecraftConnectionState: 'disconnected',
    heartbeatActive: false,
    emergencyStopActive: false,
    exitCategory: 'requested'
  });
  assert.equal(JSON.stringify(runtime.status()).includes(TOKEN), false);
});

test('no status or heartbeat begins before welcome and repeated shutdown is idempotent', async (context) => {
  const setup = await startRuntime(context, { delayedWelcome: true });
  await new Promise<void>((resolve) => setTimeout(resolve, 15));
  assert.equal(setup.queue.length, 0);
  assert.equal(setup.intervals.callback, undefined);
  sendJson(setup.socket, welcome());
  await consumeStartup(setup.queue);
  await Promise.all([setup.runtime.shutdown('signal'), setup.runtime.shutdown('signal')]);
  assert.equal((await setup.run).category, 'signal');
  assert.equal(setup.intervals.clearCount, 1);
});

test('control disconnect stops heartbeat, exits safely, and never reconnects', async (context) => {
  const setup = await startRuntime(context);
  let connections = 1;
  setup.running.server.on('connection', () => {
    connections += 1;
  });
  await consumeStartup(setup.queue);
  setup.socket.terminate();
  const exit = await setup.run;
  assert.equal(exit.category, 'control_disconnected');
  assert.equal(setup.intervals.clearCount, 1);
  await new Promise<void>((resolve) => setTimeout(resolve, 30));
  assert.equal(connections, 1);
});

test('unsupported canonical commands receive one rejected ack and no terminal claim', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  const commands: Array<[string, Record<string, unknown>]> = [
    ['join', {}],
    ['leave', {}],
    ['follow_owner', { follow_distance: 3, lease_duration_seconds: 30 }],
    ['wait_here', {}],
    ['come_here', { arrival_distance: 3 }],
    ['look_at_owner', { duration_seconds: 2 }],
    ['stop', {}]
  ];
  let sequence = 1;
  for (const [name, args] of commands) {
    const id = `unsupported-${name}`;
    sendJson(setup.socket, gammaCommand(sequence++, id, name, args));
    const ack = await setup.queue.next();
    assert.equal(ack.type, 'command_ack');
    assert.equal(ack.command_id, id);
    assert.equal(ack.payload.accepted, false);
    assert.equal(ack.payload.failure.code, 'SAFETY_POLICY_BLOCKED');
  }
  await new Promise<void>((resolve) => setTimeout(resolve, 15));
  assert.equal(setup.queue.length, 0);
  await setup.runtime.shutdown();
  await setup.run;
});

test('report_status sends accepted ack, bounded disconnected facts, and one terminal result', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  setup.advance(25);
  sendJson(
    setup.socket,
    gammaCommand(1, 'report-one', 'report_status', { detail_level: 'standard' })
  );
  const messages = [
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next()
  ];
  assert.deepEqual(messages.map((message) => message.type), [
    'command_ack',
    'sidecar_status',
    'minecraft_status',
    'state_snapshot',
    'terminal_result'
  ]);
  assert.equal(messages[0]?.payload.accepted, true);
  assert.equal(messages[2]?.payload.connection_state, 'disconnected');
  assert.equal(messages[3]?.payload.owner_present, false);
  assert.equal(messages[3]?.payload.active_command_name, 'report_status');
  assert.equal(messages[4]?.payload.outcome, 'completed');
  assert.equal(messages[4]?.command_id, 'report-one');
  await setup.runtime.shutdown();
  await setup.run;
});

test('expired report_status is rejected once with DEADLINE_EXCEEDED', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  sendJson(
    setup.socket,
    gammaCommand(
      1,
      'expired-report',
      'report_status',
      { detail_level: 'basic' },
      '2026-07-10T17:59:59Z',
      '2026-07-10T17:59:00Z'
    )
  );
  const ack = await setup.queue.next();
  assert.equal(ack.type, 'command_ack');
  assert.equal(ack.payload.accepted, false);
  assert.equal(ack.payload.failure.code, 'DEADLINE_EXCEEDED');
  await new Promise<void>((resolve) => setTimeout(resolve, 15));
  assert.equal(setup.queue.length, 0);
  await setup.runtime.shutdown();
  await setup.run;
});

test('emergency-stop envelope latches before reporting and heartbeat stays non-moving', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  sendJson(setup.socket, {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'emergency_stop',
    message_id: 'emergency-envelope',
    connection_id: 'runtime-connection',
    sent_at: NOW,
    sequence: 1,
    trace_id: 'trace-emergency-envelope',
    command_id: 'command-emergency-envelope',
    payload: { reason: 'Operator stop.' }
  });
  const messages = [await setup.queue.next(), await setup.queue.next(), await setup.queue.next()];
  assert.deepEqual(messages.map((message) => message.type), [
    'sidecar_status',
    'minecraft_status',
    'state_snapshot'
  ]);
  assert.equal(messages.every((message) => message.payload.companion_state === 'STOPPED'), true);
  assert.equal(setup.runtime.status().emergencyStopActive, true);
  setup.intervals.fire();
  const heartbeat = await setup.queue.next();
  assert.equal(heartbeat.type, 'heartbeat');
  assert.equal(heartbeat.payload.companion_state, 'STOPPED');
  await setup.runtime.shutdown();
  await setup.run;
});

test('canonical emergency-stop command acknowledges only after the local latch and completes once', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  sendJson(
    setup.socket,
    gammaCommand(1, 'emergency-command', 'emergency_stop', { reason: 'Immediate stop.' })
  );
  const messages = [
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next(),
    await setup.queue.next()
  ];
  assert.deepEqual(messages.map((message) => message.type), [
    'command_ack',
    'sidecar_status',
    'minecraft_status',
    'state_snapshot',
    'terminal_result'
  ]);
  assert.equal(messages[0]?.payload.accepted, true);
  assert.equal(messages[1]?.payload.companion_state, 'STOPPED');
  assert.equal(messages[4]?.payload.outcome, 'completed');
  assert.equal(setup.runtime.status().emergencyStopActive, true);
  await setup.runtime.shutdown();
  await setup.run;
});

test('Gamma shutdown is delivered without a fabricated acknowledgment and exits cleanly', async (context) => {
  const setup = await startRuntime(context);
  await consumeStartup(setup.queue);
  sendJson(setup.socket, {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'shutdown',
    message_id: 'shutdown-runtime',
    connection_id: 'runtime-connection',
    sent_at: NOW,
    sequence: 1,
    trace_id: 'trace-shutdown-runtime',
    payload: { reason: 'Gamma shutdown.', leave_minecraft: true }
  });
  const exit = await setup.run;
  assert.equal(exit.category, 'gamma_shutdown');
  assert.equal(setup.queue.length, 0);
  assert.equal(setup.intervals.clearCount, 1);
});

test('signal hooks are scoped, removable, and main logs only fixed lifecycle categories', async () => {
  const target = new EventEmitter();
  let requests = 0;
  const dispose = installSignalHandlers(async () => {
    requests += 1;
  }, target as unknown as MinecraftSignalTarget);
  assert.equal(target.listenerCount('SIGINT'), 1);
  assert.equal(target.listenerCount('SIGTERM'), 1);
  target.emit('SIGINT');
  target.emit('SIGTERM');
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(requests, 2);
  dispose();
  dispose();
  assert.equal(target.listenerCount('SIGINT'), 0);
  assert.equal(target.listenerCount('SIGTERM'), 0);

  const mainTarget = new EventEmitter();
  const logs: string[] = [];
  let stopResolved!: () => void;
  const stopped = new Promise<void>((resolve) => {
    stopResolved = resolve;
  });
  let shutdownRequested = false;
  const main = runMinecraftSidecarMain({
    environment: { SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN },
    signalTarget: mainTarget as unknown as MinecraftSignalTarget,
    log: (message) => logs.push(message),
    createRuntime: () => ({
      run: async () => {
        await stopped;
        return Object.freeze({ category: 'signal' as const });
      },
      shutdown: async () => {
        if (!shutdownRequested) {
          shutdownRequested = true;
          stopResolved();
        }
      }
    })
  });
  await new Promise<void>((resolve) => setImmediate(resolve));
  mainTarget.emit('SIGTERM');
  assert.equal(await main, 0);
  assert.deepEqual(logs, [
    'Minecraft sidecar starting.',
    'Minecraft sidecar stopped: signal.'
  ]);
  assert.equal(logs.some((message) => message.includes(TOKEN)), false);
  assert.equal(mainTarget.listenerCount('SIGINT'), 0);
  assert.equal(mainTarget.listenerCount('SIGTERM'), 0);
});

test('main configuration failure registers no signals and never logs the rejected token', async () => {
  const target = new EventEmitter();
  const logs: string[] = [];
  const exitCode = await runMinecraftSidecarMain({
    environment: { SHANA_MINECRAFT_CONTROL_TOKEN: ` ${TOKEN}` },
    signalTarget: target as unknown as MinecraftSignalTarget,
    log: (message) => logs.push(message)
  });
  assert.equal(exitCode, 2);
  assert.deepEqual(logs, ['Minecraft sidecar configuration is invalid.']);
  assert.equal(logs.some((message) => message.includes(TOKEN)), false);
  assert.equal(target.listenerCount('SIGINT'), 0);
  assert.equal(target.listenerCount('SIGTERM'), 0);
});

function rawDataToBuffer(data: RawData): Buffer {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.concat(data);
  return Buffer.from(data);
}
