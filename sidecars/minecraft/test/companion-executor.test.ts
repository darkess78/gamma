import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CompanionExecutor,
  type CompanionExecutionResult,
  type CompanionProgress
} from '../src/companion-executor.js';
import { MinecraftCommandDispatcher } from '../src/command-dispatcher.js';
import type { MinecraftDispatcherTimer } from '../src/command-dispatcher.js';
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
  parseProtocolMessage,
  type CommandMessage,
  type CommandName,
  type FailureCode
} from '../src/protocol.js';
import type { SidecarOutboundMessage } from '../src/control-client.js';

const BASE_WALL_MS = Date.parse('2026-07-10T18:00:00.000Z');

class FakeClock {
  wallMs = BASE_WALL_MS;
  monotonicMs = 1_000;

  readonly now = (): Date => new Date(this.wallMs);
  readonly monotonicNowMs = (): number => this.monotonicMs;

  advance(milliseconds: number): void {
    this.wallMs += milliseconds;
    this.monotonicMs += milliseconds;
  }
}

type Deferred<T> = Readonly<{
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
}>;

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return Object.freeze({ promise, resolve, reject });
}

class FakeMovementAdapter implements MinecraftMovementAdapter {
  stateValue: MinecraftAdapterState = connectedState();
  botPosition: SafePosition | undefined = position(0, 64, 0);
  ownerValue: ObservedPlayer | undefined = observedOwner(8);
  safetyValue: ForwardSafety = safeStep(1);
  throwOnInspect = false;
  forward = false;
  lookFailure: Error | undefined;
  lookGate: Deferred<void> | undefined;
  eventHandler: MinecraftAdapterEventHandler | undefined;

  clearCalls = 0;
  stopAllCalls = 0;
  forwardCalls: boolean[] = [];
  inspectCalls = 0;
  inspectedOwners: ObservedPlayer[] = [];
  lookTargets: SafePosition[] = [];
  tickRegistrations = 0;
  tickCleanups = 0;
  maximumListeners = 0;
  connectCalls = 0;
  disconnectCalls = 0;
  readonly log: string[] = [];

  readonly #tickHandlers = new Set<() => void>();

  async connect(
    _config: MinecraftAdapterConnectionConfig,
    signal: AbortSignal
  ): Promise<void> {
    this.connectCalls += 1;
    this.log.push('connect');
    if (signal.aborted) throw new Error('connect aborted');
    this.botPosition ??= position(0, 64, 0);
    this.stateValue = connectedState(this.botPosition);
  }

  async disconnect(): Promise<void> {
    this.disconnectCalls += 1;
    this.log.push('disconnect');
    this.clearControls();
    this.stateValue = disconnectedMinecraftAdapterState('requested');
    this.botPosition = undefined;
  }

  stopAllControls(): void {
    this.stopAllCalls += 1;
    this.log.push('stop-all');
    this.clearControls();
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
    this.eventHandler = handler;
  }

  getBotPosition(): SafePosition | undefined {
    return this.botPosition === undefined
      ? undefined
      : Object.freeze({ ...this.botPosition });
  }

  getDimension(): MinecraftDimension | undefined {
    return this.stateValue.dimension ?? undefined;
  }

  getPlayer(username: string): ObservedPlayer | undefined {
    const owner = this.ownerValue;
    if (owner === undefined || owner.username !== username) return undefined;
    return Object.freeze({
      username: owner.username,
      position: Object.freeze({ ...owner.position }),
      dimension: owner.dimension
    });
  }

  async lookAt(target: SafePosition): Promise<void> {
    this.lookTargets.push(Object.freeze({ ...target }));
    this.log.push('look');
    if (this.lookFailure !== undefined) throw this.lookFailure;
    if (this.lookGate !== undefined) await this.lookGate.promise;
  }

  setForward(active: boolean): void {
    this.forward = active;
    this.forwardCalls.push(active);
    this.log.push(`forward:${String(active)}`);
  }

  clearControls(): void {
    this.clearCalls += 1;
    this.forward = false;
    this.log.push('clear');
  }

  inspectForwardStep(target: ObservedPlayer): ForwardSafety {
    if (this.throwOnInspect) throw new Error('injected tick inspection error');
    this.inspectCalls += 1;
    this.inspectedOwners.push(
      Object.freeze({
        username: target.username,
        position: Object.freeze({ ...target.position }),
        dimension: target.dimension
      })
    );
    return this.safetyValue.kind === 'safe'
      ? Object.freeze({
          kind: 'safe',
          candidate: Object.freeze({ ...this.safetyValue.candidate })
        })
      : this.safetyValue;
  }

  onMovementTick(handler: () => void): () => void {
    this.tickRegistrations += 1;
    this.#tickHandlers.add(handler);
    this.maximumListeners = Math.max(
      this.maximumListeners,
      this.#tickHandlers.size
    );
    this.log.push('listen');
    let cleaned = false;
    return () => {
      if (cleaned) return;
      cleaned = true;
      this.#tickHandlers.delete(handler);
      this.tickCleanups += 1;
      this.log.push('cleanup');
    };
  }

  listenerCount(): number {
    return this.#tickHandlers.size;
  }

