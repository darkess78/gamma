import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test, { type TestContext } from 'node:test';

import { loadMinecraftSidecarRuntimeConfig } from '../src/config.js';
import type {
  GammaControlMessage,
  MinecraftControlClientOptions,
  MinecraftControlDisconnect,
  MinecraftControlClientStatus,
  SidecarOutboundMessage
} from '../src/control-client.js';
import type {
  CommandMessage,
  FailureCode,
  WelcomeMessage
} from '../src/protocol.js';
import { parseProtocolMessage } from '../src/protocol.js';
import {
  MineflayerMinecraftAdapter,
  type MineflayerRuntimeDependencies
} from '../src/mineflayer-runtime.js';
import {
  MinecraftSidecarRuntime,
  type MinecraftControlClientLike,
  type MinecraftRuntimeTimer
} from '../src/runtime.js';

const CONTROL_TOKEN = 'runtime-safety-trap-token-8Lq4';
const OWNER = 'Neety';
const START_MS = Date.parse('2026-07-11T18:00:00.000Z');
const FULL_CUBE = Object.freeze([
  Object.freeze([0, 0, 0, 1, 1, 1] as const)
]);
const AIR = Object.freeze({
  name: 'air',
  boundingBox: 'empty',
  shapes: Object.freeze([])
});
const STONE = Object.freeze({
  name: 'stone',
  boundingBox: 'block',
  shapes: FULL_CUBE
});

type TerminalDraft = Extract<
  SidecarOutboundMessage,
  { type: 'terminal_result' }
>;

type MutablePosition = { x: number; y: number; z: number };

type TrappedVector = Readonly<{
  view: Readonly<MutablePosition>;
  replace: (position: Readonly<MutablePosition>) => void;
}>;

function trappedVector(
  initial: Readonly<MutablePosition>,
  onMutation: () => void
): TrappedVector {
  const backing: MutablePosition = { ...initial };
  const view = new Proxy(backing, {
    set: () => {
      onMutation();
      throw new TypeError('raw vector mutation is forbidden');
    },
    defineProperty: () => {
      onMutation();
      throw new TypeError('raw vector mutation is forbidden');
    },
    deleteProperty: () => {
      onMutation();
      throw new TypeError('raw vector mutation is forbidden');
    }
  });
  return Object.freeze({
    view,
    replace: (position: Readonly<MutablePosition>) => {
      backing.x = position.x;
      backing.y = position.y;
      backing.z = position.z;
    }
  });
}

type TrappedEntity = Readonly<{
  view: Readonly<{
    position: Readonly<MutablePosition>;
    velocity: Readonly<MutablePosition>;
  }>;
  moveTo: (position: Readonly<MutablePosition>) => void;
}>;

function trappedEntity(onMutation: () => void): TrappedEntity {
  const position = trappedVector({ x: 0, y: 64, z: 0 }, onMutation);
  const velocity = trappedVector({ x: 0, y: 0, z: 0 }, onMutation);
  const target = {
    position: position.view,
    velocity: velocity.view
  };
  const view = new Proxy(target, {
    set: () => {
      onMutation();
      throw new TypeError('raw entity mutation is forbidden');
    },
    defineProperty: () => {
      onMutation();
      throw new TypeError('raw entity mutation is forbidden');
    },
    deleteProperty: () => {
      onMutation();
      throw new TypeError('raw entity mutation is forbidden');
    }
  });
  return Object.freeze({
    view,
    moveTo: position.replace
  });
}

