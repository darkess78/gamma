import assert from 'node:assert/strict';
import test from 'node:test';

import { MinecraftCommandDispatcher } from '../src/command-dispatcher.js';
import {
  DEFAULT_MINECRAFT_BOT_USERNAME,
  DEFAULT_MINECRAFT_SERVER_HOST,
  DEFAULT_MINECRAFT_SERVER_PORT,
  SUPPORTED_MINECRAFT_VERSION,
  loadMinecraftSidecarRuntimeConfig
} from '../src/config.js';
import {
  disconnectedMinecraftAdapterState,
  type MinecraftAdapter,
  type MinecraftAdapterConnectionConfig,
  type MinecraftAdapterEvent,
  type MinecraftAdapterEventHandler,
  type MinecraftAdapterState,
  type MinecraftDimension
} from '../src/minecraft-adapter.js';
import {
  parseProtocolMessage,
  type CancelCommandMessage,
  type CommandMessage,
  type EmergencyStopMessage
} from '../src/protocol.js';
import {
  type GammaControlMessage,
  type MinecraftControlClientOptions,
  type SidecarOutboundMessage
} from '../src/control-client.js';
import { MinecraftSidecarRuntime } from '../src/runtime.js';

const TOKEN = 'dispatcher-control-token-5Rm9';
const NOW = '2026-07-10T18:00:00.000Z';
const RAW_SENTINEL = 'raw-minecraft-error-secret-7L';

type ConnectMode = 'pending' | 'fail';

type Deferred<T> = Readonly<{
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
}>;

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return Object.freeze({ promise, resolve });
}

type DisconnectBarrier = Readonly<{
  entered: Promise<void>;
  release: () => void;
}>;

class FakeMinecraftAdapter implements MinecraftAdapter {
  connectMode: ConnectMode = 'pending';
  connectCalls = 0;
  disconnectCalls = 0;
  stopCalls = 0;
  lastConfig: MinecraftAdapterConnectionConfig | undefined;
  handler: MinecraftAdapterEventHandler | undefined;
  stateValue: MinecraftAdapterState = disconnectedMinecraftAdapterState();
  #pending:
    | {
        resolve: () => void;
        reject: () => void;
        signal: AbortSignal;
        onAbort: () => void;
      }
    | undefined;
  #disconnectBarrier:
    | Readonly<{
        entered: Deferred<void>;
        released: Deferred<void>;
        stateBeforeWait: boolean;
      }>
    | undefined;

  async connect(
    config: MinecraftAdapterConnectionConfig,
    signal: AbortSignal
  ): Promise<void> {
    this.connectCalls += 1;
    this.lastConfig = config;
    this.stateValue = Object.freeze({
      ...disconnectedMinecraftAdapterState(),
      connectionState: 'connecting'
    });
    this.emit({ type: 'connecting' });
    if (this.connectMode === 'fail') throw new Error(RAW_SENTINEL);
    await new Promise<void>((resolve, reject) => {
      const onAbort = (): void => {
        this.#pending = undefined;
        reject(new Error('aborted'));
      };
      this.#pending = {
        resolve: () => {
          signal.removeEventListener('abort', onAbort);
          this.#pending = undefined;
          resolve();
        },
        reject: () => {
          signal.removeEventListener('abort', onAbort);
          this.#pending = undefined;
          reject(new Error(RAW_SENTINEL));
        },
        signal,
        onAbort
      };
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  }

  async disconnect(): Promise<void> {
    this.disconnectCalls += 1;
    const pending = this.#pending;
    if (pending !== undefined) {
      pending.signal.removeEventListener('abort', pending.onAbort);
      pending.reject();
    }
    const barrier = this.#disconnectBarrier;
    this.#disconnectBarrier = undefined;
    if (barrier?.stateBeforeWait === true) {
      this.stateValue = disconnectedMinecraftAdapterState('requested');
    }
    barrier?.entered.resolve(undefined);
    if (barrier !== undefined) await barrier.released.promise;
    if (barrier?.stateBeforeWait !== true) {
      this.stateValue = disconnectedMinecraftAdapterState('requested');
    }
  }

  blockNextDisconnect(stateBeforeWait = false): DisconnectBarrier {
    const entered = deferred<void>();
    const released = deferred<void>();
    this.#disconnectBarrier = Object.freeze({
      entered,
      released,
      stateBeforeWait
    });
    return Object.freeze({
      entered: entered.promise,
      release: () => released.resolve(undefined)
    });
  }

  stopAllControls(): void {
    this.stopCalls += 1;
  }

  state(): MinecraftAdapterState {
    return Object.freeze({
      ...this.stateValue,
      roundedPosition:
        this.stateValue.roundedPosition === null
          ? null
          : Object.freeze({ ...this.stateValue.roundedPosition })
    });
  }

  setEventHandler(handler: MinecraftAdapterEventHandler | undefined): void {
    this.handler = handler;
  }

  spawn(
    dimension: MinecraftDimension | null = 'minecraft:overworld',
    version = '1.21.11'
  ): void {
    this.stateValue = Object.freeze({
      connectionState: 'connected',
      spawned: true,
      alive: true,
      negotiatedVersion: version,
      dimension,
      roundedPosition: Object.freeze({ x: 10, y: 64, z: -4 }),
      health: 19.5,
      hunger: 18,
      lastDisconnectCategory: null
    });
    this.emit({ type: 'spawned' });
    this.#pending?.resolve();
  }

  failPending(): void {
    this.#pending?.reject();
  }

  emit(event: MinecraftAdapterEvent): void {
    this.handler?.(event);
  }
}