  tick(): void {
    this.log.push('tick');
    for (const handler of [...this.#tickHandlers]) handler();
  }

  setBotPosition(next: SafePosition | undefined): void {
    this.botPosition =
      next === undefined ? undefined : Object.freeze({ ...next });
    this.stateValue = Object.freeze({
      ...this.stateValue,
      roundedPosition:
        next === undefined ? null : Object.freeze({ ...next })
    });
  }

  emit(event: MinecraftAdapterEvent): void {
    this.eventHandler?.(event);
  }
}

function position(x: number, y: number, z: number): SafePosition {
  return Object.freeze({ x, y, z });
}

function observedOwner(
  x: number,
  dimension: MinecraftDimension = 'minecraft:overworld'
): ObservedPlayer {
  return Object.freeze({
    username: 'Neety',
    position: position(x, 64, 0),
    dimension
  });
}

function safeStep(x: number): ForwardSafety {
  return Object.freeze({ kind: 'safe', candidate: position(x, 64, 0) });
}

function unsafeStep(
  kind: Exclude<ForwardSafety['kind'], 'safe'>
): ForwardSafety {
  return Object.freeze({ kind });
}

function connectedState(
  roundedPosition: SafePosition = position(0, 64, 0)
): MinecraftAdapterState {
  return Object.freeze({
    connectionState: 'connected',
    spawned: true,
    alive: true,
    negotiatedVersion: '1.21.11',
    dimension: 'minecraft:overworld',
    roundedPosition: Object.freeze({ ...roundedPosition }),
    health: 20,
    hunger: 20,
    lastDisconnectCategory: null
  });
}

type FakeTimerRecord = Readonly<{
  callback: () => void;
  milliseconds: number;
}>;

class FakeTimers {
  readonly active = new Set<MinecraftDispatcherTimer>();
  readonly delays: number[] = [];
  readonly #records = new Map<MinecraftDispatcherTimer, FakeTimerRecord>();

  readonly setTimeout = (
    callback: () => void,
    milliseconds: number
  ): MinecraftDispatcherTimer => {
    const timer = Object.freeze({ unref: () => undefined });
    this.active.add(timer);
    this.delays.push(milliseconds);
    this.#records.set(timer, Object.freeze({ callback, milliseconds }));
    return timer;
  };

  readonly clearTimeout = (timer: MinecraftDispatcherTimer): void => {
    this.active.delete(timer);
    this.#records.delete(timer);
  };

  fireDelay(milliseconds: number): void {
    const timer = [...this.active].find(
      (candidate) => this.#records.get(candidate)?.milliseconds === milliseconds
    );
    if (timer === undefined) {
      throw new Error(`No active fake timer with delay ${milliseconds}`);
    }
    const record = this.#records.get(timer);
    this.active.delete(timer);
    this.#records.delete(timer);
    record?.callback();
  }
}

type ExecutorHarness = Readonly<{
  adapter: FakeMovementAdapter;
  clock: FakeClock;
  controller: AbortController;
  executor: CompanionExecutor;
  progress: CompanionProgress[];
  states: string[];
  timers: FakeTimers;
}>;

function executorHarness(
  options: Readonly<{
    adapter?: FakeMovementAdapter;
    ownerUsername?: string | null;
  }> = {}
): ExecutorHarness {
  const adapter = options.adapter ?? new FakeMovementAdapter();
  const clock = new FakeClock();
  const controller = new AbortController();
  const progress: CompanionProgress[] = [];
  const states: string[] = [];
  const timers = new FakeTimers();
  const executor = new CompanionExecutor(
    adapter,
    options.ownerUsername === undefined ? 'Neety' : options.ownerUsername,
    {
      now: clock.now,
      monotonicNowMs: clock.monotonicNowMs,
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
      onStateChange: (state) => {
        states.push(state);
        adapter.log.push(`state:${state}`);
      },
      onProgress: (value) => progress.push(value)
    }
  );
  executor.synchronizeState('IDLE');
  return Object.freeze({
    adapter,
    clock,
    controller,
    executor,
    progress,
    states,
    timers
  });
}

async function flushMicrotasks(iterations = 12): Promise<void> {
  for (let index = 0; index < iterations; index += 1) {
    await Promise.resolve();
  }
}

async function physicsTick(
  value: Pick<ExecutorHarness, 'adapter' | 'clock'>,
  advanceMs = 0
): Promise<void> {
  value.clock.advance(advanceMs);
  value.adapter.tick();
  await flushMicrotasks();
}

function trackResult(promise: Promise<CompanionExecutionResult>): {
  readonly settlements: () => number;
  readonly result: () => CompanionExecutionResult | undefined;
} {
  let settlements = 0;
  let settledResult: CompanionExecutionResult | undefined;
  void promise.then((result) => {
    settlements += 1;
    settledResult = result;
  });
  return Object.freeze({
    settlements: () => settlements,
    result: () => settledResult
  });
}

function beginFollow(
  value: ExecutorHarness,
  leaseSeconds = 30,
  deadlineMs = 60_000
): Promise<CompanionExecutionResult> {
  return value.executor.followOwner(
    3,
    leaseSeconds,
    value.clock.wallMs + deadlineMs,
    value.controller.signal
  );
}

function beginCome(
  value: ExecutorHarness,
  deadlineMs = 60_000
): Promise<CompanionExecutionResult> {
  return value.executor.comeHere(
    3,
    value.clock.wallMs + deadlineMs,
    value.controller.signal
  );
}

test('movement preconditions require connected, alive, Overworld owner facts', async () => {
  const value = executorHarness();
  assert.equal(value.executor.precondition('follow_owner'), null);
  assert.equal(value.executor.precondition('come_here'), null);
  assert.equal(value.executor.precondition('look_at_owner'), null);
  assert.equal(value.executor.precondition('wait_here'), null);

  value.adapter.ownerValue = undefined;
  assert.equal(value.executor.precondition('follow_owner'), 'OWNER_NOT_PRESENT');
  const unconfigured = executorHarness({ ownerUsername: null });
  assert.equal(
    unconfigured.executor.precondition('follow_owner'),
    'OWNER_NOT_CONFIGURED'
  );

  value.adapter.ownerValue = observedOwner(33);
  assert.equal(
    value.executor.precondition('follow_owner'),
    'OWNER_TOO_FAR_AWAY'
  );
  assert.equal(value.executor.precondition('come_here'), 'OWNER_TOO_FAR_AWAY');

  value.adapter.ownerValue = observedOwner(8, 'minecraft:the_nether');
  assert.equal(
    value.executor.precondition('look_at_owner'),
    'UNSUPPORTED_DIMENSION'
  );

  value.adapter.ownerValue = observedOwner(8);
  value.adapter.stateValue = Object.freeze({
    ...connectedState(),
    dimension: 'minecraft:the_nether'
  });
  assert.equal(
    value.executor.precondition('wait_here'),
    'UNSUPPORTED_DIMENSION'
  );
  value.adapter.stateValue = Object.freeze({ ...connectedState(), alive: false });
  assert.equal(value.executor.precondition('wait_here'), 'BOT_DEAD');
  value.adapter.stateValue = disconnectedMinecraftAdapterState();
  assert.equal(
    value.executor.precondition('wait_here'),
    'MINECRAFT_NOT_CONNECTED'
  );

  const active = executorHarness();
  const movement = beginFollow(active);
  assert.equal(active.executor.precondition('come_here'), 'COMMAND_ALREADY_ACTIVE');
  active.executor.waitHere();
  assert.equal((await movement).outcome, 'cancelled');
});

test('follow walks only forward on a safe flat direct step', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  assert.equal(value.executor.state(), 'FOLLOWING');
  assert.equal(value.adapter.listenerCount(), 1);
  assert.equal(value.adapter.maximumListeners, 1);

  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  assert.deepEqual(value.executor.activeMovementTarget(), position(8, 64, 0));
  assert.deepEqual(value.adapter.lookTargets, [position(8, 65.6, 0)]);
  assert.equal(value.adapter.inspectCalls, 2);
  assert.deepEqual(value.adapter.forwardCalls, [true]);

  const wait = value.executor.waitHere();
  assert.equal(wait.outcome, 'completed');
  assert.equal((await movement).outcome, 'cancelled');
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
});

test('follow holds distance, resumes when owner moves, and remains FOLLOWING', async () => {
  const value = executorHarness();
  value.adapter.ownerValue = observedOwner(2);
  const movement = beginFollow(value);

  await physicsTick(value);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.executor.state(), 'FOLLOWING');
  assert.equal(value.adapter.listenerCount(), 1);

  value.adapter.ownerValue = observedOwner(8);
  await physicsTick(value, 100);
  assert.equal(value.adapter.forward, true);
  assert.equal(value.executor.state(), 'FOLLOWING');

  value.adapter.ownerValue = observedOwner(2);
  await physicsTick(value, 100);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.executor.state(), 'FOLLOWING');

