import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import {
  LOCAL_SMOKE_ENTIRE_TIMEOUT_MS,
  createLocalSmokeCleanup,
  installLocalSmokeSignalHandlers,
  runLocalSmokeMain,
  type LocalSmokeCommandObservation,
  type LocalSmokeConfig,
  type LocalSmokeDependencies,
  type LocalSmokePromptId,
  type LocalSmokePromptRequest,
  type LocalSmokeSession,
  type LocalSmokeTerminal,
  type LocalSmokeTimer,
  type LocalSmokeTimers
} from '../src/local-smoke.js';
import type { CommandName } from '../src/protocol.js';
import type { SafePosition } from '../src/minecraft-adapter.js';

const SECRET_TOKEN = 'local-smoke-secret-token-that-must-not-leak';

const VALID_ENVIRONMENT = Object.freeze({
  SHANA_MINECRAFT_RUN_LOCAL_SMOKE: '1',
  SHANA_MINECRAFT_SERVER_HOST: '127.0.0.1',
  SHANA_MINECRAFT_SERVER_PORT: '25565',
  SHANA_MINECRAFT_VERSION: '1.21.11',
  SHANA_MINECRAFT_ACCOUNT_MODE: 'offline',
  SHANA_MINECRAFT_BOT_USERNAME: 'SmokeBot',
  SHANA_MINECRAFT_OWNER_USERNAME: 'SmokeOwner'
});

type Activity = {
  portChecks: number;
  prompts: LocalSmokePromptId[];
  tokenCreations: number;
  sessionCreations: number;
  timers: FakeTimers;
  logs: string[];
};

function activity(): Activity {
  return {
    portChecks: 0,
    prompts: [],
    tokenCreations: 0,
    sessionCreations: 0,
    timers: new FakeTimers(),
    logs: []
  };
}

function earlyDependencies(
  environment: Readonly<Record<string, string | undefined>>,
  observed: Activity,
  options: Readonly<{ portListening?: boolean; promptAnswer?: string }> = {}
): LocalSmokeDependencies {
  return {
    environment,
    log: (message: string) => observed.logs.push(message),
    prompt: async (request: LocalSmokePromptRequest) => {
      observed.prompts.push(request.id);
      return options.promptAnswer ??
        (request.expected === 'confirm' ? 'confirm' : '');
    },
    checkPort: async () => {
      observed.portChecks += 1;
      return options.portListening ?? true;
    },
    createToken: () => {
      observed.tokenCreations += 1;
      return SECRET_TOKEN;
    },
    createSession: () => {
      observed.sessionCreations += 1;
      throw new Error('session creation must not occur in this test');
    },
    timers: observed.timers,
    signalTarget: new FakeSignalTarget().target(),
    isInteractive: () => true
  } as unknown as LocalSmokeDependencies;
}

type FakeTimerRecord = Readonly<{
  callback: () => void;
  milliseconds: number;
}>;

class FakeTimers implements LocalSmokeTimers {
  readonly #active = new Map<LocalSmokeTimer, FakeTimerRecord>();
  clearCount = 0;
  setCount = 0;

  readonly setTimeout = (
    callback: () => void,
    milliseconds: number
  ): LocalSmokeTimer => {
    const timer = Object.freeze({ unref: () => undefined });
    this.#active.set(timer, Object.freeze({ callback, milliseconds }));
    this.setCount += 1;
    return timer;
  };

  readonly clearTimeout = (timer: LocalSmokeTimer): void => {
    this.#active.delete(timer);
    this.clearCount += 1;
  };

  get activeCount(): number {
    return this.#active.size;
  }

  hasDelay(milliseconds: number): boolean {
    return [...this.#active.values()].some(
      (record) => record.milliseconds === milliseconds
    );
  }

  fireDelay(milliseconds: number): void {
    const match = [...this.#active.entries()].find(
      ([, record]) => record.milliseconds === milliseconds
    );
    assert.notEqual(match, undefined);
    if (match === undefined) return;
    this.#active.delete(match[0]);
    match[1].callback();
  }
}