function runtimeConfig(overrides: Record<string, string | undefined> = {}) {
  return loadMinecraftSidecarRuntimeConfig({
    SHANA_MINECRAFT_CONTROL_TOKEN: TOKEN,
    SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'dispatcher-sidecar',
    ...overrides
  });
}

function setup(
  adapter = new FakeMinecraftAdapter(),
  options: Readonly<{
    afterSend?: (message: SidecarOutboundMessage) => void | Promise<void>;
  }> = {}
) {
  const messages: SidecarOutboundMessage[] = [];
  let monotonic = 1_000;
  let deliveryFailures = 0;
  type TestTimer = Readonly<{ handle: NodeJS.Timeout; unref: () => void }>;
  const dispatcher = new MinecraftCommandDispatcher({
    config: runtimeConfig(),
    adapter,
    send: async (message) => {
      messages.push(message);
      await options.afterSend?.(message);
    },
    now: () => new Date(NOW),
    monotonicNowMs: () => monotonic,
    uptimeSeconds: () => 12,
    setTimeout: (callback, milliseconds) =>
      Object.freeze({
        handle: setTimeout(callback, milliseconds),
        unref: () => undefined
      }),
    clearTimeout: (timer) =>
      clearTimeout((timer as TestTimer).handle),
    onDeliveryFailure: () => {
      deliveryFailures += 1;
    }
  });
  dispatcher.beginSession(1_000, 600);
  return {
    adapter,
    dispatcher,
    messages,
    advance: (milliseconds: number) => {
      monotonic += milliseconds;
    },
    deliveryFailures: () => deliveryFailures
  };
}

function command(
  commandId: string,
  name: string,
  args: Record<string, unknown>,
  options: {
    deadline?: string;
    traceId?: string;
    sequence?: number;
    sentAt?: string;
  } = {}
): CommandMessage {
  const parsed = parseProtocolMessage({
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'command',
    message_id: `message-${commandId}-${options.sequence ?? 1}`,
    connection_id: 'dispatcher-connection',
    sent_at: options.sentAt ?? NOW,
    sequence: options.sequence ?? 1,
    trace_id: options.traceId ?? `trace-${commandId}`,
    command_id: commandId,
    payload: {
      name,
      deadline_at: options.deadline ?? '2026-07-10T18:01:00.000Z',
      arguments: args
    }
  });
  assert.equal(parsed.type, 'command');
  return parsed as CommandMessage;
}

function cancel(commandId: string): CancelCommandMessage {
  const parsed = parseProtocolMessage({
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'cancel_command',
    message_id: `cancel-${commandId}`,
    connection_id: 'dispatcher-connection',
    sent_at: NOW,
    sequence: 10,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: { reason: 'Operator cancelled.' }
  });
  assert.equal(parsed.type, 'cancel_command');
  return parsed as CancelCommandMessage;
}

function emergency(commandId: string): EmergencyStopMessage {
  const parsed = parseProtocolMessage({
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'emergency_stop',
    message_id: `emergency-${commandId}`,
    connection_id: 'dispatcher-connection',
    sent_at: NOW,
    sequence: 20,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: { reason: 'Immediate stop.' }
  });
  assert.equal(parsed.type, 'emergency_stop');
  return parsed as EmergencyStopMessage;
}

function messagesOfType<T extends SidecarOutboundMessage['type']>(
  messages: readonly SidecarOutboundMessage[],
  type: T
): Array<Extract<SidecarOutboundMessage, { type: T }>> {
  return messages.filter(
    (message): message is Extract<SidecarOutboundMessage, { type: T }> =>
      message.type === type
  );
}