  value.adapter.ownerValue = observedOwner(9);
  await physicsTick(value, 100);
  assert.equal(value.adapter.forward, true);
  value.executor.waitHere();
  assert.equal((await movement).outcome, 'cancelled');
});

test('owner loss clears controls and target on the first tick and stays stationary during grace', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  const tracked = trackResult(movement);
  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  assert.notEqual(value.executor.activeMovementTarget(), null);

  value.adapter.ownerValue = undefined;
  value.adapter.tick();
  assert.equal(value.adapter.forward, false);
  assert.equal(value.executor.activeMovementTarget(), null);
  assert.equal(value.adapter.listenerCount(), 1);
  await flushMicrotasks();
  assert.equal(tracked.settlements(), 0);

  await physicsTick(value, 9_999);
  assert.equal(value.adapter.forward, false);
  assert.equal(tracked.settlements(), 0);

  value.adapter.ownerValue = observedOwner(7);
  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  assert.deepEqual(value.executor.activeMovementTarget(), position(7, 64, 0));
  assert.equal(tracked.settlements(), 0);

  value.executor.waitHere();
  assert.equal((await movement).outcome, 'cancelled');
});

test('owner-loss grace expires once after exactly ten stationary seconds', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  const tracked = trackResult(movement);
  await physicsTick(value);

  value.adapter.ownerValue = undefined;
  value.adapter.tick();
  assert.equal(value.adapter.forward, false);
  assert.equal(value.executor.activeMovementTarget(), null);
  await flushMicrotasks();
  await physicsTick(value, 9_999);
  assert.equal(tracked.settlements(), 0);
  await physicsTick(value, 1);

  const result = await movement;
  assert.equal(result.outcome, 'failed');
  assert.equal(result.failureCode, 'OWNER_NOT_PRESENT');
  assert.equal(tracked.settlements(), 1);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.forward, false);
  value.adapter.tick();
  await flushMicrotasks();
  assert.equal(tracked.settlements(), 1);
});

test('owner-loss timer expires without ticks and a late owner return cannot resume', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  const tracked = trackResult(movement);
  await physicsTick(value);
  value.adapter.ownerValue = undefined;
  value.adapter.tick();
  await flushMicrotasks();
  assert.equal(value.adapter.forward, false);
  assert.equal(value.executor.activeMovementTarget(), null);
  assert.equal(value.timers.active.size, 2);

  value.clock.advance(10_000);
  value.adapter.ownerValue = observedOwner(8);
  value.timers.fireDelay(10_000);
  await flushMicrotasks();

  const result = await movement;
  assert.equal(result.failureCode, 'OWNER_NOT_PRESENT');
  assert.equal(tracked.settlements(), 1);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.timers.active.size, 0);
  value.adapter.tick();
  await flushMicrotasks();
  assert.equal(value.adapter.forward, false);
  assert.equal(tracked.settlements(), 1);
});

test('owner return observed after grace elapsed fails before steering resumes', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  await physicsTick(value);
  value.adapter.ownerValue = undefined;
  await physicsTick(value);
  value.clock.advance(10_000);
  value.adapter.ownerValue = observedOwner(8);
  value.adapter.tick();
  assert.equal(value.adapter.forward, false);
  await flushMicrotasks();
  assert.equal((await movement).failureCode, 'OWNER_NOT_PRESENT');
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.timers.active.size, 0);
});

test('follow fails safely for owner distance and dimension changes', async () => {
  const tooFar = executorHarness();
  const farMovement = beginFollow(tooFar);
  tooFar.adapter.ownerValue = observedOwner(33);
  await physicsTick(tooFar);
  assert.equal((await farMovement).failureCode, 'OWNER_TOO_FAR_AWAY');
  assert.equal(tooFar.adapter.listenerCount(), 0);

  const wrongDimension = executorHarness();
  const dimensionMovement = beginFollow(wrongDimension);
  wrongDimension.adapter.ownerValue = observedOwner(8, 'minecraft:the_nether');
  await physicsTick(wrongDimension);
  assert.equal(
    (await dimensionMovement).failureCode,
    'UNSUPPORTED_DIMENSION'
  );
  assert.equal(wrongDimension.adapter.forward, false);
});

const SAFETY_FAILURES = [
  ['blocked', 'PATH_NOT_FOUND'],
  ['unsupported_drop', 'DESTINATION_UNAVAILABLE'],
  ['hazard', 'SAFETY_POLICY_BLOCKED'],
  ['liquid', 'SAFETY_POLICY_BLOCKED'],
  ['unloaded', 'DESTINATION_UNAVAILABLE'],
  ['dimension_mismatch', 'SAFETY_POLICY_BLOCKED']
] as const satisfies readonly [
  Exclude<ForwardSafety['kind'], 'safe'>,
  FailureCode
][];

for (const [safetyKind, failureCode] of SAFETY_FAILURES) {
  test(`follow remains stopped and fails after bounded ${safetyKind} retries`, async () => {
    const value = executorHarness();
    value.adapter.safetyValue = unsafeStep(safetyKind);
    const movement = beginFollow(value);

    await physicsTick(value);
    assert.equal(value.adapter.forward, false);
    assert.equal(value.executor.activeMovementTarget(), null);
    await physicsTick(value, 999);
    assert.equal(value.adapter.forward, false);
    await physicsTick(value, 1);
    assert.equal(value.adapter.forward, false);
    await physicsTick(value, 1_000);

    const result = await movement;
    assert.equal(result.outcome, 'failed');
    assert.equal(result.failureCode, failureCode);
    assert.equal(value.adapter.forward, false);
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.inspectCalls, 4);
  });
}

test('alternating unsafe kinds share one global three-retry budget', async () => {
  const value = executorHarness();
  value.adapter.safetyValue = unsafeStep('blocked');
  const movement = beginFollow(value);
  await physicsTick(value);
  assert.equal(value.adapter.listenerCount(), 1);

  value.adapter.safetyValue = unsafeStep('hazard');
  await physicsTick(value, 1_000);
  assert.equal(value.adapter.listenerCount(), 1);

  value.adapter.safetyValue = unsafeStep('liquid');
  await physicsTick(value, 1_000);
  const result = await movement;
  assert.equal(result.failureCode, 'SAFETY_POLICY_BLOCKED');
  assert.equal(value.adapter.inspectCalls, 3);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.timers.active.size, 0);
});