class FakeSignalTarget extends EventEmitter {
  target(): NodeJS.Process {
    return this as unknown as NodeJS.Process;
  }
}

type FakeSessionOptions = Readonly<{
  closeFails?: boolean;
  startNever?: boolean;
}>;

class FakeSession implements LocalSmokeSession {
  readonly commandNames: CommandName[] = [];
  readonly states: string[] = [];
  readonly #commandObservations: LocalSmokeCommandObservation[] = [];
  readonly #options: FakeSessionOptions;
  closeCalls = 0;
  emergencyCleanupCalls = 0;
  shutdownCalls = 0;
  startCalls = 0;
  startupAssertions = 0;
  disconnectAssertions = 0;
  ownerAssertions = 0;
  ownerDistanceAssertions = 0;
  movedAssertions = 0;
  stationaryAssertions = 0;
  stayedNearAssertions = 0;
  receivedToken: string | undefined;
  #emergencyActive = false;
  #leftWhileEmergency = false;
  #failureHandler: (() => void) | undefined;

  constructor(options: FakeSessionOptions = {}) {
    this.#options = options;
  }

  setFailureHandler(handler: (() => void) | undefined): void {
    this.#failureHandler = handler;
  }

  failSession(): void {
    this.#failureHandler?.();
  }

  async start(
    _config: LocalSmokeConfig,
    controlToken: string,
    signal: AbortSignal
  ): Promise<void> {
    assert.equal(signal.aborted, false);
    this.startCalls += 1;
    this.receivedToken = controlToken;
    if (this.#options.startNever === true) {
      await new Promise<void>(() => undefined);
    }
  }

  async assertStartupDisconnected(): Promise<void> {
    this.startupAssertions += 1;
  }

  async assertMinecraftDisconnected(): Promise<void> {
    this.disconnectAssertions += 1;
  }

  async assertConnectedOwner(): Promise<void> {
    this.ownerAssertions += 1;
  }

  async assertOwnerWithin(maximumDistance: number): Promise<void> {
    assert.equal(maximumDistance, 3);
    this.ownerDistanceAssertions += 1;
  }

  captureBotPosition(): SafePosition {
    return Object.freeze({ x: 10, y: 64, z: 10 });
  }

  async assertBotMovedFrom(
    position: SafePosition,
    minimumDistance: number
  ): Promise<void> {
    assert.deepEqual(position, { x: 10, y: 64, z: 10 });
    assert.equal(minimumDistance, 0.25);
    this.movedAssertions += 1;
  }

  async assertBotStayedNear(
    position: SafePosition,
    maximumDistance: number
  ): Promise<void> {
    assert.deepEqual(position, { x: 10, y: 64, z: 10 });
    assert.equal(maximumDistance, 0.125);
    this.stayedNearAssertions += 1;
  }

  async assertStationary(signal: AbortSignal): Promise<void> {
    assert.equal(signal.aborted, false);
    this.stationaryAssertions += 1;
  }

  async sendCommand(
    name: CommandName,
    _args: Readonly<Record<string, unknown>>,
    deadlineMs: number,
    signal: AbortSignal
  ): Promise<LocalSmokeCommandObservation> {
    assert.equal(signal.aborted, false);
    assert.equal(deadlineMs > 0 && deadlineMs <= 60_000, true);
    this.commandNames.push(name);
    const rejectedForEmergency =
      name === 'follow_owner' && this.#emergencyActive;
    const index = this.#commandObservations.length + 1;
    const observation: LocalSmokeCommandObservation = {
      name,
      commandId: `fake-command-${index}`,
      traceId: `fake-trace-${index}`,
      ackCount: 1,
      accepted: !rejectedForEmergency,
      failureCode: rejectedForEmergency ? 'EMERGENCY_STOP_ACTIVE' : null,
      terminalCount: 0,
      terminalOutcome: null,
      terminalFailureCode: null
    };
    this.#commandObservations.push(observation);
    if (name === 'emergency_stop') this.#emergencyActive = true;
    if (name === 'leave' && this.#emergencyActive) {
      this.#leftWhileEmergency = true;
    }
    if (name === 'join' && this.#leftWhileEmergency) {
      this.#emergencyActive = false;
      this.#leftWhileEmergency = false;
    }
    return observation;
  }