async function turn(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

class FakeControlClient {
  readonly sent: SidecarOutboundMessage[] = [];
  handler:
    | ((message: GammaControlMessage) => void | Promise<void>)
    | undefined;
  closeCalls = 0;

  async connect() {
    const parsed = parseProtocolMessage({
      protocol: 'gamma.minecraft',
      version: 1,
      type: 'welcome',
      message_id: 'runtime-welcome',
      connection_id: 'runtime-connection',
      sent_at: NOW,
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
    });
    assert.equal(parsed.type, 'welcome');
    return parsed;
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
  }

  waitForDisconnect(): Promise<never> {
    return new Promise<never>(() => undefined);
  }

  async sendHeartbeat(): Promise<void> {}

  async sendSidecarMessage(message: SidecarOutboundMessage): Promise<void> {
    this.sent.push(message);
  }

  setMessageHandler(
    handler:
      | ((message: GammaControlMessage) => void | Promise<void>)
      | undefined
  ): void {
    this.handler = handler;
  }

  status() {
    return Object.freeze({
      state: 'ready' as const,
      connectionId: 'runtime-connection',
      lastReceivedSequence: 0,
      nextOutboundSequence: 1
    });
  }

  async deliver(message: GammaControlMessage): Promise<void> {
    await this.handler?.(message);
  }
}

test('Minecraft server configuration is loopback-only, exact-version, offline-only, and bounded', () => {
  const defaults = runtimeConfig();
  assert.equal(defaults.minecraftServerHost, DEFAULT_MINECRAFT_SERVER_HOST);
  assert.equal(defaults.minecraftServerPort, DEFAULT_MINECRAFT_SERVER_PORT);
  assert.equal(defaults.minecraftVersion, SUPPORTED_MINECRAFT_VERSION);
  assert.equal(defaults.minecraftAccountMode, 'offline');
  assert.equal(defaults.minecraftBotUsername, DEFAULT_MINECRAFT_BOT_USERNAME);
  assert.equal(Object.isFrozen(defaults), true);

  for (const host of ['127.0.0.1', '127.255.1.2', '::1', '::ffff:127.0.0.9']) {
    assert.equal(
      runtimeConfig({ SHANA_MINECRAFT_SERVER_HOST: host }).minecraftServerHost,
      host
    );
  }
  for (const host of [
    'localhost',
    '0.0.0.0',
    '192.168.1.4',
    '169.254.1.1',
    '[::1]',
    '::',
    'example.test'
  ]) {
    assert.throws(
      () => runtimeConfig({ SHANA_MINECRAFT_SERVER_HOST: host }),
      /literal loopback/u
    );
  }
  for (const port of ['1', '25565', '65535']) {
    assert.equal(
      runtimeConfig({ SHANA_MINECRAFT_SERVER_PORT: port }).minecraftServerPort,
      Number(port)
    );
  }
  for (const port of ['0', '65536', '025565', '+25565', '25565.0', ' 25565']) {
    assert.throws(
      () => runtimeConfig({ SHANA_MINECRAFT_SERVER_PORT: port }),
      /server port/u
    );
  }
  assert.throws(
    () => runtimeConfig({ SHANA_MINECRAFT_VERSION: '1.21.10' }),
    /exactly 1\.21\.11/u
  );
  assert.throws(
    () => runtimeConfig({ SHANA_MINECRAFT_ACCOUNT_MODE: 'microsoft' }),
    /must be offline/u
  );
  for (const username of ['', 'ab', 'space name', 'x'.repeat(17)]) {
    assert.throws(
      () => runtimeConfig({ SHANA_MINECRAFT_BOT_USERNAME: username }),
      /bot username/u
    );
  }
});

test('full runtime advertises Mineflayer, never autojoins, and routes explicit join through the adapter', async () => {
  const adapter = new FakeMinecraftAdapter();
  const client = new FakeControlClient();
  let clientOptions: MinecraftControlClientOptions | undefined;
  const runtime = new MinecraftSidecarRuntime(runtimeConfig(), {
    createControlClient: (options) => {
      clientOptions = options;
      return client;
    },
    createMinecraftAdapter: () => adapter,
    now: () => new Date(NOW),
    monotonicNowMs: () => 1_000
  });
  assert.equal(adapter.connectCalls, 0);
  assert.equal(clientOptions?.hello.minecraftLibraryVersion, '4.37.1');
  assert.equal(clientOptions?.hello.pathfinderVersion, 'not-installed');

  const run = runtime.run();
  await turn();
  assert.equal(adapter.connectCalls, 0);
  assert.deepEqual(
    client.sent.slice(0, 3).map((message) => message.type),
    ['sidecar_status', 'minecraft_status', 'state_snapshot']
  );
  await client.deliver(command('runtime-join', 'join', {}));
  await turn();
  assert.equal(adapter.connectCalls, 1);
  adapter.spawn();
  await turn();
  await turn();
  assert.equal(
    messagesOfType(client.sent, 'terminal_result').at(-1)?.payload.outcome,
    'completed'
  );
  await runtime.shutdown('requested');
  assert.equal((await run).category, 'requested');
  assert.equal(adapter.disconnectCalls >= 1, true);
  assert.equal(client.closeCalls >= 1, true);
});

test('dispatcher is inactive until join and successful join reports bounded connected state once', async () => {
  const setupValue = setup();
  assert.equal(setupValue.adapter.connectCalls, 0);
  assert.equal(setupValue.dispatcher.status().minecraft.connectionState, 'disconnected');

  const join = command('join-success', 'join', {});
  const work = setupValue.dispatcher.handleCommand(join);
  await turn();
  assert.equal(setupValue.adapter.connectCalls, 1);
  assert.equal(messagesOfType(setupValue.messages, 'command_ack').length, 1);
  assert.equal(messagesOfType(setupValue.messages, 'terminal_result').length, 0);
  assert.equal(
    messagesOfType(setupValue.messages, 'minecraft_status')[0]?.payload.connection_state,
    'connecting'
  );
  assert.deepEqual(setupValue.adapter.lastConfig, {
    minecraftServerHost: '127.0.0.1',
    minecraftServerPort: 25_565,
    minecraftVersion: '1.21.11',
    minecraftAccountMode: 'offline',
    minecraftBotUsername: 'Shana'
  });
  assert.equal(JSON.stringify(setupValue.adapter.lastConfig).includes(TOKEN), false);

  setupValue.adapter.spawn();
  await work;
  await setupValue.dispatcher.waitForEvents();
  const results = messagesOfType(setupValue.messages, 'terminal_result');
  assert.equal(results.length, 1);
  assert.equal(results[0]?.payload.outcome, 'completed');
  assert.equal(results[0]?.payload.failure, null);
  assert.equal(setupValue.dispatcher.status().companionState, 'IDLE');
  assert.equal(setupValue.dispatcher.status().minecraft.dimension, 'minecraft:overworld');
  const snapshots = messagesOfType(setupValue.messages, 'state_snapshot');
  assert.deepEqual(snapshots.at(-1)?.payload.rounded_position, { x: 10, y: 64, z: -4 });
  assert.equal(snapshots.at(-1)?.payload.health, 19.5);
  assert.equal(snapshots.at(-1)?.payload.owner_present, false);
});

test('join failure, timeout, wrong dimension, and version mismatch clean partial state without retry', async (context) => {
  await context.test('connection failure', async () => {
    const adapter = new FakeMinecraftAdapter();
    adapter.connectMode = 'fail';
    const value = setup(adapter);
    await value.dispatcher.handleCommand(command('join-fail', 'join', {}));
    const result = messagesOfType(value.messages, 'terminal_result')[0];
    assert.equal(result?.payload.outcome, 'failed');
    assert.equal(result?.payload.failure?.code, 'MINECRAFT_SERVER_DISCONNECTED');
    assert.equal(JSON.stringify(value.messages).includes(RAW_SENTINEL), false);
    assert.equal(adapter.connectCalls, 1);
    assert.equal(adapter.disconnectCalls >= 1, true);
  });

  await context.test('deadline timeout', async () => {
    const value = setup();
    await value.dispatcher.handleCommand(
      command('join-timeout', 'join', {}, {
        deadline: '2026-07-10T18:00:00.010Z'
      })
    );
    const result = messagesOfType(value.messages, 'terminal_result')[0];
    assert.equal(result?.payload.outcome, 'timed_out');
    assert.equal(result?.payload.failure?.code, 'DEADLINE_EXCEEDED');
    assert.equal(value.adapter.disconnectCalls >= 1, true);
  });

  for (const [label, dimension, version, code] of [
    ['wrong dimension', 'minecraft:the_nether', '1.21.11', 'UNSUPPORTED_DIMENSION'],
    ['version mismatch', 'minecraft:overworld', '1.21.10', 'PROTOCOL_MISMATCH']
  ] as const) {
    await context.test(label, async () => {
      const value = setup();
      const safeLabel = label.replace(' ', '-');
      const work = value.dispatcher.handleCommand(
        command(`join-${safeLabel}`, 'join', {})
      );
      await turn();
      value.adapter.spawn(dimension, version);
      await work;
      const result = messagesOfType(value.messages, 'terminal_result')[0];
      assert.equal(result?.payload.outcome, 'failed');
      assert.equal(result?.payload.failure?.code, code);
      assert.equal(value.adapter.state().connectionState, 'disconnected');
      assert.equal(value.adapter.connectCalls, 1);
    });
  }
});

test('cancellation and control loss stop a pending join immediately and terminalize at most once', async () => {
  const value = setup();
  const work = value.dispatcher.handleCommand(command('join-cancel', 'join', {}));
  await turn();
  await value.dispatcher.cancelCommand(cancel('join-cancel'));
  await work;
  const results = messagesOfType(value.messages, 'terminal_result');
  assert.equal(results.length, 1);
  assert.equal(results[0]?.payload.outcome, 'cancelled');
  assert.equal(value.adapter.stopCalls >= 1, true);
  assert.equal(value.adapter.disconnectCalls >= 1, true);

  const second = setup();
  const lost = second.dispatcher.handleCommand(command('join-control-loss', 'join', {}));
  await turn();
  const beforeLoss = second.messages.length;
  await second.dispatcher.controlDisconnected();
  await lost;
  assert.equal(second.adapter.stopCalls >= 1, true);
  assert.equal(second.adapter.disconnectCalls >= 1, true);
  assert.equal(second.messages.length, beforeLoss);
  assert.equal(messagesOfType(second.messages, 'terminal_result').length, 0);
});

test('duplicates replay exact cached correlation without execution and conflicts are rejected', async () => {
  const value = setup();
  const report = command('duplicate-report', 'report_status', {
    detail_level: 'standard'
  });
  value.advance(25);
  await value.dispatcher.handleCommand(report);
  const firstAck = messagesOfType(value.messages, 'command_ack')[0];
  const firstResult = messagesOfType(value.messages, 'terminal_result')[0];
  await value.dispatcher.handleCommand(report);
  const acknowledgments = messagesOfType(value.messages, 'command_ack');
  const results = messagesOfType(value.messages, 'terminal_result');
  assert.equal(acknowledgments.length, 2);
  assert.equal(results.length, 2);
  assert.deepEqual(acknowledgments[1], firstAck);
  assert.deepEqual(results[1], firstResult);
  assert.equal(value.adapter.connectCalls, 0);

  await value.dispatcher.handleCommand(
    command('duplicate-report', 'report_status', { detail_level: 'basic' }, {
      traceId: 'trace-conflicting-duplicate',
      sequence: 2
    })
  );
  const conflict = messagesOfType(value.messages, 'command_ack').at(-1);
  assert.equal(conflict?.payload.accepted, false);
  assert.equal(conflict?.payload.failure?.code, 'INVALID_COMMAND');
  assert.equal(messagesOfType(value.messages, 'terminal_result').length, 2);

  const active = setup();
  const joinWork = active.dispatcher.handleCommand(command('active-join', 'join', {}));
  await turn();
  await active.dispatcher.handleCommand(
    command('second-join', 'join', {}, { sequence: 2 })
  );
  const rejected = messagesOfType(active.messages, 'command_ack').at(-1);
  assert.equal(rejected?.payload.failure?.code, 'COMMAND_ALREADY_ACTIVE');
  await active.dispatcher.cancelCommand(cancel('active-join'));
  await joinWork;
});

test('leave, report_status, and stop are accepted, bounded, and idempotent', async () => {
  const disconnected = setup();
  await disconnected.dispatcher.handleCommand(command('leave-one', 'leave', {}));
  await disconnected.dispatcher.handleCommand(command('leave-two', 'leave', {}));
  assert.deepEqual(
    messagesOfType(disconnected.messages, 'terminal_result').map(
      (message) => message.payload.outcome
    ),
    ['completed', 'completed']
  );
  assert.equal(disconnected.adapter.stopCalls >= 2, true);

  const connected = setup();
  const joinWork = connected.dispatcher.handleCommand(command('join-for-status', 'join', {}));
  await turn();
  connected.adapter.spawn();
  await joinWork;
  connected.messages.length = 0;
  await connected.dispatcher.handleCommand(
    command('report-connected', 'report_status', { detail_level: 'standard' }, { sequence: 2 })
  );
  const snapshot = messagesOfType(connected.messages, 'state_snapshot')[0];
  assert.equal(snapshot?.payload.minecraft_connection_state, 'connected');
  assert.equal(snapshot?.payload.dimension, 'minecraft:overworld');
  assert.equal(snapshot?.payload.active_command_name, 'report_status');
  await connected.dispatcher.handleCommand(
    command('stop-connected', 'stop', {}, { sequence: 3 })
  );
  assert.equal(connected.dispatcher.status().companionState, 'IDLE');
  assert.equal(connected.adapter.stopCalls >= 1, true);
});

test('emergency stop latches first and clears only after leave plus fresh successful join', async () => {
  const value = setup();
  const emergencyWork = value.dispatcher.emergencyStop(emergency('latch-one'));
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.equal(value.adapter.stopCalls, 1);
  await emergencyWork;

  await value.dispatcher.handleCommand(command('join-blocked', 'join', {}, { sequence: 2 }));
  assert.equal(
    messagesOfType(value.messages, 'command_ack').at(-1)?.payload.failure?.code,
    'EMERGENCY_STOP_ACTIVE'
  );
  await value.dispatcher.handleCommand(command('leave-recovery', 'leave', {}, { sequence: 3 }));
  assert.equal(value.dispatcher.status().emergencyStopActive, true);

  value.adapter.connectMode = 'fail';
  await value.dispatcher.handleCommand(command('join-recovery-fails', 'join', {}, { sequence: 4 }));
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  await value.dispatcher.handleCommand(command('leave-recovery-two', 'leave', {}, { sequence: 5 }));
  value.adapter.connectMode = 'pending';
  const joined = value.dispatcher.handleCommand(
    command('join-recovery-success', 'join', {}, { sequence: 6 })
  );
  await turn();
  value.adapter.spawn();
  await joined;
  assert.equal(value.dispatcher.status().emergencyStopActive, false);
  assert.equal(value.dispatcher.status().companionState, 'IDLE');
});

test('cancelled leave cannot qualify an emergency recovery join', async () => {
  const value = setup();
  const initialJoin = value.dispatcher.handleCommand(
    command('join-before-cancelled-leave', 'join', {})
  );
  await turn();
  value.adapter.spawn();
  await initialJoin;
  await value.dispatcher.emergencyStop(emergency('cancelled-leave-latch'));

  const barrier = value.adapter.blockNextDisconnect();
  const leave = value.dispatcher.handleCommand(
    command('cancelled-recovery-leave', 'leave', {}, { sequence: 2 })
  );
  await barrier.entered;
  await value.dispatcher.cancelCommand(cancel('cancelled-recovery-leave'));
  barrier.release();
  await leave;

  const leaveResult = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'cancelled-recovery-leave'
  );
  assert.equal(leaveResult.length, 1);
  assert.equal(leaveResult[0]?.payload.outcome, 'cancelled');
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.equal(value.dispatcher.status().emergencyStopActive, true);

  await value.dispatcher.handleCommand(
    command('join-after-cancelled-leave', 'join', {}, { sequence: 3 })
  );
  const recoveryAck = messagesOfType(value.messages, 'command_ack').find(
    (message) => message.command_id === 'join-after-cancelled-leave'
  );
  assert.equal(recoveryAck?.payload.accepted, false);
  assert.equal(recoveryAck?.payload.failure?.code, 'EMERGENCY_STOP_ACTIVE');
});