test('alternating unsafe kinds cannot reset the global five-second cap', async () => {
  const value = executorHarness();
  value.adapter.safetyValue = unsafeStep('blocked');
  const movement = beginFollow(value);
  await physicsTick(value);

  value.adapter.safetyValue = unsafeStep('hazard');
  await physicsTick(value, 4_999);
  assert.equal(value.adapter.listenerCount(), 1);

  value.adapter.safetyValue = unsafeStep('unloaded');
  await physicsTick(value, 1);
  const result = await movement;
  assert.equal(result.failureCode, 'DESTINATION_UNAVAILABLE');
  assert.equal(value.adapter.inspectCalls, 3);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.forward, false);
});

test('safe direct movement fails after five seconds without material progress', async () => {
  const value = executorHarness();
  const movement = beginFollow(value);
  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  await physicsTick(value, 4_999);
  assert.equal(value.adapter.forward, true);
  await physicsTick(value, 1);

  const result = await movement;
  assert.equal(result.failureCode, 'PATH_STALLED');
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
});

test('follow honors the smaller command deadline, lease, and 900-second cap', async () => {
  const deadline = executorHarness();
  const deadlineMovement = beginFollow(deadline, 30, 2_000);
  await physicsTick(deadline, 2_000);
  assert.equal((await deadlineMovement).outcome, 'timed_out');

  const lease = executorHarness();
  const leaseMovement = beginFollow(lease, 5, 60_000);
  await physicsTick(lease, 5_000);
  assert.equal((await leaseMovement).failureCode, 'DEADLINE_EXCEEDED');

  const capped = executorHarness();
  const cappedMovement = beginFollow(capped, 1_000, 1_000_000);
  await physicsTick(capped, 900_000);
  assert.equal((await cappedMovement).outcome, 'timed_out');
});

for (const kind of ['follow', 'come'] as const) {
  test(`${kind} deadline timer terminalizes and cleans up without a physics tick`, async () => {
    const value = executorHarness();
    const delay = kind === 'follow' ? 5_000 : 2_000;
    const movement =
      kind === 'follow'
        ? beginFollow(value, 5, 60_000)
        : beginCome(value, delay);
    const tracked = trackResult(movement);
    assert.equal(value.adapter.listenerCount(), 1);
    assert.equal(value.adapter.inspectCalls, 0);
    assert.equal(value.timers.active.size, 1);

    value.clock.advance(delay);
    value.timers.fireDelay(delay);
    await flushMicrotasks();

    const result = await movement;
    assert.equal(result.outcome, 'timed_out');
    assert.equal(result.failureCode, 'DEADLINE_EXCEEDED');
    assert.equal(tracked.settlements(), 1);
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.tickCleanups, 1);
    assert.equal(value.adapter.forward, false);
    assert.equal(value.adapter.inspectCalls, 0);
    assert.equal(value.timers.active.size, 0);
  });
}

test('progress is material and throttled to at most one update per five seconds', async () => {
  const value = executorHarness();
  value.adapter.ownerValue = observedOwner(20);
  const movement = beginFollow(value, 30, 30_000);

  for (let second = 1; second <= 12; second += 1) {
    value.adapter.setBotPosition(position(second * 0.25, 64, 0));
    await physicsTick(value, 1_000);
  }

  assert.deepEqual(
    value.progress.map((progress) => progress.phase),
    ['started', 'moving', 'moving']
  );
  assert.equal(value.progress.length, 3);
  value.executor.waitHere();
  assert.equal((await movement).outcome, 'cancelled');
});

test('come re-reads a moving owner and completes with full cleanup on arrival', async () => {
  const value = executorHarness();
  const movement = beginCome(value);
  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  assert.deepEqual(value.executor.activeMovementTarget(), position(8, 64, 0));

  value.adapter.ownerValue = observedOwner(6);
  value.adapter.setBotPosition(position(1, 64, 0));
  await physicsTick(value, 500);
  assert.equal(value.adapter.forward, true);
  assert.deepEqual(value.executor.activeMovementTarget(), position(6, 64, 0));
  assert.deepEqual(value.adapter.lookTargets.at(-1), position(6, 65.6, 0));

  value.adapter.ownerValue = observedOwner(3);
  await physicsTick(value, 500);
  const result = await movement;
  assert.equal(result.outcome, 'completed');
  assert.equal(value.executor.state(), 'IDLE');
  assert.equal(value.executor.activeMovementTarget(), null);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
});

test('come stops immediately for missing, distant, blocked, and unsafe owner routes', async () => {
  const missing = executorHarness();
  const missingMovement = beginCome(missing);
  await physicsTick(missing);
  assert.equal(missing.adapter.forward, true);
  missing.adapter.ownerValue = undefined;
  missing.adapter.tick();
  assert.equal(missing.adapter.forward, false);
  assert.equal(missing.executor.activeMovementTarget(), null);
  await flushMicrotasks();
  assert.equal((await missingMovement).failureCode, 'OWNER_NOT_PRESENT');
  assert.equal(missing.adapter.listenerCount(), 0);

  const distant = executorHarness();
  distant.adapter.ownerValue = observedOwner(33);
  assert.equal(distant.executor.precondition('come_here'), 'OWNER_TOO_FAR_AWAY');
  const distantMovement = beginCome(distant);
  await physicsTick(distant);
  assert.equal((await distantMovement).failureCode, 'OWNER_TOO_FAR_AWAY');

  for (const safetyKind of ['blocked', 'hazard'] as const) {
    const unsafe = executorHarness();
    unsafe.adapter.safetyValue = unsafeStep(safetyKind);
    const unsafeMovement = beginCome(unsafe);
    await physicsTick(unsafe);
    await physicsTick(unsafe, 1_000);
    await physicsTick(unsafe, 1_000);
    const result = await unsafeMovement;
    assert.equal(
      result.failureCode,
      safetyKind === 'blocked' ? 'PATH_NOT_FOUND' : 'SAFETY_POLICY_BLOCKED'
    );
    assert.equal(unsafe.adapter.forward, false);
    assert.equal(unsafe.adapter.listenerCount(), 0);
  }
});

test('come enforces its command deadline and hard 60-second cap', async () => {
  const commandDeadline = executorHarness();
  const first = beginCome(commandDeadline, 2_000);
  await physicsTick(commandDeadline, 2_000);
  assert.equal((await first).failureCode, 'DEADLINE_EXCEEDED');

  const capped = executorHarness();
  const second = beginCome(capped, 120_000);
  await physicsTick(capped, 60_000);
  assert.equal((await second).outcome, 'timed_out');
  assert.equal(capped.adapter.listenerCount(), 0);
});