  async waitForTerminal(
    observation: LocalSmokeCommandObservation,
    signal: AbortSignal
  ): Promise<LocalSmokeTerminal> {
    assert.equal(signal.aborted, false);
    assert.equal(observation.accepted, true);
    assert.equal(observation.terminalCount, 0);
    const outcome = observation.name === 'follow_owner'
      ? 'cancelled'
      : 'completed';
    observation.terminalCount = 1;
    observation.terminalOutcome = outcome;
    return Object.freeze({ outcome, failureCode: null });
  }

  async waitForState(state: string, signal: AbortSignal): Promise<void> {
    assert.equal(signal.aborted, false);
    this.states.push(state);
  }

  async shutdownGamma(signal: AbortSignal): Promise<void> {
    assert.equal(signal.aborted, false);
    this.shutdownCalls += 1;
  }

  async emergencyCleanup(): Promise<void> {
    this.#emergencyActive = true;
    this.emergencyCleanupCalls += 1;
  }

  async close(): Promise<void> {
    this.closeCalls += 1;
    if (this.#options.closeFails === true) {
      throw new Error('raw fake close failure');
    }
  }

  observations(): readonly LocalSmokeCommandObservation[] {
    return this.#commandObservations;
  }

  maximumObservedOwnerDistance(): number | null {
    return 8.5;
  }

  emergencyStopConfirmed(): boolean {
    return this.#emergencyActive;
  }
}

type ScriptedRun = Readonly<{
  dependencies: LocalSmokeDependencies;
  logs: string[];
  prompts: LocalSmokePromptId[];
  session: FakeSession;
  signals: FakeSignalTarget;
  timers: FakeTimers;
}>;

function scriptedRun(
  options: Readonly<{
    abortAt?: LocalSmokePromptId;
    session?: FakeSession;
  }> = {}
): ScriptedRun {
  const logs: string[] = [];
  const prompts: LocalSmokePromptId[] = [];
  const session = options.session ?? new FakeSession();
  const signals = new FakeSignalTarget();
  const timers = new FakeTimers();
  let nowMilliseconds = Date.parse('2026-07-11T18:00:00.000Z');
  const prompt = async (
    request: LocalSmokePromptRequest,
    signal: AbortSignal
  ): Promise<string> => {
    assert.equal(signal.aborted, false);
    prompts.push(request.id);
    if (request.id === options.abortAt) return 'abort';
    return request.expected === 'confirm' ? 'confirm' : '';
  };
  return {
    dependencies: {
      environment: VALID_ENVIRONMENT,
      log: (message) => logs.push(message),
      prompt,
      checkPort: async (_config, signal) => !signal.aborted,
      createToken: () => SECRET_TOKEN,
      createSession: () => session,
      now: () => {
        const value = new Date(nowMilliseconds);
        nowMilliseconds += 1_000;
        return value;
      },
      timers,
      signalTarget: signals.target(),
      isInteractive: () => true
    },
    logs,
    prompts,
    session,
    signals,
    timers
  };
}

async function flushUntil(predicate: () => boolean): Promise<void> {
  for (let index = 0; index < 100; index += 1) {
    if (predicate()) return;
    await Promise.resolve();
  }
  assert.fail('Expected asynchronous state was not reached');
}

test('missing explicit opt-in exits before network, prompts, tokens, timers, or runtime activity', async () => {
  const observed = activity();
  const environment = { ...VALID_ENVIRONMENT };
  delete (environment as { SHANA_MINECRAFT_RUN_LOCAL_SMOKE?: string })
    .SHANA_MINECRAFT_RUN_LOCAL_SMOKE;

  const exitCode = await runLocalSmokeMain(
    earlyDependencies(environment, observed)
  );

  assert.notEqual(exitCode, 0);
  assert.equal(observed.portChecks, 0);
  assert.deepEqual(observed.prompts, []);
  assert.equal(observed.tokenCreations, 0);
  assert.equal(observed.sessionCreations, 0);
  assert.equal(observed.timers.setCount, 0);
});