test('cancelled recovery join stays latched and reflects its physical disconnect', async () => {
  let blockRecoveryStatus = false;
  const statusEntered = deferred<void>();
  const releaseStatus = deferred<void>();
  const value = setup(new FakeMinecraftAdapter(), {
    afterSend: async (message) => {
      if (
        blockRecoveryStatus &&
        message.type === 'sidecar_status' &&
        message.payload.companion_state === 'IDLE'
      ) {
        blockRecoveryStatus = false;
        statusEntered.resolve(undefined);
        await releaseStatus.promise;
      }
    }
  });
  const initialJoin = value.dispatcher.handleCommand(
    command('join-before-recovery-cancel', 'join', {})
  );
  await turn();
  value.adapter.spawn();
  await initialJoin;
  await value.dispatcher.emergencyStop(emergency('recovery-cancel-latch'));
  await value.dispatcher.handleCommand(
    command('completed-recovery-leave', 'leave', {}, { sequence: 2 })
  );

  blockRecoveryStatus = true;
  const recoveryJoin = value.dispatcher.handleCommand(
    command('cancelled-recovery-join', 'join', {}, { sequence: 3 })
  );
  await turn();
  value.adapter.spawn();
  await statusEntered.promise;
  await value.dispatcher.cancelCommand(cancel('cancelled-recovery-join'));
  releaseStatus.resolve(undefined);
  await recoveryJoin;

  const results = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'cancelled-recovery-join'
  );
  assert.equal(results.length, 1);
  assert.equal(results[0]?.payload.outcome, 'cancelled');
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.equal(value.dispatcher.status().minecraft.connectionState, 'disconnected');
});