test('look is bounded, clears movement controls, and uses one adapter look', async () => {
  const value = executorHarness();
  value.adapter.forward = true;
  value.adapter.log.length = 0;
  const result = await value.executor.lookAtOwner(
    1,
    value.clock.wallMs + 5_000,
    value.controller.signal
  );
  assert.equal(result.outcome, 'completed');
  assert.equal(value.adapter.log[0], 'clear');
  assert.equal(value.adapter.forward, false);
  assert.deepEqual(value.adapter.lookTargets, [position(8, 65.6, 0)]);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.executor.state(), 'IDLE');

  for (const duration of [0.05, 10.01]) {
    const invalid = await value.executor.lookAtOwner(
      duration,
      value.clock.wallMs + 5_000,
      value.controller.signal
    );
    assert.equal(invalid.failureCode, 'INVALID_COMMAND');
  }

  const expired = await value.executor.lookAtOwner(
    1,
    value.clock.wallMs,
    value.controller.signal
  );
  assert.equal(expired.outcome, 'timed_out');
});

test('look fails safely for missing owner, wrong dimension, rejection, and cancellation', async () => {
  const missing = executorHarness();
  missing.adapter.ownerValue = undefined;
  assert.equal(
    (
      await missing.executor.lookAtOwner(
        1,
        missing.clock.wallMs + 1_000,
        missing.controller.signal
      )
    ).failureCode,
    'OWNER_NOT_PRESENT'
  );

  const dimension = executorHarness();
  dimension.adapter.ownerValue = observedOwner(8, 'minecraft:the_nether');
  assert.equal(
    (
      await dimension.executor.lookAtOwner(
        1,
        dimension.clock.wallMs + 1_000,
        dimension.controller.signal
      )
    ).failureCode,
    'UNSUPPORTED_DIMENSION'
  );

  const rejected = executorHarness();
  rejected.adapter.lookFailure = new Error('bounded fake rejection');
  assert.equal(
    (
      await rejected.executor.lookAtOwner(
        1,
        rejected.clock.wallMs + 1_000,
        rejected.controller.signal
      )
    ).failureCode,
    'DESTINATION_UNAVAILABLE'
  );
  assert.equal(rejected.adapter.forward, false);

  const cancelled = executorHarness();
  cancelled.controller.abort();
  assert.equal(
    (
      await cancelled.executor.lookAtOwner(
        1,
        cancelled.clock.wallMs + 1_000,
        cancelled.controller.signal
      )
    ).outcome,
    'cancelled'
  );
});

for (const kind of ['follow', 'come'] as const) {
  test(`wait synchronously preempts ${kind}, cleans its listener, and is idempotent`, async () => {
    const value = executorHarness();
    const movement = kind === 'follow' ? beginFollow(value) : beginCome(value);
    await physicsTick(value);
    assert.equal(value.adapter.forward, true);
    value.adapter.log.length = 0;

    const first = value.executor.waitHere();
    assert.equal(value.adapter.log[0], 'clear');
    assert.equal(first.outcome, 'completed');
    assert.equal(value.executor.state(), 'WAITING');
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.forward, false);
    assert.equal((await movement).outcome, 'cancelled');
    assert.equal(
      value.adapter.log.indexOf('cleanup') <
        value.adapter.log.indexOf('state:WAITING'),
      true
    );

    const cleanups = value.adapter.tickCleanups;
    const second = value.executor.waitHere();
    assert.equal(second.outcome, 'completed');
    assert.equal(value.executor.state(), 'WAITING');
    assert.equal(value.adapter.tickCleanups, cleanups);
  });
}

for (const kind of ['follow', 'come'] as const) {
  test(`stop synchronously preempts ${kind} and remains idempotent`, async () => {
    const value = executorHarness();
    const movement = kind === 'follow' ? beginFollow(value) : beginCome(value);
    await physicsTick(value);
    value.adapter.log.length = 0;

    const first = value.executor.stop();
    assert.equal(value.adapter.log[0], 'clear');
    assert.equal(first.outcome, 'completed');
    assert.equal((await movement).outcome, 'cancelled');
    assert.equal(value.executor.state(), 'IDLE');
    assert.equal(value.adapter.forward, false);
    assert.equal(value.adapter.listenerCount(), 0);

    const second = value.executor.stop();
    assert.equal(second.outcome, 'completed');
    assert.equal(value.executor.state(), 'IDLE');
  });
}

test('late look completion cannot restore movement after stop', async () => {
  const value = executorHarness();
  const gate = deferred<void>();
  value.adapter.lookGate = gate;
  const movement = beginFollow(value);
  const tracked = trackResult(movement);
  value.adapter.tick();
  await flushMicrotasks();
  assert.equal(value.adapter.forward, false);

  value.executor.stop();
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal((await movement).outcome, 'cancelled');
  gate.resolve(undefined);
  await flushMicrotasks();
  assert.equal(value.adapter.forward, false);
  assert.equal(tracked.settlements(), 1);
});

for (const lifecycle of ['death', 'disconnect'] as const) {
  test(`late steering look after adapter ${lifecycle} never activates forward`, async () => {
    const value = executorHarness();
    const gate = deferred<void>();
    value.adapter.lookGate = gate;
    const movement = beginFollow(value);
    const tracked = trackResult(movement);
    value.adapter.tick();
    await flushMicrotasks();
    assert.equal(value.adapter.lookTargets.length, 1);
    assert.equal(value.adapter.forward, false);

    if (lifecycle === 'death') {
      value.adapter.stateValue = Object.freeze({
        ...connectedState(),
        alive: false
      });
    } else {
      value.adapter.stateValue = disconnectedMinecraftAdapterState('ended');
      value.adapter.setBotPosition(undefined);
    }
    gate.resolve(undefined);
    await flushMicrotasks();

    const result = await movement;
    assert.equal(
      result.failureCode,
      lifecycle === 'death' ? 'BOT_DEAD' : 'MINECRAFT_SERVER_DISCONNECTED'
    );
    assert.equal(value.adapter.forward, false);
    assert.equal(value.adapter.forwardCalls.includes(true), false);
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.timers.active.size, 0);
    assert.equal(tracked.settlements(), 1);
  });
}

test('an injected movement-tick error fails and cleans the session exactly once', async () => {
  const value = executorHarness();
  value.adapter.throwOnInspect = true;
  const movement = beginFollow(value);
  const tracked = trackResult(movement);
  value.adapter.tick();
  await flushMicrotasks();

  const result = await movement;
  assert.equal(result.outcome, 'failed');
  assert.equal(result.failureCode, 'INTERNAL_SIDECAR_ERROR');
  assert.equal(tracked.settlements(), 1);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.tickCleanups, 1);
  assert.equal(value.timers.active.size, 0);
  value.adapter.tick();
  await flushMicrotasks();
  assert.equal(tracked.settlements(), 1);
  assert.equal(value.adapter.tickCleanups, 1);
});