test('every smoke configuration value is explicit and required before activity', async () => {
  for (const name of [
    'SHANA_MINECRAFT_SERVER_HOST',
    'SHANA_MINECRAFT_SERVER_PORT',
    'SHANA_MINECRAFT_VERSION',
    'SHANA_MINECRAFT_ACCOUNT_MODE',
    'SHANA_MINECRAFT_BOT_USERNAME',
    'SHANA_MINECRAFT_OWNER_USERNAME'
  ] as const) {
    const environment: Record<string, string | undefined> = {
      ...VALID_ENVIRONMENT
    };
    delete environment[name];
    const observed = activity();

    const exitCode = await runLocalSmokeMain(
      earlyDependencies(environment, observed)
    );

    assert.notEqual(exitCode, 0, name);
    assert.equal(observed.portChecks, 0, name);
    assert.deepEqual(observed.prompts, [], name);
    assert.equal(observed.tokenCreations, 0, name);
    assert.equal(observed.sessionCreations, 0, name);
    assert.equal(observed.timers.setCount, 0, name);
  }
});

for (const scenario of [
  {
    name: 'non-literal-loopback host',
    override: { SHANA_MINECRAFT_SERVER_HOST: 'localhost' }
  },
  {
    name: 'URL-shaped host',
    override: { SHANA_MINECRAFT_SERVER_HOST: 'http://127.0.0.1' }
  },
  {
    name: 'host with a query string',
    override: { SHANA_MINECRAFT_SERVER_HOST: '127.0.0.1?world=test' }
  },
  {
    name: 'invalid explicit port',
    override: { SHANA_MINECRAFT_SERVER_PORT: '0' }
  },
  {
    name: 'Shana service port',
    override: { SHANA_MINECRAFT_SERVER_PORT: '8000' }
  },
  {
    name: 'Dashboard service port',
    override: { SHANA_MINECRAFT_SERVER_PORT: '8001' }
  },
  {
    name: 'unsupported version',
    override: { SHANA_MINECRAFT_VERSION: '1.21.10' }
  },
  {
    name: 'online account mode',
    override: { SHANA_MINECRAFT_ACCOUNT_MODE: 'microsoft' }
  },
  {
    name: 'matching bot and owner usernames',
    override: { SHANA_MINECRAFT_OWNER_USERNAME: 'SmokeBot' }
  },
  {
    name: 'case-insensitively matching usernames',
    override: { SHANA_MINECRAFT_OWNER_USERNAME: 'smokebot' }
  },
  {
    name: 'invalid owner username',
    override: { SHANA_MINECRAFT_OWNER_USERNAME: 'owner-name' }
  }
] as const) {
  test(`${scenario.name} fails before any activity`, async () => {
    const observed = activity();
    const exitCode = await runLocalSmokeMain(
      earlyDependencies(
        { ...VALID_ENVIRONMENT, ...scenario.override },
        observed
      )
    );

    assert.notEqual(exitCode, 0);
    assert.equal(observed.portChecks, 0);
    assert.deepEqual(observed.prompts, []);
    assert.equal(observed.tokenCreations, 0);
    assert.equal(observed.sessionCreations, 0);
    assert.equal(observed.timers.setCount, 0);
  });
}

test('a non-listening port fails without creating a token or sidecar session', async () => {
  const observed = activity();
  const exitCode = await runLocalSmokeMain(
    earlyDependencies(VALID_ENVIRONMENT, observed, { portListening: false })
  );

  assert.notEqual(exitCode, 0);
  assert.equal(observed.portChecks, 1);
  assert.deepEqual(observed.prompts, []);
  assert.equal(observed.tokenCreations, 0);
  assert.equal(observed.sessionCreations, 0);
  assert.equal(observed.timers.setCount, 0);
});

test('declining the safety confirmation exits before token or session creation', async () => {
  const observed = activity();
  const exitCode = await runLocalSmokeMain(
    earlyDependencies(VALID_ENVIRONMENT, observed, { promptAnswer: 'decline' })
  );

  assert.notEqual(exitCode, 0);
  assert.equal(observed.portChecks, 1);
  assert.equal(observed.prompts.length, 1);
  assert.equal(observed.tokenCreations, 0);
  assert.equal(observed.sessionCreations, 0);
  assert.equal(observed.timers.setCount, 0);
});