test('late cancellation cannot undo a committed recovery-join terminal', async () => {
  let blockTerminal = false;
  const terminalEntered = deferred<void>();
  const releaseTerminal = deferred<void>();
  const value = setup(new FakeMinecraftAdapter(), {
    afterSend: async (message) => {
      if (
        blockTerminal &&
        message.type === 'terminal_result' &&
        message.command_id === 'committed-recovery-join'
      ) {
        blockTerminal = false;
        terminalEntered.resolve(undefined);
        await releaseTerminal.promise;
      }
    }
  });
  const initialJoin = value.dispatcher.handleCommand(
    command('join-before-committed-recovery', 'join', {})
  );
  await turn();
  value.adapter.spawn();
  await initialJoin;
  await value.dispatcher.emergencyStop(emergency('committed-recovery-latch'));
  await value.dispatcher.handleCommand(
    command('leave-before-committed-recovery', 'leave', {}, { sequence: 2 })
  );

  blockTerminal = true;
  const recoveryJoin = value.dispatcher.handleCommand(
    command('committed-recovery-join', 'join', {}, { sequence: 3 })
  );
  await turn();
  value.adapter.spawn();
  await terminalEntered.promise;
  const disconnectsBeforeCancel = value.adapter.disconnectCalls;
  await value.dispatcher.cancelCommand(cancel('committed-recovery-join'));
  assert.equal(value.adapter.disconnectCalls, disconnectsBeforeCancel);
  releaseTerminal.resolve(undefined);
  await recoveryJoin;

  const results = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'committed-recovery-join'
  );
  assert.equal(results.length, 2);
  assert.equal(results.every((message) => message.payload.outcome === 'completed'), true);
  assert.equal(value.dispatcher.status().emergencyStopActive, false);
  assert.equal(value.dispatcher.status().companionState, 'IDLE');
  assert.equal(value.dispatcher.status().minecraft.connectionState, 'connected');
});