for (const kind of ['follow', 'come'] as const) {
  test(`emergency stop latches and preempts active ${kind} before async work`, async () => {
    const value = executorHarness();
    const movement = kind === 'follow' ? beginFollow(value) : beginCome(value);
    await physicsTick(value);
    value.adapter.log.length = 0;

    value.executor.emergencyStop();
    assert.equal(value.adapter.log[0], 'clear');
    assert.equal(value.executor.state(), 'STOPPED');
    assert.equal(value.adapter.forward, false);
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal((await movement).outcome, 'cancelled');

    value.executor.emergencyStop();
    assert.equal(value.executor.stop().outcome, 'completed');
    assert.equal(value.executor.state(), 'STOPPED');
    assert.throws(() => value.executor.synchronizeState('IDLE'));
    value.executor.synchronizeState('IDLE', { emergencyRecovery: true });
    assert.equal(value.executor.state(), 'IDLE');
  });
}

test('disconnect, death, and respawn terminate movement without resumption', async () => {
  const disconnected = executorHarness();
  const disconnectMovement = beginFollow(disconnected);
  await physicsTick(disconnected);
  disconnected.adapter.stateValue = disconnectedMinecraftAdapterState('ended');
  disconnected.adapter.setBotPosition(undefined);
  await physicsTick(disconnected);
  assert.equal(
    (await disconnectMovement).failureCode,
    'MINECRAFT_SERVER_DISCONNECTED'
  );
  assert.equal(disconnected.executor.state(), 'DISCONNECTED');
  assert.equal(disconnected.adapter.listenerCount(), 0);

  const dead = executorHarness();
  const deathMovement = beginCome(dead);
  await physicsTick(dead);
  dead.adapter.stateValue = Object.freeze({ ...connectedState(), alive: false });
  await physicsTick(dead);
  assert.equal((await deathMovement).failureCode, 'BOT_DEAD');
  assert.equal(dead.executor.state(), 'DEAD');
  assert.equal(dead.adapter.listenerCount(), 0);

  dead.adapter.stateValue = connectedState();
  dead.adapter.setBotPosition(position(0, 64, 0));
  dead.executor.synchronizeState('IDLE', { respawnRecovery: true });
  assert.equal(dead.executor.state(), 'IDLE');
  assert.equal(dead.adapter.listenerCount(), 0);
  assert.equal(dead.adapter.forward, false);
});

test('repeated movement cycles never accumulate physics listeners', async () => {
  const value = executorHarness();
  for (let index = 0; index < 20; index += 1) {
    const movement = beginFollow(value);
    assert.equal(value.adapter.listenerCount(), 1);
    value.executor.waitHere();
    assert.equal((await movement).outcome, 'cancelled');
    assert.equal(value.adapter.listenerCount(), 0);
  }
  assert.equal(value.adapter.tickRegistrations, 20);
  assert.equal(value.adapter.tickCleanups, 20);
  assert.equal(value.adapter.maximumListeners, 1);
});

type DispatcherHarness = Readonly<{
  adapter: FakeMovementAdapter;
  clock: FakeClock;
  dispatcher: MinecraftCommandDispatcher;
  messages: SidecarOutboundMessage[];
  timers: FakeTimers;
}>;

async function dispatcherHarness(): Promise<DispatcherHarness> {
  const adapter = new FakeMovementAdapter();
  const clock = new FakeClock();
  const messages: SidecarOutboundMessage[] = [];
  const timers = new FakeTimers();
  const dispatcher = new MinecraftCommandDispatcher({
    config: {
      minecraftServerHost: '127.0.0.1',
      minecraftServerPort: 25_565,
      minecraftVersion: '1.21.11',
      minecraftAccountMode: 'offline',
      minecraftBotUsername: 'Shana'
    },
    adapter,
    ownerUsername: 'Neety',
    now: clock.now,
    monotonicNowMs: clock.monotonicNowMs,
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    send: async (message) => {
      messages.push(message);
      const commandId =
        'command_id' in message && message.command_id !== undefined
          ? message.command_id
          : '-';
      adapter.log.push(`send:${message.type}:${commandId}`);
    }
  });
  dispatcher.beginSession(1_000, 600);
  adapter.emit({ type: 'respawn' });
  await dispatcher.waitForEvents();
  await flushMicrotasks();
  assert.equal(dispatcher.status().companionState, 'IDLE');
  messages.length = 0;
  adapter.log.length = 0;
  return Object.freeze({ adapter, clock, dispatcher, messages, timers });
}

function command(
  value: Pick<DispatcherHarness, 'clock'>,
  commandId: string,
  name: CommandName,
  args: Record<string, unknown>,
  sequence: number,
  deadlineMs = 60_000
): CommandMessage {
  const parsed = parseProtocolMessage({
    protocol: 'gamma.minecraft',
    version: 1,
    type: 'command',
    message_id: `message-${commandId}`,
    connection_id: 'movement-connection',
    sent_at: new Date(value.clock.wallMs).toISOString(),
    sequence,
    trace_id: `trace-${commandId}`,
    command_id: commandId,
    payload: {
      name,
      deadline_at: new Date(value.clock.wallMs + deadlineMs).toISOString(),
      arguments: args
    }
  });
  assert.equal(parsed.type, 'command');
  if (parsed.type !== 'command') throw new Error('expected command');
  return parsed;
}

type AckMessage = Extract<SidecarOutboundMessage, { type: 'command_ack' }>;
type TerminalMessage = Extract<
  SidecarOutboundMessage,
  { type: 'terminal_result' }
>;

function acknowledgments(
  messages: readonly SidecarOutboundMessage[],
  commandId: string
): AckMessage[] {
  return messages.filter(
    (message): message is AckMessage =>
      message.type === 'command_ack' && message.command_id === commandId
  );
}

function terminals(
  messages: readonly SidecarOutboundMessage[],
  commandId: string
): TerminalMessage[] {
  return messages.filter(
    (message): message is TerminalMessage =>
      message.type === 'terminal_result' && message.command_id === commandId
  );
}

function resultFrameOrder(messages: readonly SidecarOutboundMessage[]): string[] {
  return messages
    .filter(
      (message): message is AckMessage | TerminalMessage =>
        message.type === 'command_ack' || message.type === 'terminal_result'
    )
    .map((message) => `${message.type}:${message.command_id}`);
}

async function beginDispatcherMovement(
  value: DispatcherHarness,
  commandId: string,
  name: 'follow_owner' | 'come_here',
  sequence: number
): Promise<Readonly<{ work: Promise<void> }>> {
  const args =
    name === 'follow_owner'
      ? { follow_distance: 3, lease_duration_seconds: 30 }
      : { arrival_distance: 3 };
  const work = value.dispatcher.handleCommand(
    command(value, commandId, name, args, sequence)
  );
  await flushMicrotasks(30);
  assert.equal(value.adapter.listenerCount(), 1);
  await physicsTick(value);
  assert.equal(value.adapter.forward, true);
  return Object.freeze({ work });
}