test('the fully scripted smoke follows the bounded command sequence and leaves no fake handles', async () => {
  const run = scriptedRun();

  const exitCode = await runLocalSmokeMain(run.dependencies);

  assert.equal(exitCode, 0);
  assert.deepEqual(run.prompts, [
    'safety_confirmation',
    'owner_ready',
    'follow_ready',
    'follow_observed',
    'come_ready',
    'stop_follow_ready',
    'stop_follow_observed',
    'emergency_follow_ready',
    'emergency_follow_observed'
  ]);
  assert.deepEqual(run.session.commandNames, [
    'join',
    'follow_owner',
    'wait_here',
    'come_here',
    'look_at_owner',
    'follow_owner',
    'stop',
    'follow_owner',
    'emergency_stop',
    'follow_owner',
    'leave',
    'join',
    'leave'
  ]);
  assert.equal(run.session.startCalls, 1);
  assert.equal(run.session.startupAssertions, 1);
  assert.equal(run.session.disconnectAssertions, 2);
  assert.equal(run.session.ownerAssertions, 2);
  assert.equal(run.session.ownerDistanceAssertions, 1);
  assert.equal(run.session.movedAssertions, 3);
  assert.equal(run.session.stationaryAssertions, 4);
  assert.equal(run.session.stayedNearAssertions, 1);
  assert.equal(run.session.shutdownCalls, 1);
  assert.equal(run.session.emergencyCleanupCalls, 0);
  assert.equal(run.session.closeCalls, 1);
  assert.equal(run.timers.setCount, 1);
  assert.equal(run.timers.activeCount, 0);
  assert.equal(run.signals.listenerCount('SIGINT'), 0);
  assert.equal(run.signals.listenerCount('SIGTERM'), 0);

  const output = run.logs.join('\n');
  assert.match(output, /LOCAL MINECRAFT SMOKE PASS/u);
  assert.equal(output.includes(SECRET_TOKEN), false);
  assert.equal(output.includes('controlToken'), false);
  const summaryStart = output.indexOf('{');
  assert.notEqual(summaryStart, -1);
  const summary = JSON.parse(output.slice(summaryStart)) as Record<string, unknown>;
  assert.equal(summary.result, 'pass');
  assert.equal(summary.emergencyStopConfirmed, true);
  assert.equal(summary.cleanLeaveConfirmed, true);
  assert.equal(summary.cleanShutdownConfirmed, true);
  assert.equal(summary.maximumObservedOwnerDistance, 8.5);
});

for (const checkpoint of [
  'follow_ready',
  'follow_observed',
  'come_ready',
  'stop_follow_ready',
  'stop_follow_observed',
  'emergency_follow_ready',
  'emergency_follow_observed'
] as const satisfies readonly LocalSmokePromptId[]) {
  test(`abort at movement checkpoint ${checkpoint} performs emergency cleanup`, async () => {
    const run = scriptedRun({ abortAt: checkpoint });

    const exitCode = await runLocalSmokeMain(run.dependencies);

    assert.equal(exitCode, 1);
    assert.equal(run.prompts.includes(checkpoint), true);
    assert.equal(run.session.emergencyCleanupCalls, 1);
    assert.equal(run.session.closeCalls, 1);
    assert.equal(run.timers.activeCount, 0);
    assert.equal(run.signals.listenerCount('SIGINT'), 0);
    assert.equal(run.signals.listenerCount('SIGTERM'), 0);
    assert.equal(run.logs.join('\n').includes(SECRET_TOKEN), false);
  });
}