test('cancelled leave completion cannot overwrite a newer join', async () => {
  const value = setup();
  const initialJoin = value.dispatcher.handleCommand(
    command('join-before-old-leave', 'join', {})
  );
  await turn();
  value.adapter.spawn();
  await initialJoin;
  value.messages.length = 0;

  const barrier = value.adapter.blockNextDisconnect(true);
  const oldLeave = value.dispatcher.handleCommand(
    command('old-cancelled-leave', 'leave', {}, { sequence: 2 })
  );
  await barrier.entered;
  await value.dispatcher.cancelCommand(cancel('old-cancelled-leave'));
  const replacementJoin = value.dispatcher.handleCommand(
    command('replacement-join', 'join', {}, { sequence: 3 })
  );
  await turn();
  barrier.release();
  await oldLeave;
  value.adapter.spawn();
  await replacementJoin;

  assert.equal(value.dispatcher.status().companionState, 'IDLE');
  assert.equal(value.dispatcher.status().minecraft.connectionState, 'connected');
  assert.equal(
    messagesOfType(value.messages, 'state_snapshot').filter(
      (message) => message.command_id === 'old-cancelled-leave'
    ).length,
    0
  );
  assert.equal(
    messagesOfType(value.messages, 'terminal_result').filter(
      (message) => message.command_id === 'replacement-join'
    )[0]?.payload.outcome,
    'completed'
  );
});