for (const movementName of ['follow_owner', 'come_here'] as const) {
  test(`dispatcher wait preempts ${movementName} with exact correlated results`, async () => {
    const value = await dispatcherHarness();
    const movementId = `${movementName}-for-wait`;
    const { work: movementWork } = await beginDispatcherMovement(
      value,
      movementId,
      movementName,
      1
    );
    value.adapter.log.length = 0;

    await value.dispatcher.handleCommand(
      command(value, 'wait-preempt', 'wait_here', {}, 2)
    );
    await movementWork;

    assert.equal(value.adapter.log[0], 'clear');
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.forward, false);
    assert.deepEqual(resultFrameOrder(value.messages), [
      `command_ack:${movementId}`,
      `terminal_result:${movementId}`,
      'command_ack:wait-preempt',
      'terminal_result:wait-preempt'
    ]);
    const movementTerminal = terminals(value.messages, movementId);
    const waitTerminal = terminals(value.messages, 'wait-preempt');
    assert.equal(movementTerminal.length, 1);
    assert.equal(movementTerminal[0]?.trace_id, `trace-${movementId}`);
    assert.equal(movementTerminal[0]?.payload.outcome, 'cancelled');
    assert.equal(waitTerminal.length, 1);
    assert.equal(waitTerminal[0]?.trace_id, 'trace-wait-preempt');
    assert.equal(waitTerminal[0]?.payload.outcome, 'completed');
    assert.equal(value.dispatcher.status().companionState, 'WAITING');
    assert.equal(
      value.adapter.log.indexOf('cleanup') <
        value.adapter.log.indexOf(`send:terminal_result:${movementId}`),
      true
    );
  });
}

for (const movementName of ['follow_owner', 'come_here'] as const) {
  test(`dispatcher stop is accepted during ${movementName} and preempts exactly once`, async () => {
    const value = await dispatcherHarness();
    const movementId = `${movementName}-for-stop`;
    const { work: movementWork } = await beginDispatcherMovement(
      value,
      movementId,
      movementName,
      1
    );
    value.adapter.log.length = 0;

    await value.dispatcher.handleCommand(
      command(value, 'stop-preempt', 'stop', {}, 2)
    );
    await movementWork;

    assert.equal(value.adapter.log[0], 'clear');
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.forward, false);
    assert.deepEqual(resultFrameOrder(value.messages), [
      `command_ack:${movementId}`,
      `terminal_result:${movementId}`,
      'command_ack:stop-preempt',
      'terminal_result:stop-preempt'
    ]);
    assert.equal(terminals(value.messages, movementId).length, 1);
    assert.equal(
      terminals(value.messages, movementId)[0]?.payload.outcome,
      'cancelled'
    );
    assert.equal(acknowledgments(value.messages, 'stop-preempt')[0]?.payload.accepted, true);
    assert.equal(terminals(value.messages, 'stop-preempt').length, 1);
    assert.equal(
      terminals(value.messages, 'stop-preempt')[0]?.payload.outcome,
      'completed'
    );
    assert.equal(value.dispatcher.status().companionState, 'IDLE');
  });
}

test('concurrent stop and wait preemptors serialize and both complete', async () => {
  const value = await dispatcherHarness();
  const { work: followWork } = await beginDispatcherMovement(
    value,
    'follow-concurrent-preempt',
    'follow_owner',
    1
  );
  value.adapter.log.length = 0;

  const stopWork = value.dispatcher.handleCommand(
    command(value, 'concurrent-stop', 'stop', {}, 2)
  );
  const waitWork = value.dispatcher.handleCommand(
    command(value, 'concurrent-wait', 'wait_here', {}, 3)
  );
  await Promise.all([stopWork, waitWork, followWork]);

  assert.deepEqual(resultFrameOrder(value.messages), [
    'command_ack:follow-concurrent-preempt',
    'terminal_result:follow-concurrent-preempt',
    'command_ack:concurrent-stop',
    'terminal_result:concurrent-stop',
    'command_ack:concurrent-wait',
    'terminal_result:concurrent-wait'
  ]);
  assert.equal(
    terminals(value.messages, 'follow-concurrent-preempt')[0]?.payload.outcome,
    'cancelled'
  );
  assert.equal(terminals(value.messages, 'concurrent-stop').length, 1);
  assert.equal(
    terminals(value.messages, 'concurrent-stop')[0]?.payload.outcome,
    'completed'
  );
  assert.equal(terminals(value.messages, 'concurrent-wait').length, 1);
  assert.equal(
    terminals(value.messages, 'concurrent-wait')[0]?.payload.outcome,
    'completed'
  );
  assert.equal(value.dispatcher.status().companionState, 'WAITING');
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.forward, false);
  assert.equal(value.timers.active.size, 0);
});

test('dispatcher emergency stop clears first, persists, rejects movement, and requires leave plus join', async () => {
  const value = await dispatcherHarness();
  const { work: followWork } = await beginDispatcherMovement(
    value,
    'follow-emergency',
    'follow_owner',
    1
  );
  value.adapter.log.length = 0;

  await value.dispatcher.handleCommand(
    command(value, 'emergency', 'emergency_stop', {}, 2)
  );
  await followWork;
  assert.equal(value.adapter.log[0], 'clear');
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.dispatcher.status().emergencyStopActive, true);
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.deepEqual(resultFrameOrder(value.messages), [
    'command_ack:follow-emergency',
    'terminal_result:follow-emergency',
    'command_ack:emergency',
    'terminal_result:emergency'
  ]);
  assert.equal(terminals(value.messages, 'follow-emergency').length, 1);
  assert.equal(
    terminals(value.messages, 'follow-emergency')[0]?.payload.outcome,
    'cancelled'
  );
  assert.equal(terminals(value.messages, 'emergency').length, 1);

  await value.dispatcher.handleCommand(
    command(
      value,
      'rejected-follow',
      'follow_owner',
      { follow_distance: 3, lease_duration_seconds: 30 },
      3
    )
  );
  const rejected = acknowledgments(value.messages, 'rejected-follow');
  assert.equal(rejected.length, 1);
  assert.equal(rejected[0]?.payload.accepted, false);
  assert.equal(rejected[0]?.payload.failure?.code, 'EMERGENCY_STOP_ACTIVE');
  assert.equal(terminals(value.messages, 'rejected-follow').length, 0);

  value.dispatcher.beginSession(1_000, 600);
  await value.dispatcher.handleCommand(
    command(
      value,
      'replacement-rejected',
      'come_here',
      { arrival_distance: 3 },
      4
    )
  );
  assert.equal(
    acknowledgments(value.messages, 'replacement-rejected')[0]?.payload.failure
      ?.code,
    'EMERGENCY_STOP_ACTIVE'
  );
  assert.equal(value.dispatcher.status().emergencyStopActive, true);

  await value.dispatcher.handleCommand(
    command(value, 'stop-latched', 'stop', {}, 5)
  );
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.equal(value.dispatcher.status().emergencyStopActive, true);

  await value.dispatcher.handleCommand(
    command(value, 'leave-latched', 'leave', {}, 6)
  );
  assert.equal(value.adapter.state().connectionState, 'disconnected');
  assert.equal(value.dispatcher.status().companionState, 'STOPPED');
  assert.equal(value.dispatcher.status().emergencyStopActive, true);

  await value.dispatcher.handleCommand(
    command(value, 'fresh-join', 'join', {}, 7)
  );
  assert.equal(value.adapter.connectCalls, 1);
  assert.equal(value.dispatcher.status().companionState, 'IDLE');
  assert.equal(value.dispatcher.status().emergencyStopActive, false);
  assert.equal(terminals(value.messages, 'fresh-join').length, 1);
  assert.equal(terminals(value.messages, 'fresh-join')[0]?.payload.outcome, 'completed');
  assert.equal(value.timers.active.size, 0);
});