class SafetyTrapBot extends EventEmitter {
  readonly version = '1.21.11';
  readonly game = { dimension: 'overworld' as unknown };
  readonly forbiddenCalls = new Map<string, number>();
  readonly lookTargets: unknown[] = [];
  readonly controlCalls: Array<Readonly<{ control: string; active: boolean }>> = [];
  readonly #botEntity = trappedEntity(() => {
    this.rawMutationAttempts += 1;
  });
  readonly #ownerEntity = trappedEntity(() => {
    this.rawMutationAttempts += 1;
  });
  readonly _client = {
    socket: {
      destroy: () => {
        this.transportDestroyCalls += 1;
      }
    }
  };

  health: unknown = 20;
  food: unknown = 20;
  targetDigBlock: unknown = null;
  usingHeldItem = false;
  players: Readonly<Record<string, unknown>> = {};
  rawMutationAttempts = 0;
  clearCalls = 0;
  forwardActivations = 0;
  forwardEnabled = false;
  quitCalls = 0;
  endCalls = 0;
  transportDestroyCalls = 0;
  stopDiggingCalls = 0;
  deactivateItemCalls = 0;
  boundaryReadAttempts = 0;

  constructor() {
    super();
    this.on('error', () => undefined);
    this.showOwner({ x: 8, y: 64, z: 0 });
  }

  get entity(): TrappedEntity['view'] {
    return this.#botEntity.view;
  }

  set entity(_value: TrappedEntity['view']) {
    this.rawMutationAttempts += 1;
    throw new TypeError('raw bot entity replacement is forbidden');
  }

  get registry(): never {
    this.boundaryReadAttempts += 1;
    throw new TypeError('raw registry access is forbidden');
  }

  get world(): never {
    this.boundaryReadAttempts += 1;
    throw new TypeError('raw world access is forbidden');
  }

  get pathfinder(): never {
    this.boundaryReadAttempts += 1;
    throw new TypeError('Mineflayer plugin access is forbidden');
  }

  moveBot(position: Readonly<MutablePosition>): void {
    this.#botEntity.moveTo(position);
  }

  showOwner(position: Readonly<MutablePosition>): void {
    this.#ownerEntity.moveTo(position);
    this.players = Object.freeze({
      owner: Object.freeze({ username: OWNER, entity: this.#ownerEntity.view })
    });
  }

  hideOwner(): void {
    this.players = Object.freeze({});
  }

  clearControlStates(): void {
    this.clearCalls += 1;
    this.forwardEnabled = false;
  }

  setControlState(control: string, active: boolean): void {
    this.controlCalls.push(Object.freeze({ control, active }));
    if (control !== 'forward') this.#forbidden(`setControlState:${control}`);
    if (active) this.forwardActivations += 1;
    this.forwardEnabled = active;
  }

  async lookAt(position: unknown, force?: boolean): Promise<void> {
    assert.equal(force, false);
    assert.notEqual(position, this.#botEntity.view.position);
    assert.notEqual(position, this.#ownerEntity.view.position);
    this.lookTargets.push(position);
  }

  blockAt(position: unknown): typeof AIR | typeof STONE | null {
    if (typeof position !== 'object' || position === null) return null;
    const candidate = position as Readonly<{ y?: unknown }>;
    if (typeof candidate.y !== 'number') return null;
    return Math.floor(candidate.y) === 63 ? STONE : AIR;
  }

  stopDigging(): void {
    this.stopDiggingCalls += 1;
  }

  deactivateItem(): void {
    this.deactivateItemCalls += 1;
  }

  quit(): void {
    this.quitCalls += 1;
    this.emit('end', 'bounded-test-end');
  }

  end(): void {
    this.endCalls += 1;
    this.emit('end', 'bounded-test-end');
  }

  activateBlock(): never { return this.#forbidden('activateBlock'); }
  activateEntity(): never { return this.#forbidden('activateEntity'); }
  activateItem(): never { return this.#forbidden('activateItem'); }
  attack(): never { return this.#forbidden('attack'); }
  chat(): never { return this.#forbidden('chat'); }
  consume(): never { return this.#forbidden('consume'); }
  dig(): never { return this.#forbidden('dig'); }
  equip(): never { return this.#forbidden('equip'); }
  loadPlugin(): never { return this.#forbidden('loadPlugin'); }
  openChest(): never { return this.#forbidden('openChest'); }
  openContainer(): never { return this.#forbidden('openContainer'); }
  openFurnace(): never { return this.#forbidden('openFurnace'); }
  placeBlock(): never { return this.#forbidden('placeBlock'); }
  sleep(): never { return this.#forbidden('sleep'); }
  toss(): never { return this.#forbidden('toss'); }
  tossStack(): never { return this.#forbidden('tossStack'); }

  #forbidden(name: string): never {
    this.forbiddenCalls.set(name, (this.forbiddenCalls.get(name) ?? 0) + 1);
    throw new TypeError(`forbidden Mineflayer method: ${name}`);
  }
}

class DeterministicClock {
  wallMs = START_MS;
  monotonicMs = 1_000;

  readonly now = (): Date => new Date(this.wallMs);
  readonly monotonicNowMs = (): number => this.monotonicMs;

  advance(milliseconds: number): void {
    this.wallMs += milliseconds;
    this.monotonicMs += milliseconds;
  }

  deadline(milliseconds = 60_000): string {
    return new Date(this.wallMs + milliseconds).toISOString();
  }
}

class FakeIntervals {
  callback: (() => void) | undefined;
  readonly handle: MinecraftRuntimeTimer = Object.freeze({
    unref: () => undefined
  });

  readonly set = (callback: () => void): MinecraftRuntimeTimer => {
    assert.equal(this.callback, undefined);
    this.callback = callback;
    return this.handle;
  };

  readonly clear = (timer: MinecraftRuntimeTimer): void => {
    assert.equal(timer, this.handle);
    this.callback = undefined;
  };
}

type Deferred<T> = Readonly<{
  promise: Promise<T>;
  resolve: (value: T) => void;
}>;

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return Object.freeze({ promise, resolve });
}

class FakeControlClient implements MinecraftControlClientLike {
  readonly sent: SidecarOutboundMessage[] = [];
  readonly #disconnected = deferred<MinecraftControlDisconnect>();
  handler: ((message: GammaControlMessage) => void | Promise<void>) | undefined;
  closeCalls = 0;

  async connect(): Promise<WelcomeMessage> {
    const parsed = parseProtocolMessage({
      protocol: 'gamma.minecraft',
      version: 1,
      type: 'welcome',
      message_id: 'runtime-safety-welcome',
      connection_id: 'runtime-safety-connection',
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
    });
    assert.equal(parsed.type, 'welcome');
    return parsed;
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
  }

  waitForDisconnect(): Promise<Readonly<MinecraftControlDisconnect>> {
    return this.#disconnected.promise;
  }

  async sendHeartbeat(): Promise<void> {}

  async sendSidecarMessage(message: SidecarOutboundMessage): Promise<void> {
    this.sent.push(message);
  }

  setMessageHandler(
    handler: ((message: GammaControlMessage) => void | Promise<void>) | undefined
  ): void {
    this.handler = handler;
  }

  status(): Readonly<MinecraftControlClientStatus> {
    return Object.freeze({
      state: 'ready',
      connectionId: 'runtime-safety-connection',
      lastReceivedSequence: 0
    });
  }

  async deliver(message: GammaControlMessage): Promise<void> {
    assert.notEqual(this.handler, undefined);
    await this.handler?.(message);
  }

  loseControl(): void {
    this.#disconnected.resolve(Object.freeze({ kind: 'abrupt', code: null }));
  }
}

type RuntimeHarness = Readonly<{
  runtime: MinecraftSidecarRuntime;
  run: ReturnType<MinecraftSidecarRuntime['run']>;
  client: FakeControlClient;
  clock: DeterministicClock;
  adapter: MineflayerMinecraftAdapter;
  bots: SafetyTrapBot[];
  command: (
    name: CommandMessage['payload']['name'],
    args: Readonly<Record<string, unknown>>,
    deadlineMs?: number
  ) => CommandMessage;
  cancel: (commandId: string) => GammaControlMessage;
  emergency: (commandId: string) => GammaControlMessage;
  shutdown: () => GammaControlMessage;
}>;

async function runtimeHarness(): Promise<RuntimeHarness> {
  const clock = new DeterministicClock();
  const client = new FakeControlClient();
  const intervals = new FakeIntervals();
  const bots: SafetyTrapBot[] = [];
  const adapterDependencies: MineflayerRuntimeDependencies = {
    createBot: () => {
      const bot = new SafetyTrapBot();
      bots.push(bot);
      return bot;
    },
    createVector: (x, y, z) => Object.freeze({ x, y, z })
  };
  const adapter = new MineflayerMinecraftAdapter(adapterDependencies);
  let controlOptions: MinecraftControlClientOptions | undefined;
  const runtime = new MinecraftSidecarRuntime(
    loadMinecraftSidecarRuntimeConfig({
      SHANA_MINECRAFT_CONTROL_TOKEN: CONTROL_TOKEN,
      SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'runtime-safety-trap',
      SHANA_MINECRAFT_OWNER_USERNAME: OWNER
    }),
    {
      createControlClient: (options) => {
        controlOptions = options;
        return client;
      },
      createMinecraftAdapter: () => adapter,
      now: clock.now,
      monotonicNowMs: clock.monotonicNowMs,
      setInterval: intervals.set,
      clearInterval: intervals.clear
    }
  );
  assert.equal(controlOptions?.hello.pathfinderVersion, 'not-installed');
  const run = runtime.run();
  await waitFor(() => client.sent.length >= 3, 'runtime startup');
  let sequence = 1;
  let commandNumber = 1;
  return Object.freeze({
    runtime,
    run,
    client,
    clock,
    adapter,
    bots,
    command: (name, args, deadlineMs = 60_000) => {
      const commandId = `trap-${name}-${commandNumber++}`;
      const parsed = parseProtocolMessage({
        protocol: 'gamma.minecraft',
        version: 1,
        type: 'command',
        message_id: `message-${commandId}`,
        connection_id: 'runtime-safety-connection',
        sent_at: clock.now().toISOString(),
        sequence: sequence++,
        trace_id: `trace-${commandId}`,
        command_id: commandId,
        payload: {
          name,
          deadline_at: clock.deadline(deadlineMs),
          arguments: args
        }
      });
      assert.equal(parsed.type, 'command');
      return parsed;
    },
    cancel: (commandId) => {
      const parsed = parseProtocolMessage({
        protocol: 'gamma.minecraft',
        version: 1,
        type: 'cancel_command',
        message_id: `cancel-${commandId}`,
        connection_id: 'runtime-safety-connection',
        sent_at: clock.now().toISOString(),
        sequence: sequence++,
        trace_id: `trace-${commandId}`,
        command_id: commandId,
        payload: { reason: 'Bounded test cancellation.' }
      });
      assert.equal(parsed.type, 'cancel_command');
      return parsed;
    },
    emergency: (commandId) => {
      const parsed = parseProtocolMessage({
        protocol: 'gamma.minecraft',
        version: 1,
        type: 'emergency_stop',
        message_id: `emergency-${commandId}`,
        connection_id: 'runtime-safety-connection',
        sent_at: clock.now().toISOString(),
        sequence: sequence++,
        trace_id: `trace-${commandId}`,
        command_id: commandId,
        payload: { reason: 'Bounded test emergency.' }
      });
      assert.equal(parsed.type, 'emergency_stop');
      return parsed;
    },
    shutdown: () => {
      const parsed = parseProtocolMessage({
        protocol: 'gamma.minecraft',
        version: 1,
        type: 'shutdown',
        message_id: 'runtime-safety-shutdown',
        connection_id: 'runtime-safety-connection',
        sent_at: clock.now().toISOString(),
        sequence: sequence++,
        trace_id: 'trace-runtime-safety-shutdown',
        payload: { reason: 'Bounded test shutdown.', leave_minecraft: true }
      });
      assert.equal(parsed.type, 'shutdown');
      return parsed;
    }
  });
}

async function join(harness: RuntimeHarness): Promise<SafetyTrapBot> {
  const message = harness.command('join', {});
  await harness.client.deliver(message);
  await waitFor(() => harness.bots.length > 0, 'Mineflayer bot creation');
  const bot = harness.bots.at(-1);
  assert.notEqual(bot, undefined);
  bot?.emit('spawn');
  await terminal(harness.client, message.command_id);
  assert.equal(
    terminalFor(harness.client, message.command_id)?.payload.outcome,
    'completed'
  );
  assert.notEqual(bot, undefined);
  return bot as SafetyTrapBot;
}

async function startFollow(
  harness: RuntimeHarness,
  deadlineMs = 60_000
): Promise<CommandMessage> {
  const message = harness.command(
    'follow_owner',
    { follow_distance: 3, lease_duration_seconds: 30 },
    deadlineMs
  );
  await harness.client.deliver(message);
  await acceptedAck(harness.client, message.command_id);
  return message;
}

async function acceptedAck(client: FakeControlClient, commandId: string): Promise<void> {
  await waitFor(
    () =>
      client.sent.some(
        (message) =>
          message.type === 'command_ack' &&
          message.command_id === commandId &&
          message.payload.accepted
      ),
    `accepted acknowledgment for ${commandId}`
  );
}

async function terminal(client: FakeControlClient, commandId: string): Promise<void> {
  await waitFor(
    () => terminalFor(client, commandId) !== undefined,
    `terminal result for ${commandId}`
  );
}

function terminalFor(
  client: FakeControlClient,
  commandId: string
): TerminalDraft | undefined {
  return client.sent.find(
    (message): message is TerminalDraft =>
      message.type === 'terminal_result' && message.command_id === commandId
  );
}

function terminalCount(client: FakeControlClient, commandId: string): number {
  return client.sent.filter(
    (message) =>
      message.type === 'terminal_result' && message.command_id === commandId
  ).length;
}

async function tick(bot: SafetyTrapBot): Promise<void> {
  bot.emit('physicsTick');
  await turn();
  await turn();
}

async function turn(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

async function waitFor(
  predicate: () => boolean,
  label: string,
  attempts = 100
): Promise<void> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (predicate()) return;
    await turn();
  }
  assert.fail(`Timed out waiting for ${label}`);
}

function assertTrapProof(bot: SafetyTrapBot): void {
  assert.equal(bot.rawMutationAttempts, 0);
  assert.equal(bot.boundaryReadAttempts, 0);
  assert.deepEqual([...bot.forbiddenCalls.entries()], []);
  assert.equal(
    bot.controlCalls.every((call) => call.control === 'forward'),
    true
  );
}

function assertMovementClean(bot: SafetyTrapBot): void {
  assert.equal(bot.forwardEnabled, false);
  assert.equal(bot.listenerCount('physicsTick'), 0);
  assertTrapProof(bot);
}

async function finishHarness(harness: RuntimeHarness): Promise<void> {
  await harness.runtime.shutdown('requested');
  await harness.run;
  for (const bot of harness.bots) assertMovementClean(bot);
}

test(
  'real runtime, dispatcher, executor, and Mineflayer adapter keep every command inside the safe boundary',
  async () => {
    const harness = await runtimeHarness();
    try {
      const bot = await join(harness);

      const botCopy = harness.adapter.getBotPosition();
      const ownerCopy = harness.adapter.getPlayer(OWNER);
      const rawOwnerPosition = (
        bot.players.owner as Readonly<{
          entity: Readonly<{ position: Readonly<MutablePosition> }>;
        }>
      ).entity.position;
      assert.notEqual(botCopy, bot.entity.position);
      assert.notEqual(ownerCopy?.position, rawOwnerPosition);
      assert.throws(() => {
        (botCopy as MutablePosition).x = 99;
      }, TypeError);
      assert.throws(() => {
        (ownerCopy?.position as MutablePosition).x = 99;
      }, TypeError);
      assert.equal(bot.rawMutationAttempts, 0);

      const follow = await startFollow(harness);
      await tick(bot);
      assert.equal(bot.forwardEnabled, true);
      assert.equal(bot.listenerCount('physicsTick'), 1);
      const wait = harness.command('wait_here', { reason: 'Wait safely.' });
      await harness.client.deliver(wait);
      await Promise.all([
        terminal(harness.client, follow.command_id),
        terminal(harness.client, wait.command_id)
      ]);
      assert.equal(terminalFor(harness.client, follow.command_id)?.payload.outcome, 'cancelled');
      assert.equal(terminalFor(harness.client, wait.command_id)?.payload.outcome, 'completed');
      assertMovementClean(bot);

      bot.moveBot({ x: 0, y: 64, z: 0 });
      bot.showOwner({ x: 8, y: 64, z: 0 });
      const come = harness.command('come_here', { arrival_distance: 3 });
      await harness.client.deliver(come);
      await acceptedAck(harness.client, come.command_id);
      await tick(bot);
      assert.equal(bot.forwardEnabled, true);
      bot.moveBot({ x: 6, y: 64, z: 0 });
      await tick(bot);
      await terminal(harness.client, come.command_id);
      assert.equal(terminalFor(harness.client, come.command_id)?.payload.outcome, 'completed');
      assertMovementClean(bot);

      bot.showOwner({ x: 8, y: 64, z: 0 });
      const look = harness.command('look_at_owner', { duration_seconds: 0.1 });
      const forwardBeforeLook = bot.forwardActivations;
      await harness.client.deliver(look);
      await terminal(harness.client, look.command_id);
      assert.equal(terminalFor(harness.client, look.command_id)?.payload.outcome, 'completed');
      assert.equal(bot.forwardActivations, forwardBeforeLook);
      assertMovementClean(bot);

      const report = harness.command('report_status', { detail_level: 'standard' });
      await harness.client.deliver(report);
      await terminal(harness.client, report.command_id);
      assert.equal(terminalFor(harness.client, report.command_id)?.payload.outcome, 'completed');
      assertMovementClean(bot);

      bot.moveBot({ x: 0, y: 64, z: 0 });
      const stoppedFollow = await startFollow(harness);
      await tick(bot);
      const stop = harness.command('stop', { reason: 'Stop safely.' });
      await harness.client.deliver(stop);
      await Promise.all([
        terminal(harness.client, stoppedFollow.command_id),
        terminal(harness.client, stop.command_id)
      ]);
      assert.equal(
        terminalFor(harness.client, stoppedFollow.command_id)?.payload.outcome,
        'cancelled'
      );
      assert.equal(terminalFor(harness.client, stop.command_id)?.payload.outcome, 'completed');
      assertMovementClean(bot);

      const cancelledFollow = await startFollow(harness);
      await tick(bot);
      await harness.client.deliver(harness.cancel(cancelledFollow.command_id));
      await terminal(harness.client, cancelledFollow.command_id);
      assert.equal(
        terminalFor(harness.client, cancelledFollow.command_id)?.payload.outcome,
        'cancelled'
      );
      assert.equal(terminalCount(harness.client, cancelledFollow.command_id), 1);
      assertMovementClean(bot);

      const ownerLossFollow = await startFollow(harness);
      bot.hideOwner();
      await tick(bot);
      assert.equal(bot.forwardEnabled, false);
      assert.equal(terminalFor(harness.client, ownerLossFollow.command_id), undefined);
      harness.clock.advance(9_999);
      bot.showOwner({ x: 8, y: 64, z: 0 });
      await tick(bot);
      assert.equal(bot.forwardEnabled, true);
      const ownerReturnWait = harness.command('wait_here', {});
      await harness.client.deliver(ownerReturnWait);
      await Promise.all([
        terminal(harness.client, ownerLossFollow.command_id),
        terminal(harness.client, ownerReturnWait.command_id)
      ]);
      assertMovementClean(bot);

      const deadlineFollow = await startFollow(harness, 1_000);
      harness.clock.advance(1_000);
      await tick(bot);
      await terminal(harness.client, deadlineFollow.command_id);
      assert.equal(
        terminalFor(harness.client, deadlineFollow.command_id)?.payload.outcome,
        'timed_out'
      );
      assertMovementClean(bot);

      bot.moveBot({ x: 0, y: 64, z: 0 });
      const stalledFollow = await startFollow(harness);
      await tick(bot);
      harness.clock.advance(5_000);
      await tick(bot);
      await terminal(harness.client, stalledFollow.command_id);
      assert.equal(
        terminalFor(harness.client, stalledFollow.command_id)?.payload.failure?.code,
        'PATH_STALLED' satisfies FailureCode
      );
      assertMovementClean(bot);

      const deathFollow = await startFollow(harness);
      await tick(bot);
      bot.health = 0;
      bot.emit('death');
      assertMovementClean(bot);
      await terminal(harness.client, deathFollow.command_id);
      assert.equal(
        terminalFor(harness.client, deathFollow.command_id)?.payload.failure?.code,
        'BOT_DEAD'
      );
      bot.emit('respawn');
      assertMovementClean(bot);
      bot.health = 20;
      bot.emit('spawn');
      await waitFor(
        () => harness.runtime.status().companionState === 'IDLE',
        'respawn recovery'
      );
      assert.equal(terminalCount(harness.client, deathFollow.command_id), 1);
      assertMovementClean(bot);

      const emergencyFollow = await startFollow(harness);
      await tick(bot);
      const emergencyCommand = harness.command('emergency_stop', {
        reason: 'Emergency trap test.'
      });
      await harness.client.deliver(emergencyCommand);
      await Promise.all([
        terminal(harness.client, emergencyFollow.command_id),
        terminal(harness.client, emergencyCommand.command_id)
      ]);
      assert.equal(harness.runtime.status().emergencyStopActive, true);
      assertMovementClean(bot);

      const rejectedMovement = harness.command('come_here', {
        arrival_distance: 3
      });
      await harness.client.deliver(rejectedMovement);
      await waitFor(
        () =>
          harness.client.sent.some(
            (message) =>
              message.type === 'command_ack' &&
              message.command_id === rejectedMovement.command_id &&
              message.payload.failure?.code === 'EMERGENCY_STOP_ACTIVE'
          ),
        'latched movement rejection'
      );
      assert.equal(terminalCount(harness.client, rejectedMovement.command_id), 0);

      const envelopeId = 'trap-emergency-envelope';
      await harness.client.deliver(harness.emergency(envelopeId));
      await waitFor(
        () => harness.runtime.status().emergencyStopActive,
        'emergency envelope latch'
      );
      assertMovementClean(bot);

      const leave = harness.command('leave', { reason: 'Recovery leave.' });
      await harness.client.deliver(leave);
      await terminal(harness.client, leave.command_id);
      assert.equal(terminalFor(harness.client, leave.command_id)?.payload.outcome, 'completed');
      assertMovementClean(bot);

      const recoveryBot = await join(harness);
      assert.notEqual(recoveryBot, bot);
      assert.equal(harness.runtime.status().emergencyStopActive, false);
      bot.emit('physicsTick');
      bot.emit('death');
      bot.emit('respawn');
      bot.emit('spawn');
      bot.emit('kicked', 'stale raw event');
      bot.emit('error', new Error('stale raw event'));
      await turn();
      assert.equal(harness.runtime.status().minecraftConnectionState, 'connected');
      assertMovementClean(bot);
      assertMovementClean(recoveryBot);

      const finalLeave = harness.command('leave', {});
      await harness.client.deliver(finalLeave);
      await terminal(harness.client, finalLeave.command_id);
      assertMovementClean(recoveryBot);

      const shutdownBot = await join(harness);
      const shutdownFollow = await startFollow(harness);
      await tick(shutdownBot);
      await harness.client.deliver(harness.shutdown());
      assert.equal((await harness.run).category, 'gamma_shutdown');
      assert.equal(terminalCount(harness.client, shutdownFollow.command_id), 0);
      for (const candidate of harness.bots) assertMovementClean(candidate);
    } finally {
      await finishHarness(harness);
    }
  }
);

test('kick, error, and transport disconnect each clear a real active movement once', async (context) => {
  for (const event of ['kicked', 'error', 'end'] as const) {
    await context.test(event, async () => {
      const harness = await runtimeHarness();
      try {
        const bot = await join(harness);
        const follow = await startFollow(harness);
        await tick(bot);
        assert.equal(bot.forwardEnabled, true);
        if (event === 'error') bot.emit(event, new Error('raw transport failure'));
        else bot.emit(event, 'raw transport failure');
        assertMovementClean(bot);
        await terminal(harness.client, follow.command_id);
        assert.equal(
          terminalFor(harness.client, follow.command_id)?.payload.failure?.code,
          'MINECRAFT_SERVER_DISCONNECTED'
        );
        assert.equal(terminalCount(harness.client, follow.command_id), 1);
        assert.equal(harness.runtime.status().minecraftConnectionState, 'disconnected');
        assertTrapProof(bot);
      } finally {
        await finishHarness(harness);
      }
    });
  }
});

test('control loss clears the real adapter and never permits a late tick to resume movement', async () => {
  const harness = await runtimeHarness();
  const bot = await join(harness);
  const follow = await startFollow(harness);
  await tick(bot);
  assert.equal(bot.forwardEnabled, true);
  harness.client.loseControl();
  assert.equal((await harness.run).category, 'control_disconnected');
  assert.equal(terminalCount(harness.client, follow.command_id), 0);
  assertMovementClean(bot);
  const activations = bot.forwardActivations;
  bot.emit('physicsTick');
  await turn();
  assert.equal(bot.forwardActivations, activations);
  await finishHarness(harness);
});