test('queued disconnect from an older lifecycle cannot overwrite a replacement join', async () => {
  let blockEventStatus = false;
  const eventStatusEntered = deferred<void>();
  const releaseEventStatus = deferred<void>();
  const value = setup(new FakeMinecraftAdapter(), {
    afterSend: async (message) => {
      if (blockEventStatus && message.type === 'sidecar_status') {
        blockEventStatus = false;
        eventStatusEntered.resolve(undefined);
        await releaseEventStatus.promise;
      }
    }
  });
  const initialJoin = value.dispatcher.handleCommand(
    command('join-before-stale-event', 'join', {})
  );
  await turn();
  value.adapter.spawn();
  await initialJoin;

  blockEventStatus = true;
  value.adapter.emit({ type: 'health' });
  await eventStatusEntered.promise;
  value.adapter.stateValue = disconnectedMinecraftAdapterState('requested');
  value.adapter.emit({ type: 'disconnected', category: 'requested' });

  const replacementJoin = value.dispatcher.handleCommand(
    command('join-after-stale-event', 'join', {}, { sequence: 2 })
  );
  await turn();
  value.adapter.spawn();
  await replacementJoin;
  releaseEventStatus.resolve(undefined);
  await value.dispatcher.waitForEvents();

  assert.equal(value.dispatcher.status().companionState, 'IDLE');
  assert.equal(value.dispatcher.status().minecraft.connectionState, 'connected');
});

test('concurrent identical emergency commands replay one cached logical result', async () => {
  const value = setup();
  const report = value.dispatcher.handleCommand(
    command('report-before-emergency-duplicate', 'report_status', {
      detail_level: 'basic'
    })
  );
  const first = command('duplicate-emergency', 'emergency_stop', {}, {
    sequence: 2
  });
  const duplicate = command('duplicate-emergency', 'emergency_stop', {}, {
    sequence: 3
  });
  await Promise.all([
    report,
    value.dispatcher.handleCommand(first),
    value.dispatcher.handleCommand(duplicate)
  ]);

  const acknowledgments = messagesOfType(value.messages, 'command_ack').filter(
    (message) => message.command_id === 'duplicate-emergency'
  );
  const results = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'duplicate-emergency'
  );
  assert.equal(acknowledgments.length, 2);
  assert.equal(acknowledgments.every((message) => message.payload.accepted), true);
  assert.deepEqual(acknowledgments[1], acknowledgments[0]);
  assert.equal(results.length, 2);
  assert.deepEqual(results[1], results[0]);
  assert.equal(results[0]?.payload.outcome, 'completed');
  assert.equal(
    messagesOfType(value.messages, 'terminal_result').filter(
      (message) => message.command_id === 'report-before-emergency-duplicate'
    )[0]?.payload.outcome,
    'cancelled'
  );
});

test('concurrent distinct emergency commands both complete after one interrupted terminal', async () => {
  const interruptedEntered = deferred<void>();
  const releaseInterrupted = deferred<void>();
  let blockInterrupted = true;
  const value = setup(new FakeMinecraftAdapter(), {
    afterSend: async (message) => {
      if (
        blockInterrupted &&
        message.type === 'terminal_result' &&
        message.command_id === 'report-before-distinct-emergencies'
      ) {
        blockInterrupted = false;
        interruptedEntered.resolve(undefined);
        await releaseInterrupted.promise;
      }
    }
  });
  const report = value.dispatcher.handleCommand(
    command('report-before-distinct-emergencies', 'report_status', {
      detail_level: 'basic'
    })
  );
  const first = value.dispatcher.handleCommand(
    command('first-distinct-emergency', 'emergency_stop', {}, { sequence: 2 })
  );
  const second = value.dispatcher.handleCommand(
    command('second-distinct-emergency', 'emergency_stop', {}, { sequence: 3 })
  );
  await interruptedEntered.promise;
  releaseInterrupted.resolve(undefined);
  await Promise.all([report, first, second]);

  const interrupted = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'report-before-distinct-emergencies'
  );
  assert.equal(interrupted.length, 1);
  assert.equal(interrupted[0]?.payload.outcome, 'cancelled');
  for (const commandId of [
    'first-distinct-emergency',
    'second-distinct-emergency'
  ]) {
    const acknowledgments = messagesOfType(value.messages, 'command_ack').filter(
      (message) => message.command_id === commandId
    );
    const results = messagesOfType(value.messages, 'terminal_result').filter(
      (message) => message.command_id === commandId
    );
    assert.equal(acknowledgments.length, 1);
    assert.equal(acknowledgments[0]?.payload.accepted, true);
    assert.equal(results.length, 1);
    assert.equal(results[0]?.payload.outcome, 'completed');
  }
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
});