test('dispatcher emergency stop terminalizes a pending look once and ignores late completion', async () => {
  const value = await dispatcherHarness();
  const gate = deferred<void>();
  value.adapter.lookGate = gate;
  const lookWork = value.dispatcher.handleCommand(
    command(
      value,
      'pending-look',
      'look_at_owner',
      { duration_seconds: 1 },
      1
    )
  );
  await flushMicrotasks(30);
  assert.equal(value.adapter.lookTargets.length, 1);
  value.adapter.log.length = 0;

  await value.dispatcher.handleCommand(
    command(value, 'look-emergency', 'emergency_stop', {}, 2)
  );
  assert.equal(value.adapter.log[0], 'clear');
  assert.equal(terminals(value.messages, 'pending-look').length, 1);
  assert.equal(
    terminals(value.messages, 'pending-look')[0]?.payload.outcome,
    'cancelled'
  );
  gate.resolve(undefined);
  await lookWork;
  await flushMicrotasks();
  assert.equal(terminals(value.messages, 'pending-look').length, 1);
  assert.equal(value.adapter.forward, false);
});

for (const lifecycle of ['death', 'disconnected'] as const) {
  test(`dispatcher ${lifecycle} clears a bounded look timer and terminalizes once`, async () => {
    const value = await dispatcherHarness();
    const gate = deferred<void>();
    value.adapter.lookGate = gate;
    const commandId = `bounded-look-${lifecycle}`;
    const lookWork = value.dispatcher.handleCommand(
      command(
        value,
        commandId,
        'look_at_owner',
        { duration_seconds: 1 },
        1
      )
    );
    await flushMicrotasks(30);
    assert.equal(value.adapter.lookTargets.length, 1);
    assert.equal(value.timers.active.size, 1);

    if (lifecycle === 'death') {
      value.adapter.stateValue = Object.freeze({
        ...connectedState(),
        alive: false
      });
      value.adapter.emit({ type: 'death' });
    } else {
      value.adapter.stateValue = disconnectedMinecraftAdapterState('ended');
      value.adapter.setBotPosition(undefined);
      value.adapter.emit({ type: 'disconnected', category: 'ended' });
    }
    await value.dispatcher.waitForEvents();
    await lookWork;

    const result = terminals(value.messages, commandId);
    assert.equal(result.length, 1);
    assert.equal(result[0]?.payload.outcome, 'failed');
    assert.equal(
      result[0]?.payload.failure?.code,
      lifecycle === 'death' ? 'BOT_DEAD' : 'MINECRAFT_SERVER_DISCONNECTED'
    );
    assert.equal(value.timers.active.size, 0);
    assert.equal(value.adapter.forward, false);

    gate.resolve(undefined);
    await flushMicrotasks();
    assert.equal(terminals(value.messages, commandId).length, 1);
    assert.equal(value.timers.active.size, 0);
  });
}

for (const lifecycle of ['disconnected', 'death'] as const) {
  test(`dispatcher ${lifecycle} event fails active movement exactly once`, async () => {
    const value = await dispatcherHarness();
    const commandId = `follow-${lifecycle}`;
    const { work } = await beginDispatcherMovement(
      value,
      commandId,
      'follow_owner',
      1
    );
    if (lifecycle === 'disconnected') {
      value.adapter.stateValue = disconnectedMinecraftAdapterState('ended');
      value.adapter.setBotPosition(undefined);
      value.adapter.emit({ type: 'disconnected', category: 'ended' });
    } else {
      value.adapter.stateValue = Object.freeze({
        ...connectedState(),
        alive: false
      });
      value.adapter.emit({ type: 'death' });
    }
    await value.dispatcher.waitForEvents();
    await work;

    const result = terminals(value.messages, commandId);
    assert.equal(result.length, 1);
    assert.equal(result[0]?.payload.outcome, 'failed');
    assert.equal(
      result[0]?.payload.failure?.code,
      lifecycle === 'disconnected'
        ? 'MINECRAFT_SERVER_DISCONNECTED'
        : 'BOT_DEAD'
    );
    assert.equal(value.adapter.listenerCount(), 0);
    assert.equal(value.adapter.forward, false);

    if (lifecycle === 'death') {
      value.adapter.stateValue = connectedState();
      value.adapter.setBotPosition(position(0, 64, 0));
      value.adapter.emit({ type: 'respawn' });
      await value.dispatcher.waitForEvents();
      assert.equal(value.dispatcher.status().companionState, 'IDLE');
      assert.equal(value.adapter.listenerCount(), 0);
    }
  });
}

test('Gamma control disconnect synchronously removes movement authority and does not reconnect', async () => {
  const value = await dispatcherHarness();
  const { work } = await beginDispatcherMovement(
    value,
    'follow-control-loss',
    'follow_owner',
    1
  );
  value.messages.length = 0;
  value.adapter.log.length = 0;

  const disconnecting = value.dispatcher.controlDisconnected();
  assert.equal(value.adapter.log[0], 'clear');
  assert.equal(value.adapter.forward, false);
  assert.equal(value.adapter.listenerCount(), 0);
  assert.equal(value.adapter.eventHandler, undefined);
  await disconnecting;
  await work;

  assert.equal(value.adapter.disconnectCalls, 1);
  assert.equal(value.adapter.connectCalls, 0);
  assert.equal(value.dispatcher.status().companionState, 'DISCONNECTED');
  assert.equal(terminals(value.messages, 'follow-control-loss').length, 0);
  assert.equal(value.timers.active.size, 0);
});