test('the five-minute deadline aborts a hung session and performs emergency cleanup', async () => {
  const session = new FakeSession({ startNever: true });
  const run = scriptedRun({ session });
  const result = runLocalSmokeMain(run.dependencies);
  await flushUntil(() =>
    run.timers.hasDelay(LOCAL_SMOKE_ENTIRE_TIMEOUT_MS)
  );

  run.timers.fireDelay(LOCAL_SMOKE_ENTIRE_TIMEOUT_MS);
  const exitCode = await result;

  assert.equal(exitCode, 1);
  assert.equal(session.emergencyCleanupCalls, 1);
  assert.equal(session.closeCalls, 1);
  assert.equal(run.timers.activeCount, 0);
  assert.equal(run.signals.listenerCount('SIGINT'), 0);
  assert.equal(run.signals.listenerCount('SIGTERM'), 0);
  assert.match(run.logs.join('\n'), /timed out/u);
  assert.equal(run.logs.join('\n').includes(SECRET_TOKEN), false);
});

test('an unexpected session failure aborts immediately with stable cleanup', async () => {
  const session = new FakeSession({ startNever: true });
  const run = scriptedRun({ session });
  const result = runLocalSmokeMain(run.dependencies);
  await flushUntil(() => session.startCalls === 1);

  session.failSession();
  const exitCode = await result;

  assert.equal(exitCode, 1);
  assert.equal(session.emergencyCleanupCalls, 1);
  assert.equal(session.closeCalls, 1);
  assert.equal(run.timers.activeCount, 0);
  assert.equal(run.signals.listenerCount('SIGINT'), 0);
  assert.equal(run.signals.listenerCount('SIGTERM'), 0);
  assert.match(run.logs.join('\n'), /failed safely/u);
  assert.equal(run.logs.join('\n').includes(SECRET_TOKEN), false);
});

test('controller close failure cannot print a premature pass result', async () => {
  const session = new FakeSession({ closeFails: true });
  const run = scriptedRun({ session });

  const exitCode = await runLocalSmokeMain(run.dependencies);

  assert.equal(exitCode, 1);
  assert.equal(session.shutdownCalls, 1);
  assert.equal(session.emergencyCleanupCalls, 1);
  assert.equal(session.closeCalls, 2);
  assert.doesNotMatch(run.logs.join('\n'), /LOCAL MINECRAFT SMOKE PASS/u);
  assert.match(run.logs.join('\n'), /LOCAL MINECRAFT SMOKE FAIL/u);
  assert.equal(run.logs.join('\n').includes('raw fake close failure'), false);
});

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  test(`${signal} interrupts the active smoke with code 130 and bounded cleanup`, async () => {
    const session = new FakeSession({ startNever: true });
    const run = scriptedRun({ session });
    const result = runLocalSmokeMain(run.dependencies);
    await flushUntil(() => session.startCalls === 1);

    run.signals.emit(signal);
    const exitCode = await result;

    assert.equal(exitCode, 130);
    assert.equal(session.emergencyCleanupCalls, 1);
    assert.equal(session.closeCalls, 1);
    assert.equal(run.timers.activeCount, 0);
    assert.equal(run.signals.listenerCount('SIGINT'), 0);
    assert.equal(run.signals.listenerCount('SIGTERM'), 0);
    assert.equal(run.logs.join('\n').includes(SECRET_TOKEN), false);
  });
}

test('emergency cleanup and SIGINT/SIGTERM disposal are idempotent', async () => {
  const session = new FakeSession();
  const signals = new FakeSignalTarget();
  let stopTimersCalls = 0;
  const cleanup = createLocalSmokeCleanup(
    () => session,
    () => {
      stopTimersCalls += 1;
    }
  );
  let interruptCalls = 0;
  const dispose = installLocalSmokeSignalHandlers(() => {
    interruptCalls += 1;
    return cleanup.runEmergency();
  }, signals.target());

  signals.emit('SIGINT');
  signals.emit('SIGTERM');
  await cleanup.runEmergency();
  await cleanup.runEmergency();
  await cleanup.closeNormally();

  assert.equal(interruptCalls, 2);
  assert.equal(cleanup.emergencyActive(), true);
  assert.equal(session.emergencyCleanupCalls, 1);
  assert.equal(session.closeCalls, 1);
  assert.equal(stopTimersCalls, 1);
  dispose();
  dispose();
  assert.equal(signals.listenerCount('SIGINT'), 0);
  assert.equal(signals.listenerCount('SIGTERM'), 0);
});