test('emergency envelope and command share cleanup without rejecting the command', async () => {
  const interruptedEntered = deferred<void>();
  const releaseInterrupted = deferred<void>();
  let blockInterrupted = true;
  const value = setup(new FakeMinecraftAdapter(), {
    afterSend: async (message) => {
      if (
        blockInterrupted &&
        message.type === 'terminal_result' &&
        message.command_id === 'report-before-envelope-command'
      ) {
        blockInterrupted = false;
        interruptedEntered.resolve(undefined);
        await releaseInterrupted.promise;
      }
    }
  });
  const report = value.dispatcher.handleCommand(
    command('report-before-envelope-command', 'report_status', {
      detail_level: 'basic'
    })
  );
  const envelope = value.dispatcher.emergencyStop(
    emergency('concurrent-emergency-envelope')
  );
  const ordinary = value.dispatcher.handleCommand(
    command('emergency-after-envelope', 'emergency_stop', {}, { sequence: 2 })
  );
  await interruptedEntered.promise;
  releaseInterrupted.resolve(undefined);
  await Promise.all([report, envelope, ordinary]);

  assert.equal(
    messagesOfType(value.messages, 'terminal_result').filter(
      (message) => message.command_id === 'report-before-envelope-command'
    ).length,
    1
  );
  const acknowledgments = messagesOfType(value.messages, 'command_ack').filter(
    (message) => message.command_id === 'emergency-after-envelope'
  );
  const results = messagesOfType(value.messages, 'terminal_result').filter(
    (message) => message.command_id === 'emergency-after-envelope'
  );
  assert.equal(acknowledgments.length, 1);
  assert.equal(acknowledgments[0]?.payload.accepted, true);
  assert.equal(results.length, 1);
  assert.equal(results[0]?.payload.outcome, 'completed');
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
});

test('emergency command clears before ack; movement and expired commands never execute', async () => {
  const adapter = new FakeMinecraftAdapter();
  const messages: SidecarOutboundMessage[] = [];
  const config = runtimeConfig();
  const dispatcher = new MinecraftCommandDispatcher({
    config,
    adapter,
    now: () => new Date(NOW),
    send: async (message) => {
      if (message.type === 'command_ack' && message.payload.command_name === 'emergency_stop') {
        assert.equal(adapter.stopCalls >= 1, true);
      }
      messages.push(message);
    }
  });
  dispatcher.beginSession(1_000, 600);
  const interrupted = dispatcher.handleCommand(
    command('join-before-emergency-command', 'join', {}, { sequence: 1 })
  );
  await turn();
  await dispatcher.handleCommand(
    command('emergency-command', 'emergency_stop', {}, { sequence: 2 })
  );
  await interrupted;
  const emergencyResults = messagesOfType(messages, 'terminal_result');
  assert.equal(emergencyResults.length, 2);
  assert.equal(emergencyResults[0]?.payload.command_name, 'join');
  assert.equal(emergencyResults[0]?.payload.outcome, 'cancelled');
  assert.equal(emergencyResults[1]?.payload.command_name, 'emergency_stop');
  assert.equal(emergencyResults[1]?.payload.outcome, 'completed');
  assert.equal(dispatcher.status().emergencyStopActive, true);

  for (const [index, name, args] of [
    ['1', 'follow_owner', { follow_distance: 3, lease_duration_seconds: 30 }],
    ['2', 'wait_here', {}],
    ['3', 'come_here', { arrival_distance: 3 }],
    ['4', 'look_at_owner', { duration_seconds: 2 }]
  ] as const) {
    await dispatcher.handleCommand(command(`movement-${index}`, name, args));
    assert.equal(
      messagesOfType(messages, 'command_ack').at(-1)?.payload.failure?.code,
      'EMERGENCY_STOP_ACTIVE'
    );
  }
  await dispatcher.handleCommand(
    command('expired-report', 'report_status', { detail_level: 'basic' }, {
      deadline: '2026-07-10T17:59:59.000Z',
      sentAt: '2026-07-10T17:59:00.000Z'
    })
  );
  assert.equal(
    messagesOfType(messages, 'command_ack').at(-1)?.payload.failure?.code,
    'DEADLINE_EXCEEDED'
  );
  assert.equal(adapter.connectCalls, 1);
});

test('death, respawn, kick, and error events publish only bounded safe categories', async () => {
  const value = setup();
  const joinWork = value.dispatcher.handleCommand(command('join-events', 'join', {}));
  await turn();
  value.adapter.spawn();
  await joinWork;
  value.messages.length = 0;

  value.adapter.stateValue = Object.freeze({
    ...value.adapter.stateValue,
    alive: false,
    health: 0
  });
  value.adapter.emit({ type: 'death' });
  await value.dispatcher.waitForEvents();
  assert.equal(value.dispatcher.status().companionState, 'DEAD');
  assert.equal(value.adapter.stopCalls >= 1, true);

  value.adapter.stateValue = Object.freeze({
    ...value.adapter.stateValue,
    alive: true,
    health: 20,
    dimension: 'minecraft:overworld'
  });
  value.adapter.emit({ type: 'respawn' });
  await value.dispatcher.waitForEvents();
  assert.equal(value.dispatcher.status().companionState, 'IDLE');

  value.adapter.emit({ type: 'error' });
  value.adapter.emit({ type: 'kicked' });
  value.adapter.stateValue = disconnectedMinecraftAdapterState('kicked');
  value.adapter.emit({ type: 'disconnected', category: 'kicked' });
  await value.dispatcher.waitForEvents();
  assert.equal(value.dispatcher.status().companionState, 'DISCONNECTED');
  assert.equal(JSON.stringify(value.messages).includes(RAW_SENTINEL), false);
  assert.equal(value.deliveryFailures(), 0);
});
