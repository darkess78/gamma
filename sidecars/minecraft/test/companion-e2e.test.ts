import assert from 'node:assert/strict';
import { once } from 'node:events';
import type { IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import test, { type TestContext } from 'node:test';

import WebSocket, { WebSocketServer, type RawData } from 'ws';

import { loadMinecraftSidecarRuntimeConfig } from '../src/config.js';
import {
  disconnectedMinecraftAdapterState,
  type ForwardSafety,
  type MinecraftAdapterConnectionConfig,
  type MinecraftAdapterEvent,
  type MinecraftAdapterEventHandler,
  type MinecraftAdapterState,
  type MinecraftDimension,
  type MinecraftMovementAdapter,
  type ObservedPlayer,
  type SafePosition
} from '../src/minecraft-adapter.js';
import {
  MinecraftSidecarRuntime,
  type MinecraftRuntimeTimer
} from '../src/runtime.js';

const TOKEN = 'companion-e2e-control-token-7Vm2';
const OWNER = 'Neety';
const CONNECTION_ID = 'companion-e2e-connection';
const START_MS = Date.parse('2026-07-10T18:00:00.000Z');

type WireFrame = Record<string, any>;
type FramePredicate = (frame: WireFrame) => boolean;

class DeterministicClock {
  wallMs = START_MS;
  monotonicMs = 1_000;

  readonly now = (): Date => new Date(this.wallMs);
  readonly monotonicNowMs = (): number => this.monotonicMs;

  advance(milliseconds: number): void {
    assert.equal(Number.isFinite(milliseconds) && milliseconds >= 0, true);
    this.wallMs += milliseconds;
    this.monotonicMs += milliseconds;
  }
}

class FakeIntervals {
  active = false;
  clearCount = 0;
  setCount = 0;
  callback: (() => void) | undefined;
  readonly handle: MinecraftRuntimeTimer = Object.freeze({
    unref: () => undefined
  });

  readonly set = (
    callback: () => void,
    _milliseconds: number
  ): MinecraftRuntimeTimer => {
    assert.equal(this.active, false);
    this.active = true;
    this.setCount += 1;
    this.callback = callback;
    return this.handle;
  };

  readonly clear = (handle: MinecraftRuntimeTimer): void => {
    assert.equal(handle, this.handle);
    assert.equal(this.active, true);
    this.active = false;
    this.clearCount += 1;
    this.callback = undefined;
  };
}

type PendingConnection = {
  resolve: () => void;
  reject: (error: Error) => void;
  signal: AbortSignal;
  onAbort: () => void;
};

class FakeMinecraftMovementAdapter implements MinecraftMovementAdapter {
  readonly events: string[];
  connectCalls = 0;
  disconnectCalls = 0;
  stopCalls = 0;
  clearCalls = 0;
  forwardActivations = 0;
  lookTargets: SafePosition[] = [];
  safetyInspections = 0;
  movementListenerPeak = 0;
  movementListenerCleanupCount = 0;
  forward = false;
  ownerValue: ObservedPlayer | undefined;
  forwardSafety: ForwardSafety = Object.freeze({
    kind: 'safe',
    candidate: Object.freeze({ x: 0.5, y: 64, z: 0 })
  });

  #state: MinecraftAdapterState = disconnectedMinecraftAdapterState();
  #botPosition: SafePosition = Object.freeze({ x: 0, y: 64, z: 0 });
  #eventHandler: MinecraftAdapterEventHandler | undefined;
  #pendingConnection: PendingConnection | undefined;
  readonly #movementHandlers = new Set<() => void>();

  constructor(events: string[]) {
    this.events = events;
  }

  connect(
    _config: MinecraftAdapterConnectionConfig,
    signal: AbortSignal
  ): Promise<void> {
    assert.equal(this.#state.connectionState, 'disconnected');
    assert.equal(this.#pendingConnection, undefined);
    this.connectCalls += 1;
    this.#state = Object.freeze({
      ...disconnectedMinecraftAdapterState(),
      connectionState: 'connecting'
    });
    this.#emit({ type: 'connecting' });
    return new Promise<void>((resolve, reject) => {
      const onAbort = (): void => {
        if (this.#pendingConnection?.onAbort !== onAbort) return;
        this.#pendingConnection = undefined;
        reject(new Error('fake Minecraft connection aborted'));
      };
      this.#pendingConnection = { resolve, reject, signal, onAbort };
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  }

  spawn(): void {
    const pending = this.#pendingConnection;
    assert.notEqual(pending, undefined);
    if (pending === undefined) return;
    pending.signal.removeEventListener('abort', pending.onAbort);
    this.#pendingConnection = undefined;
    this.#state = this.#connectedState();
    this.events.push('adapter:spawn');
    this.#emit({ type: 'spawned' });
    pending.resolve();
  }

  async disconnect(): Promise<void> {
    this.disconnectCalls += 1;
    this.clearControls();
    const pending = this.#pendingConnection;
    if (pending !== undefined) {
      pending.signal.removeEventListener('abort', pending.onAbort);
      this.#pendingConnection = undefined;
      pending.reject(new Error('fake Minecraft disconnected'));
    }
    const wasDisconnected = this.#state.connectionState === 'disconnected';
    this.#state = disconnectedMinecraftAdapterState('requested');
    if (!wasDisconnected) {
      this.events.push('adapter:disconnect');
      this.#emit({ type: 'disconnected', category: 'requested' });
    }
  }

  stopAllControls(): void {
    this.stopCalls += 1;
    this.events.push('adapter:stop-all');
    this.clearControls();
  }

  state(): MinecraftAdapterState {
    if (this.#state.connectionState !== 'connected') return this.#state;
    return this.#connectedState();
  }

  setEventHandler(handler: MinecraftAdapterEventHandler | undefined): void {
    this.#eventHandler = handler;
  }

  getBotPosition(): SafePosition | undefined {
    return this.#state.connectionState === 'connected'
      ? Object.freeze({ ...this.#botPosition })
      : undefined;
  }

  getDimension(): MinecraftDimension | undefined {
    return this.#state.dimension ?? undefined;
  }

  getPlayer(username: string): ObservedPlayer | undefined {
    const owner = this.ownerValue;
    if (
      this.#state.connectionState !== 'connected' ||
      owner === undefined ||
      owner.username.toLowerCase() !== username.toLowerCase()
    ) {
      return undefined;
    }
    return Object.freeze({
      username: owner.username,
      dimension: owner.dimension,
      position: Object.freeze({ ...owner.position })
    });
  }

  async lookAt(position: SafePosition): Promise<void> {
    assert.equal(this.#state.connectionState, 'connected');
    this.lookTargets.push(Object.freeze({ ...position }));
    this.events.push('adapter:look');
  }

  setForward(active: boolean): void {
    this.forward = active;
    if (active) this.forwardActivations += 1;
    this.events.push(`adapter:forward:${String(active)}`);
  }

  clearControls(): void {
    this.clearCalls += 1;
    this.forward = false;
    this.events.push('adapter:clear');
  }

  inspectForwardStep(target: ObservedPlayer): ForwardSafety {
    this.safetyInspections += 1;
    this.events.push('adapter:inspect');
    if (target.dimension !== this.#state.dimension) {
      return Object.freeze({ kind: 'dimension_mismatch' });
    }
    if (this.forwardSafety.kind !== 'safe') return this.forwardSafety;
    const dx = target.position.x - this.#botPosition.x;
    const dz = target.position.z - this.#botPosition.z;
    const horizontal = Math.hypot(dx, dz);
    if (!Number.isFinite(horizontal) || horizontal === 0) {
      return Object.freeze({ kind: 'blocked' });
    }
    const step = Math.min(0.5, horizontal);
    return Object.freeze({
      kind: 'safe',
      candidate: Object.freeze({
        x: this.#botPosition.x + (dx / horizontal) * step,
        y: this.#botPosition.y,
        z: this.#botPosition.z + (dz / horizontal) * step
      })
    });
  }

  onMovementTick(handler: () => void): () => void {
    assert.equal(this.#movementHandlers.size, 0);
    this.#movementHandlers.add(handler);
    this.movementListenerPeak = Math.max(
      this.movementListenerPeak,
      this.#movementHandlers.size
    );
    this.events.push('adapter:listener:add');
    let cleaned = false;
    return () => {
      if (cleaned) return;
      cleaned = true;
      if (this.#movementHandlers.delete(handler)) {
        this.movementListenerCleanupCount += 1;
        this.events.push('adapter:listener:remove');
      }
    };
  }

  tick(): void {
    this.events.push('adapter:tick');
    for (const handler of [...this.#movementHandlers]) handler();
  }

  setOwner(position: SafePosition | undefined): void {
    this.ownerValue =
      position === undefined
        ? undefined
        : Object.freeze({
            username: OWNER,
            dimension: 'minecraft:overworld',
            position: Object.freeze({ ...position })
          });
  }

  get movementListenerCount(): number {
    return this.#movementHandlers.size;
  }

  get hasPendingConnection(): boolean {
    return this.#pendingConnection !== undefined;
  }

  get hasEventHandler(): boolean {
    return this.#eventHandler !== undefined;
  }

  #connectedState(): MinecraftAdapterState {
    return Object.freeze({
      connectionState: 'connected',
      spawned: true,
      alive: true,
      negotiatedVersion: '1.21.11',
      dimension: 'minecraft:overworld',
      roundedPosition: Object.freeze({
        x: Math.round(this.#botPosition.x),
        y: Math.round(this.#botPosition.y),
        z: Math.round(this.#botPosition.z)
      }),
      health: 20,
      hunger: 20,
      lastDisconnectCategory: null
    });
  }

  #emit(event: MinecraftAdapterEvent): void {
    this.#eventHandler?.(Object.freeze(event));
  }
}

class FrameLog {
  readonly frames: WireFrame[] = [];
  readonly #waiters: Array<{
    predicate: FramePredicate;
    count: number;
    resolve: (frame: WireFrame) => void;
  }> = [];

  constructor(socket: WebSocket, events: string[]) {
    socket.on('message', (data, isBinary) => {
      assert.equal(isBinary, false);
      const frame = JSON.parse(rawDataToBuffer(data).toString('utf8')) as WireFrame;
      this.frames.push(frame);
      events.push(frameEvent(frame));
      this.#settleWaiters();
    });
  }

  matching(predicate: FramePredicate): WireFrame[] {
    return this.frames.filter(predicate);
  }

  waitForCount(predicate: FramePredicate, count = 1): Promise<WireFrame> {
    const existing = this.matching(predicate)[count - 1];
    if (existing !== undefined) return Promise.resolve(existing);
    return new Promise<WireFrame>((resolve) => {
      this.#waiters.push({ predicate, count, resolve });
    });
  }

  #settleWaiters(): void {
    for (let index = this.#waiters.length - 1; index >= 0; index -= 1) {
      const waiter = this.#waiters[index];
      if (waiter === undefined) continue;
      const frame = this.matching(waiter.predicate)[waiter.count - 1];
      if (frame === undefined) continue;
      this.#waiters.splice(index, 1);
      waiter.resolve(frame);
    }
  }
}

async function startServer(context: TestContext): Promise<{
  server: WebSocketServer;
  url: string;
}> {
  const server = new WebSocketServer({
    host: '127.0.0.1',
    port: 0,
    perMessageDeflate: false
  });
  await once(server, 'listening');
  const address = server.address() as AddressInfo;
  context.after(async () => {
    for (const client of server.clients) client.terminate();
    if (server.address() !== null) {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
  return {
    server,
    url: `ws://127.0.0.1:${address.port}/v1/minecraft/control`
  };
}

function nextConnection(
  server: WebSocketServer
): Promise<[WebSocket, IncomingMessage]> {
  return once(server, 'connection') as Promise<[WebSocket, IncomingMessage]>;
}

function sendJson(socket: WebSocket, value: unknown): void {
  assert.equal(socket.readyState, WebSocket.OPEN);
  socket.send(JSON.stringify(value));
}

function welcome(): WireFrame {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'welcome',
    message_id: 'gamma-welcome-companion-e2e',
    connection_id: CONNECTION_ID,
    sent_at: new Date(START_MS).toISOString(),
    sequence: 0,
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
  clock: DeterministicClock,
  sequence: number,
  commandId: string,
  name: string,
  args: Record<string, unknown>
): WireFrame {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'command',
    message_id: `gamma-message-${commandId}`,
    connection_id: CONNECTION_ID,
    sent_at: clock.now().toISOString(),
    sequence,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: {
      name,
      deadline_at: new Date(clock.wallMs + 60_000).toISOString(),
      arguments: args
    }
  };
}

function shutdown(clock: DeterministicClock, sequence: number): WireFrame {
  return {
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'shutdown',
    message_id: 'gamma-message-shutdown-e2e',
    connection_id: CONNECTION_ID,
    sent_at: clock.now().toISOString(),
    sequence,
    trace_id: 'trace-shutdown-e2e',
    payload: { reason: 'End deterministic companion test.', leave_minecraft: true }
  };
}

function isType(type: string): FramePredicate {
  return (frame) => frame.type === type;
}

function isCommandFrame(type: string, commandId: string): FramePredicate {
  return (frame) => frame.type === type && frame.command_id === commandId;
}

function isCommandState(commandId: string, state: string): FramePredicate {
  return (frame) =>
    frame.type === 'state_snapshot' &&
    frame.command_id === commandId &&
    frame.payload?.companion_state === state;
}

function acceptedAck(frame: WireFrame, commandId: string): void {
  assert.equal(frame.type, 'command_ack');
  assert.equal(frame.command_id, commandId);
  assert.equal(frame.trace_id, `trace-${commandId}`);
  assert.equal(frame.payload.accepted, true);
  assert.equal(frame.payload.failure, null);
}

function terminal(
  frame: WireFrame,
  commandId: string,
  outcome: string,
  failureCode: string | null = null
): void {
  assert.equal(frame.type, 'terminal_result');
  assert.equal(frame.command_id, commandId);
  assert.equal(frame.trace_id, `trace-${commandId}`);
  assert.equal(frame.payload.outcome, outcome);
  assert.equal(frame.payload.failure?.code ?? null, failureCode);
}

function frameEvent(frame: WireFrame): string {
  const commandId = typeof frame.command_id === 'string' ? frame.command_id : '-';
  if (frame.type === 'command_ack') {
    return `frame:command_ack:${commandId}:${String(frame.payload?.accepted)}`;
  }
  if (frame.type === 'terminal_result') {
    return `frame:terminal_result:${commandId}:${String(frame.payload?.outcome)}`;
  }
  if (frame.type === 'state_snapshot') {
    return `frame:state_snapshot:${commandId}:${String(frame.payload?.companion_state)}`;
  }
  return `frame:${String(frame.type)}:${commandId}`;
}

function assertEventOrder(events: readonly string[], ...expected: string[]): void {
  let previous = -1;
  for (const value of expected) {
    const index = events.indexOf(value, previous + 1);
    assert.notEqual(index, -1, `Missing ordered event: ${value}`);
    assert.equal(index > previous, true, `Event out of order: ${value}`);
    previous = index;
  }
}

function assertCommandCorrelation(frames: readonly WireFrame[], commandId: string): void {
  const correlated = frames.filter((frame) => frame.command_id === commandId);
  assert.equal(correlated.length > 0, true);
  for (const frame of correlated) {
    assert.equal(frame.trace_id, `trace-${commandId}`);
  }
}

async function flushAsyncWork(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test(
  'complete Gamma control flow drives the direct-steering companion without a Minecraft server',
  { timeout: 15_000 },
  async (context) => {
    const events: string[] = [];
    const clock = new DeterministicClock();
    const intervals = new FakeIntervals();
    const adapter = new FakeMinecraftMovementAdapter(events);
    const running = await startServer(context);
    const config = loadMinecraftSidecarRuntimeConfig({
      SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL: running.url,
      SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
      SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'companion-e2e-sidecar',
      SHANA_MINECRAFT_HEARTBEAT_SECONDS: '5',
      SHANA_MINECRAFT_OWNER_USERNAME: OWNER
    });
    const runtime = new MinecraftSidecarRuntime(config, {
      now: clock.now,
      monotonicNowMs: clock.monotonicNowMs,
      setInterval: intervals.set,
      clearInterval: intervals.clear,
      createMinecraftAdapter: () => adapter
    });
    let runtimeFinished = false;
    context.after(async () => {
      if (!runtimeFinished) await runtime.shutdown('requested');
    });

    const connection = nextConnection(running.server);
    const run = runtime.run();
    const [socket, request] = await connection;
    const frames = new FrameLog(socket, events);
    const hello = await frames.waitForCount(isType('hello'));
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(hello.payload.minecraft_library_version, '4.37.1');
    assert.equal(hello.payload.pathfinder_version, 'not-installed');
    assert.equal(hello.payload.companion_state, 'DISCONNECTED');
    sendJson(socket, welcome());

    const startup = [
      await frames.waitForCount(isType('sidecar_status')),
      await frames.waitForCount(isType('minecraft_status')),
      await frames.waitForCount(isType('state_snapshot'))
    ];
    assert.deepEqual(
      startup.map((frame) => frame.type),
      ['sidecar_status', 'minecraft_status', 'state_snapshot']
    );
    assert.equal(startup[1]?.payload.connection_state, 'disconnected');
    assert.equal(startup[2]?.payload.companion_state, 'DISCONNECTED');
    assert.equal(intervals.active, true);

    let gammaSequence = 1;
    const sendCommand = (
      commandId: string,
      name: string,
      args: Record<string, unknown>
    ): void => {
      sendJson(socket, command(clock, gammaSequence++, commandId, name, args));
    };

    sendCommand('join-1', 'join', {});
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'join-1')),
      'join-1'
    );
    assert.equal(adapter.connectCalls, 1);
    assert.equal(adapter.hasPendingConnection, true);
    adapter.spawn();
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'join-1')),
      'join-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('join-1', 'IDLE'));
    assert.equal(runtime.status().minecraftConnectionState, 'connected');

    adapter.setOwner({ x: 8, y: 64, z: 0 });
    sendCommand('follow-1', 'follow_owner', {
      follow_distance: 3,
      lease_duration_seconds: 30
    });
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'follow-1')),
      'follow-1'
    );
    await frames.waitForCount(isCommandState('follow-1', 'FOLLOWING'));
    assert.equal(adapter.movementListenerCount, 1);
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    assert.equal(adapter.forwardActivations >= 1, true);
    assert.equal(adapter.safetyInspections >= 2, true);

    const waitBoundary = events.length;
    sendCommand('wait-1', 'wait_here', {});
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'follow-1')),
      'follow-1',
      'cancelled'
    );
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'wait-1')),
      'wait-1'
    );
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'wait-1')),
      'wait-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('wait-1', 'WAITING'));
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);
    assertEventOrder(
      events.slice(waitBoundary),
      'adapter:clear',
      'adapter:listener:remove',
      'frame:terminal_result:follow-1:cancelled',
      'frame:command_ack:wait-1:true',
      'frame:terminal_result:wait-1:completed'
    );

    adapter.setOwner({ x: 8, y: 64, z: 0 });
    sendCommand('come-1', 'come_here', { arrival_distance: 3 });
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'come-1')),
      'come-1'
    );
    await frames.waitForCount(isCommandState('come-1', 'RETURNING'));
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    adapter.setOwner({ x: 5, y: 64, z: 0 });
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    adapter.setOwner({ x: 2, y: 64, z: 0 });
    adapter.tick();
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'come-1')),
      'come-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('come-1', 'IDLE'));
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);

    const lookCount = adapter.lookTargets.length;
    sendCommand('look-1', 'look_at_owner', { duration_seconds: 2 });
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'look-1')),
      'look-1'
    );
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'look-1')),
      'look-1',
      'completed'
    );
    assert.equal(adapter.lookTargets.length, lookCount + 1);
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);

    adapter.setOwner({ x: 8, y: 64, z: 0 });
    sendCommand('follow-owner-loss', 'follow_owner', {
      follow_distance: 3,
      lease_duration_seconds: 30
    });
    acceptedAck(
      await frames.waitForCount(
        isCommandFrame('command_ack', 'follow-owner-loss')
      ),
      'follow-owner-loss'
    );
    await frames.waitForCount(
      isCommandState('follow-owner-loss', 'FOLLOWING')
    );
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    adapter.setOwner(undefined);
    const clearsBeforeOwnerLoss = adapter.clearCalls;
    adapter.tick();
    assert.equal(adapter.clearCalls > clearsBeforeOwnerLoss, true);
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 1);
    clock.advance(5_000);
    adapter.tick();
    assert.equal(adapter.forward, false);
    assert.equal(
      frames.matching(
        isCommandFrame('terminal_result', 'follow-owner-loss')
      ).length,
      0
    );
    clock.advance(5_000);
    adapter.tick();
    terminal(
      await frames.waitForCount(
        isCommandFrame('terminal_result', 'follow-owner-loss')
      ),
      'follow-owner-loss',
      'failed',
      'OWNER_NOT_PRESENT'
    );
    await frames.waitForCount(isCommandState('follow-owner-loss', 'IDLE'));
    assert.equal(adapter.movementListenerCount, 0);
    adapter.tick();
    assert.equal(
      frames.matching(
        isCommandFrame('terminal_result', 'follow-owner-loss')
      ).length,
      1
    );

    adapter.setOwner({ x: 8, y: 64, z: 0 });
    sendCommand('follow-stop', 'follow_owner', {
      follow_distance: 3,
      lease_duration_seconds: 30
    });
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'follow-stop')),
      'follow-stop'
    );
    await frames.waitForCount(isCommandState('follow-stop', 'FOLLOWING'));
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    const stopBoundary = events.length;
    sendCommand('stop-1', 'stop', {});
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'follow-stop')),
      'follow-stop',
      'cancelled'
    );
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'stop-1')),
      'stop-1'
    );
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'stop-1')),
      'stop-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('stop-1', 'IDLE'));
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);
    assertEventOrder(
      events.slice(stopBoundary),
      'adapter:clear',
      'adapter:listener:remove',
      'frame:terminal_result:follow-stop:cancelled',
      'frame:command_ack:stop-1:true',
      'frame:terminal_result:stop-1:completed'
    );

    sendCommand('follow-emergency', 'follow_owner', {
      follow_distance: 3,
      lease_duration_seconds: 30
    });
    acceptedAck(
      await frames.waitForCount(
        isCommandFrame('command_ack', 'follow-emergency')
      ),
      'follow-emergency'
    );
    await frames.waitForCount(isCommandState('follow-emergency', 'FOLLOWING'));
    adapter.tick();
    await flushAsyncWork();
    assert.equal(adapter.forward, true);
    const emergencyBoundary = events.length;
    sendCommand('emergency-1', 'emergency_stop', {
      reason: 'Deterministic emergency stop.'
    });
    terminal(
      await frames.waitForCount(
        isCommandFrame('terminal_result', 'follow-emergency')
      ),
      'follow-emergency',
      'cancelled'
    );
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'emergency-1')),
      'emergency-1'
    );
    terminal(
      await frames.waitForCount(
        isCommandFrame('terminal_result', 'emergency-1')
      ),
      'emergency-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('emergency-1', 'STOPPED'));
    assert.equal(runtime.status().emergencyStopActive, true);
    assert.equal(runtime.status().companionState, 'STOPPED');
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);
    assertEventOrder(
      events.slice(emergencyBoundary),
      'adapter:clear',
      'adapter:listener:remove',
      'frame:terminal_result:follow-emergency:cancelled',
      'frame:command_ack:emergency-1:true',
      'frame:terminal_result:emergency-1:completed'
    );

    sendCommand('follow-rejected', 'follow_owner', {
      follow_distance: 3,
      lease_duration_seconds: 30
    });
    const rejected = await frames.waitForCount(
      isCommandFrame('command_ack', 'follow-rejected')
    );
    assert.equal(rejected.trace_id, 'trace-follow-rejected');
    assert.equal(rejected.payload.accepted, false);
    assert.equal(rejected.payload.failure.code, 'EMERGENCY_STOP_ACTIVE');
    assert.equal(
      frames.matching(
        isCommandFrame('terminal_result', 'follow-rejected')
      ).length,
      0
    );

    sendCommand('leave-1', 'leave', {});
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'leave-1')),
      'leave-1'
    );
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'leave-1')),
      'leave-1',
      'completed'
    );
    await frames.waitForCount(isCommandState('leave-1', 'STOPPED'));
    assert.equal(runtime.status().emergencyStopActive, true);
    assert.equal(runtime.status().minecraftConnectionState, 'disconnected');
    assert.equal(adapter.movementListenerCount, 0);

    sendCommand('join-2', 'join', {});
    acceptedAck(
      await frames.waitForCount(isCommandFrame('command_ack', 'join-2')),
      'join-2'
    );
    assert.equal(adapter.connectCalls, 2);
    adapter.spawn();
    terminal(
      await frames.waitForCount(isCommandFrame('terminal_result', 'join-2')),
      'join-2',
      'completed'
    );
    await frames.waitForCount(isCommandState('join-2', 'IDLE'));
    assert.equal(runtime.status().emergencyStopActive, false);
    assert.equal(runtime.status().companionState, 'IDLE');

    const acceptedCommands = [
      'join-1',
      'follow-1',
      'wait-1',
      'come-1',
      'look-1',
      'follow-owner-loss',
      'follow-stop',
      'stop-1',
      'follow-emergency',
      'emergency-1',
      'leave-1',
      'join-2'
    ];
    for (const commandId of acceptedCommands) {
      assert.equal(
        frames.matching(isCommandFrame('command_ack', commandId)).length,
        1,
        `${commandId} acknowledgment count`
      );
      assert.equal(
        frames.matching(isCommandFrame('terminal_result', commandId)).length,
        1,
        `${commandId} terminal count`
      );
      assertCommandCorrelation(frames.frames, commandId);
    }
    assert.equal(
      frames.matching(isCommandFrame('command_ack', 'follow-rejected')).length,
      1
    );
    assertCommandCorrelation(frames.frames, 'follow-rejected');
    assert.equal(adapter.movementListenerPeak, 1);
    assert.equal(
      adapter.movementListenerCleanupCount >= 5,
      true,
      'every long movement command cleaned its listener'
    );

    const socketClosed = once(socket, 'close');
    sendJson(socket, shutdown(clock, gammaSequence++));
    const exit = await run;
    await socketClosed;
    runtimeFinished = true;
    assert.equal(exit.category, 'gamma_shutdown');
    assert.equal(runtime.status().lifecycle, 'stopped');
    assert.equal(runtime.status().heartbeatActive, false);
    assert.equal(intervals.active, false);
    assert.equal(intervals.clearCount, 1);
    assert.equal(adapter.forward, false);
    assert.equal(adapter.movementListenerCount, 0);
    assert.equal(adapter.hasPendingConnection, false);
    assert.equal(adapter.hasEventHandler, false);
    assert.equal(adapter.state().connectionState, 'disconnected');
    assert.equal(adapter.disconnectCalls >= 2, true);
    assert.equal(running.server.clients.size, 0);
  }
);

function rawDataToBuffer(data: RawData): Buffer {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.concat(data);
  return Buffer.from(data);
}
