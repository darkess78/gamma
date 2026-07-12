import { randomBytes, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import {
  createServer,
  type IncomingMessage,
  type Server as HttpServer
} from 'node:http';
import {
  createConnection,
  type Socket
} from 'node:net';
import { resolve } from 'node:path';
import { createInterface, type Interface as ReadlineInterface } from 'node:readline/promises';
import { pathToFileURL } from 'node:url';

import WebSocket, {
  WebSocketServer,
  type RawData
} from 'ws';

import {
  loadMinecraftSidecarRuntimeConfig,
  type MinecraftSidecarEnvironment,
  type MinecraftSidecarRuntimeConfig
} from './config.js';
import {
  MineflayerMinecraftAdapter
} from './mineflayer-runtime.js';
import type {
  MinecraftMovementAdapter,
  SafePosition
} from './minecraft-adapter.js';
import {
  parseProtocolMessage,
  type CommandName,
  type CompanionState,
  type FailureCode,
  type ProtocolMessage,
  type TerminalOutcome
} from './protocol.js';
import {
  MinecraftSidecarRuntime,
  type MinecraftSidecarRuntimeExit
} from './runtime.js';

export const LOCAL_SMOKE_OPT_IN = '1' as const;
export const LOCAL_SMOKE_ENTIRE_TIMEOUT_MS = 5 * 60 * 1_000;
export const LOCAL_SMOKE_SEGMENT_TIMEOUT_MS = 20 * 1_000;

const CONTROL_PATH = '/v1/minecraft/control';
const OWNER_DISTANCE_LIMIT = 32;
const REQUIRED_ENVIRONMENT = [
  'SHANA_MINECRAFT_SERVER_HOST',
  'SHANA_MINECRAFT_SERVER_PORT',
  'SHANA_MINECRAFT_VERSION',
  'SHANA_MINECRAFT_ACCOUNT_MODE',
  'SHANA_MINECRAFT_BOT_USERNAME',
  'SHANA_MINECRAFT_OWNER_USERNAME'
] as const;

type RequiredEnvironmentName = (typeof REQUIRED_ENVIRONMENT)[number];

export type LocalSmokeConfig = Readonly<{
  serverHost: string;
  serverPort: number;
  version: '1.21.11';
  accountMode: 'offline';
  botUsername: string;
  ownerUsername: string;
}>;

export type LocalSmokePromptId =
  | 'safety_confirmation'
  | 'owner_ready'
  | 'follow_ready'
  | 'follow_observed'
  | 'come_ready'
  | 'stop_follow_ready'
  | 'stop_follow_observed'
  | 'emergency_follow_ready'
  | 'emergency_follow_observed';

export type LocalSmokePromptRequest = Readonly<{
  id: LocalSmokePromptId;
  message: string;
  expected: 'confirm' | 'enter';
}>;

export type LocalSmokePrompt = (
  request: LocalSmokePromptRequest,
  signal: AbortSignal
) => Promise<string>;

export type LocalSmokeTimer = Readonly<{ unref?: () => void }>;

export type LocalSmokeTimers = Readonly<{
  setTimeout: (callback: () => void, milliseconds: number) => LocalSmokeTimer;
  clearTimeout: (timer: LocalSmokeTimer) => void;
}>;

export type LocalSmokeSignalTarget = Pick<NodeJS.Process, 'on' | 'off'>;

export type LocalSmokeTerminal = Readonly<{
  outcome: TerminalOutcome;
  failureCode: FailureCode | null;
}>;

export type LocalSmokeCommandObservation = {
  readonly name: CommandName;
  readonly commandId: string;
  readonly traceId: string;
  ackCount: number;
  accepted: boolean;
  failureCode: FailureCode | null;
  terminalCount: number;
  terminalOutcome: TerminalOutcome | null;
  terminalFailureCode: FailureCode | null;
};

export interface LocalSmokeSession {
  setFailureHandler(handler: (() => void) | undefined): void;
  start(config: LocalSmokeConfig, controlToken: string, signal: AbortSignal): Promise<void>;
  assertStartupDisconnected(): Promise<void>;
  assertMinecraftDisconnected(): Promise<void>;
  assertConnectedOwner(): Promise<void>;
  assertOwnerWithin(maximumDistance: number): Promise<void>;
  captureBotPosition(): SafePosition;
  assertBotMovedFrom(position: SafePosition, minimumDistance: number): Promise<void>;
  assertBotStayedNear(position: SafePosition, maximumDistance: number): Promise<void>;
  assertStationary(signal: AbortSignal): Promise<void>;
  sendCommand(
    name: CommandName,
    args: Readonly<Record<string, unknown>>,
    deadlineMs: number,
    signal: AbortSignal
  ): Promise<LocalSmokeCommandObservation>;
  waitForTerminal(
    observation: LocalSmokeCommandObservation,
    signal: AbortSignal
  ): Promise<LocalSmokeTerminal>;
  waitForState(state: CompanionState, signal: AbortSignal): Promise<void>;
  shutdownGamma(signal: AbortSignal): Promise<void>;
  emergencyCleanup(): Promise<void>;
  close(): Promise<void>;
  observations(): readonly LocalSmokeCommandObservation[];
  maximumObservedOwnerDistance(): number | null;
  emergencyStopConfirmed(): boolean;
}

export type LocalSmokeDependencies = Readonly<{
  environment?: MinecraftSidecarEnvironment;
  log?: (message: string) => void;
  prompt?: LocalSmokePrompt;
  checkPort?: (
    config: LocalSmokeConfig,
    signal: AbortSignal
  ) => Promise<boolean>;
  createToken?: () => string;
  createSession?: (now: () => Date) => LocalSmokeSession;
  now?: () => Date;
  timers?: LocalSmokeTimers;
  signalTarget?: LocalSmokeSignalTarget;
  isInteractive?: () => boolean;
}>;

export type LocalSmokeSummary = Readonly<{
  result: 'pass' | 'fail';
  startedAt: string;
  finishedAt: string;
  serverHost: string;
  serverPort: number;
  version: '1.21.11';
  botUsername: string;
  ownerUsername: string;
  commands: readonly Readonly<{
    name: CommandName;
    ackReceived: boolean;
    accepted: boolean;
    terminalOutcome: TerminalOutcome | null;
    failureCode: FailureCode | null;
  }>[];
  maximumObservedOwnerDistance: number | null;
  emergencyStopConfirmed: boolean;
  cleanLeaveConfirmed: boolean;
  cleanShutdownConfirmed: boolean;
}>;

export class LocalSmokeConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LocalSmokeConfigurationError';
  }
}

class LocalSmokeRunError extends Error {
  readonly category: 'abort' | 'failure' | 'interrupt' | 'timeout';

  constructor(category: LocalSmokeRunError['category'], message: string) {
    super(message);
    this.name = 'LocalSmokeRunError';
    this.category = category;
  }
}

export class LocalSmokeCleanup {
  readonly #getSession: () => LocalSmokeSession | undefined;
  readonly #stopTimers: () => void;
  #emergencyActive = false;
  #work: Promise<void> | undefined;

  constructor(
    getSession: () => LocalSmokeSession | undefined,
    stopTimers: () => void = () => undefined
  ) {
    this.#getSession = getSession;
    this.#stopTimers = stopTimers;
  }

  emergencyActive(): boolean {
    return this.#emergencyActive;
  }

  runEmergency(): Promise<void> {
    this.#emergencyActive = true;
    this.#work ??= this.#run();
    return this.#work;
  }

  async closeNormally(): Promise<void> {
    if (this.#work !== undefined) {
      await this.#work;
      return;
    }
    try {
      await this.#getSession()?.close();
    } finally {
      this.#stopTimers();
    }
  }

  async #run(): Promise<void> {
    const session = this.#getSession();
    try {
      await session?.emergencyCleanup();
    } catch {
      // Cleanup remains best-effort and never emits raw adapter errors.
    }
    this.#stopTimers();
    try {
      await session?.close();
    } catch {
      // The stable smoke result owns error reporting.
    }
  }
}

export function createLocalSmokeCleanup(
  getSession: () => LocalSmokeSession | undefined,
  stopTimers: () => void = () => undefined
): LocalSmokeCleanup {
  return new LocalSmokeCleanup(getSession, stopTimers);
}

export function installLocalSmokeSignalHandlers(
  requestInterrupt: () => void | Promise<void>,
  target: LocalSmokeSignalTarget = process
): () => void {
  let disposed = false;
  const handler = (): void => {
    try {
      void Promise.resolve(requestInterrupt()).catch(() => undefined);
    } catch {
      // Signal paths expose no raw error.
    }
  };
  target.on('SIGINT', handler);
  target.on('SIGTERM', handler);
  return () => {
    if (disposed) return;
    disposed = true;
    target.off('SIGINT', handler);
    target.off('SIGTERM', handler);
  };
}

export function loadLocalSmokeConfig(
  environment: MinecraftSidecarEnvironment
): LocalSmokeConfig {
  const explicit: Partial<Record<RequiredEnvironmentName, string>> = {};
  for (const name of REQUIRED_ENVIRONMENT) {
    const value = environment[name];
    if (value === undefined || value.length === 0) {
      throw new LocalSmokeConfigurationError(`${name} is required`);
    }
    explicit[name] = value;
  }

  let parsed: MinecraftSidecarRuntimeConfig;
  try {
    parsed = loadMinecraftSidecarRuntimeConfig({
      SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL:
        'ws://127.0.0.1:1/v1/minecraft/control',
      SHANA_MINECRAFT_CONTROL_TOKEN: 'local-smoke-validation-token',
      SHANA_MINECRAFT_SIDECAR_INSTANCE_ID: 'local-smoke-validation',
      SHANA_MINECRAFT_SERVER_HOST:
        explicit.SHANA_MINECRAFT_SERVER_HOST,
      SHANA_MINECRAFT_SERVER_PORT:
        explicit.SHANA_MINECRAFT_SERVER_PORT,
      SHANA_MINECRAFT_VERSION: explicit.SHANA_MINECRAFT_VERSION,
      SHANA_MINECRAFT_ACCOUNT_MODE:
        explicit.SHANA_MINECRAFT_ACCOUNT_MODE,
      SHANA_MINECRAFT_BOT_USERNAME:
        explicit.SHANA_MINECRAFT_BOT_USERNAME,
      SHANA_MINECRAFT_OWNER_USERNAME:
        explicit.SHANA_MINECRAFT_OWNER_USERNAME
    });
  } catch {
    throw new LocalSmokeConfigurationError('Local smoke configuration is invalid');
  }
  if (parsed.minecraftOwnerUsername === null) {
    throw new LocalSmokeConfigurationError('Minecraft owner username is required');
  }
  if (
    parsed.minecraftBotUsername.toLowerCase() ===
    parsed.minecraftOwnerUsername.toLowerCase()
  ) {
    throw new LocalSmokeConfigurationError(
      'Minecraft bot and owner usernames must differ'
    );
  }
  if ([8_000, 8_001].includes(parsed.minecraftServerPort)) {
    throw new LocalSmokeConfigurationError(
      'Shana and Dashboard ports cannot be used as the Minecraft endpoint'
    );
  }
  return Object.freeze({
    serverHost: parsed.minecraftServerHost,
    serverPort: parsed.minecraftServerPort,
    version: parsed.minecraftVersion,
    accountMode: parsed.minecraftAccountMode,
    botUsername: parsed.minecraftBotUsername,
    ownerUsername: parsed.minecraftOwnerUsername
  });
}

export function checkLocalMinecraftPort(
  config: LocalSmokeConfig,
  signal: AbortSignal
): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  return new Promise<boolean>((resolvePromise) => {
    let settled = false;
    let socket: Socket | undefined;
    const finish = (listening: boolean): void => {
      if (settled) return;
      settled = true;
      signal.removeEventListener('abort', onAbort);
      socket?.on('error', () => undefined);
      socket?.destroy();
      resolvePromise(listening);
    };
    const onAbort = (): void => finish(false);
    signal.addEventListener('abort', onAbort, { once: true });
    try {
      socket = createConnection({
        host: config.serverHost,
        port: config.serverPort
      });
      socket.setTimeout(1_000);
      socket.once('connect', () => finish(true));
      socket.once('timeout', () => finish(false));
      socket.once('error', () => finish(false));
    } catch {
      finish(false);
    }
    if (signal.aborted) onAbort();
  });
}

export async function runLocalSmokeMain(
  dependencies: LocalSmokeDependencies = {}
): Promise<number> {
  const environment = dependencies.environment ?? process.env;
  const log = dependencies.log ?? ((message: string) => console.log(message));
  if (
    environment.SHANA_MINECRAFT_RUN_LOCAL_SMOKE !== LOCAL_SMOKE_OPT_IN
  ) {
    log('Local Minecraft smoke not started: explicit opt-in is required.');
    return 2;
  }

  let config: LocalSmokeConfig;
  try {
    config = loadLocalSmokeConfig(environment);
  } catch {
    log('Local Minecraft smoke not started: configuration is invalid.');
    return 2;
  }
  const interactive = dependencies.isInteractive ?? (() =>
    process.stdin.isTTY === true && process.stdout.isTTY === true
  );
  if (!interactive()) {
    log('Local Minecraft smoke not started: an interactive terminal is required.');
    return 2;
  }

  const now = dependencies.now ?? (() => new Date());
  const timers = dependencies.timers ?? defaultTimers();
  const stopController = new AbortController();
  let stopReject!: (error: LocalSmokeRunError) => void;
  const stop = new Promise<never>((_resolve, reject) => {
    stopReject = reject;
  });
  let stopped = false;
  const requestStop = (error: LocalSmokeRunError): void => {
    if (stopped) return;
    stopped = true;
    stopController.abort(error);
    stopReject(error);
  };

  let session: LocalSmokeSession | undefined;
  let entireTimer: LocalSmokeTimer | undefined;
  let readline: ReadlineInterface | undefined;
  const stopTimers = (): void => {
    if (entireTimer !== undefined) {
      timers.clearTimeout(entireTimer);
      entireTimer = undefined;
    }
  };
  const cleanup = createLocalSmokeCleanup(() => session, stopTimers);
  const requestEmergencyStop = (error: LocalSmokeRunError): void => {
    if (stopped) return;
    void cleanup.runEmergency();
    requestStop(error);
  };
  const disposeSignals = installLocalSmokeSignalHandlers(() => {
    requestEmergencyStop(
      new LocalSmokeRunError('interrupt', 'Interrupted by signal.')
    );
    return cleanup.runEmergency();
  }, dependencies.signalTarget ?? process);

  const prompt = dependencies.prompt ?? ((request, signal) => {
    readline ??= createInterface({ input: process.stdin, output: process.stdout });
    return readline.question(`${request.message}\n> `, { signal });
  });

  let startedAt: Date | undefined;
  let emergencyStopWasConfirmed = false;
  let cleanLeaveConfirmed = false;
  let cleanShutdownConfirmed = false;
  try {
    const portListening = await Promise.race([
      (dependencies.checkPort ?? checkLocalMinecraftPort)(
        config,
        stopController.signal
      ),
      stop
    ]);
    throwIfStopped(stopController.signal);
    if (!portListening) {
      throw new LocalSmokeRunError(
        'failure',
        'Configured loopback Minecraft port is not listening.'
      );
    }

    log(localSafetyChecklist(config));
    await ask(prompt, {
      id: 'safety_confirmation',
      expected: 'confirm',
      message: 'Type confirm to accept this bounded smoke, or abort to stop.'
    }, stopController.signal, stop);

    startedAt = safeNow(now);
    entireTimer = timers.setTimeout(() => {
      requestEmergencyStop(
        new LocalSmokeRunError('timeout', 'Entire local smoke timed out.')
      );
    }, LOCAL_SMOKE_ENTIRE_TIMEOUT_MS);

    const token = dependencies.createToken?.() ?? randomBytes(32).toString('base64url');
    if (!usableGeneratedToken(token)) {
      throw new LocalSmokeRunError('failure', 'Control token generation failed.');
    }
    session = dependencies.createSession?.(now) ?? new RealLocalSmokeSession(now);
    session.setFailureHandler(() => {
      requestEmergencyStop(
        new LocalSmokeRunError('failure', 'Local smoke session failed safely.')
      );
    });
    await Promise.race([
      session.start(config, token, stopController.signal),
      stop
    ]);
    await session.assertStartupDisconnected();

    await ask(prompt, {
      id: 'owner_ready',
      expected: 'enter',
      message: 'Confirm the owner is logged in on safe flat Overworld terrain, then press Enter.'
    }, stopController.signal, stop);

    await completedCommand(session, 'join', {}, 45_000, stopController.signal);
    await session.waitForState('IDLE', stopController.signal);
    await session.assertConnectedOwner();

    await askMovement(prompt, 'follow_ready',
      'Stand 5-10 blocks from the bot on clear flat terrain, then press Enter to begin follow.',
      stopController.signal, stop);
    const followStart = session.captureBotPosition();
    const follow = await acceptedCommand(
      session,
      'follow_owner',
      { follow_distance: 3, lease_duration_seconds: 20 },
      LOCAL_SMOKE_SEGMENT_TIMEOUT_MS,
      stopController.signal
    );
    await session.waitForState('FOLLOWING', stopController.signal);
    await askMovement(prompt, 'follow_observed',
      'Walk slowly forward on clear flat terrain; press Enter after observing safe movement.',
      stopController.signal, stop);
    await session.assertBotMovedFrom(followStart, 0.25);
    const waiting = await acceptedCommand(
      session,
      'wait_here',
      { reason: 'Local smoke wait checkpoint.' },
      2_000,
      stopController.signal
    );
    await expectTerminal(session, follow, 'cancelled', stopController.signal);
    await expectTerminal(session, waiting, 'completed', stopController.signal);
    await session.waitForState('WAITING', stopController.signal);
    await session.assertStationary(stopController.signal);

    await askMovement(prompt, 'come_ready',
      'Stand within 10 blocks on clear flat terrain, then press Enter to begin come.',
      stopController.signal, stop);
    await completedCommand(
      session,
      'come_here',
      { arrival_distance: 3 },
      LOCAL_SMOKE_SEGMENT_TIMEOUT_MS,
      stopController.signal
    );
    await session.waitForState('IDLE', stopController.signal);
    await session.assertOwnerWithin(3);
    await session.assertStationary(stopController.signal);
    const lookPosition = session.captureBotPosition();
    await completedCommand(
      session,
      'look_at_owner',
      { duration_seconds: 2 },
      10_000,
      stopController.signal
    );
    await session.assertBotStayedNear(lookPosition, 0.125);

    await askMovement(prompt, 'stop_follow_ready',
      'Return 5-10 blocks away on clear flat terrain, then press Enter to start the stop test.',
      stopController.signal, stop);
    const stopFollowStart = session.captureBotPosition();
    const stopFollow = await acceptedCommand(
      session,
      'follow_owner',
      { follow_distance: 3, lease_duration_seconds: 20 },
      LOCAL_SMOKE_SEGMENT_TIMEOUT_MS,
      stopController.signal
    );
    await session.waitForState('FOLLOWING', stopController.signal);
    await askMovement(prompt, 'stop_follow_observed',
      'Press Enter after the bot begins safe forward movement; stop will be sent immediately.',
      stopController.signal, stop);
    await session.assertBotMovedFrom(stopFollowStart, 0.25);
    const stopCommand = await acceptedCommand(
      session,
      'stop',
      { reason: 'Local smoke stop preemption.' },
      2_000,
      stopController.signal
    );
    await expectTerminal(session, stopFollow, 'cancelled', stopController.signal);
    await expectTerminal(session, stopCommand, 'completed', stopController.signal);
    await session.waitForState('IDLE', stopController.signal);
    await session.assertStationary(stopController.signal);

    await askMovement(prompt, 'emergency_follow_ready',
      'Return 5-10 blocks away on safe terrain, then press Enter to start the emergency test.',
      stopController.signal, stop);
    const emergencyFollowStart = session.captureBotPosition();
    const emergencyFollow = await acceptedCommand(
      session,
      'follow_owner',
      { follow_distance: 3, lease_duration_seconds: 20 },
      LOCAL_SMOKE_SEGMENT_TIMEOUT_MS,
      stopController.signal
    );
    await session.waitForState('FOLLOWING', stopController.signal);
    await askMovement(prompt, 'emergency_follow_observed',
      'Press Enter after safe movement begins; emergency stop will be sent immediately.',
      stopController.signal, stop);
    await session.assertBotMovedFrom(emergencyFollowStart, 0.25);
    const emergency = await acceptedCommand(
      session,
      'emergency_stop',
      { reason: 'Local smoke emergency checkpoint.' },
      2_000,
      stopController.signal
    );
    await expectTerminal(
      session,
      emergencyFollow,
      'cancelled',
      stopController.signal
    );
    await expectTerminal(session, emergency, 'completed', stopController.signal);
    await session.waitForState('STOPPED', stopController.signal);
    if (!session.emergencyStopConfirmed()) {
      throw new LocalSmokeRunError('failure', 'Emergency latch was not confirmed.');
    }
    emergencyStopWasConfirmed = true;
    await session.assertStationary(stopController.signal);
    const rejected = await session.sendCommand(
      'follow_owner',
      { follow_distance: 3, lease_duration_seconds: 5 },
      5_000,
      stopController.signal
    );
    if (
      rejected.accepted ||
      rejected.failureCode !== 'EMERGENCY_STOP_ACTIVE'
    ) {
      throw new LocalSmokeRunError(
        'failure',
        'Movement was not rejected while emergency stop was active.'
      );
    }

    await completedCommand(session, 'leave', {}, 15_000, stopController.signal);
    await session.waitForState('STOPPED', stopController.signal);
    await session.assertMinecraftDisconnected();
    cleanLeaveConfirmed = true;
    await completedCommand(session, 'join', {}, 45_000, stopController.signal);
    await session.waitForState('IDLE', stopController.signal);
    await session.assertConnectedOwner();
    if (session.emergencyStopConfirmed()) {
      throw new LocalSmokeRunError('failure', 'Emergency recovery was not confirmed.');
    }
    await completedCommand(session, 'leave', {}, 15_000, stopController.signal);
    await session.waitForState('DISCONNECTED', stopController.signal);
    await session.assertMinecraftDisconnected();
    cleanLeaveConfirmed = true;
    await session.shutdownGamma(stopController.signal);
    await cleanup.closeNormally();
    cleanShutdownConfirmed = true;
    throwIfStopped(stopController.signal);
    validateObservations(session.observations());
    const summary = summaryFor(
      'pass',
      startedAt,
      safeNow(now),
      config,
      session,
      emergencyStopWasConfirmed,
      cleanLeaveConfirmed,
      cleanShutdownConfirmed
    );
    log(`LOCAL MINECRAFT SMOKE PASS\n${JSON.stringify(summary)}`);
    return 0;
  } catch (error: unknown) {
    const runError = normalizeRunError(error);
    await cleanup.runEmergency();
    if (startedAt !== undefined && session !== undefined) {
      const summary = summaryFor(
        'fail',
        startedAt,
        safeNow(now),
        config,
        session,
        emergencyStopWasConfirmed,
        cleanLeaveConfirmed,
        cleanShutdownConfirmed
      );
      log(`LOCAL MINECRAFT SMOKE FAIL: ${runError.message}\n${JSON.stringify(summary)}`);
    } else {
      log(`Local Minecraft smoke not started: ${runError.message}`);
    }
    return runError.category === 'interrupt' ? 130 : 1;
  } finally {
    stopped = true;
    stopTimers();
    disposeSignals();
    readline?.close();
  }
}

async function ask(
  prompt: LocalSmokePrompt,
  request: LocalSmokePromptRequest,
  signal: AbortSignal,
  stop: Promise<never>
): Promise<void> {
  let answer: string;
  try {
    answer = await Promise.race([prompt(request, signal), stop]);
  } catch (error: unknown) {
    if (error instanceof LocalSmokeRunError) throw error;
    throwIfStopped(signal);
    throw new LocalSmokeRunError('abort', 'Interactive confirmation aborted.');
  }
  if (answer === 'abort') {
    throw new LocalSmokeRunError('abort', 'User requested safe abort.');
  }
  if (
    (request.expected === 'confirm' && answer !== 'confirm') ||
    (request.expected === 'enter' && answer !== '')
  ) {
    throw new LocalSmokeRunError('abort', 'Unexpected interactive input.');
  }
}

function askMovement(
  prompt: LocalSmokePrompt,
  id: Extract<LocalSmokePromptId,
    | 'follow_ready'
    | 'follow_observed'
    | 'come_ready'
    | 'stop_follow_ready'
    | 'stop_follow_observed'
    | 'emergency_follow_ready'
    | 'emergency_follow_observed'>,
  message: string,
  signal: AbortSignal,
  stop: Promise<never>
): Promise<void> {
  return ask(prompt, { id, message, expected: 'enter' }, signal, stop);
}

async function acceptedCommand(
  session: LocalSmokeSession,
  name: CommandName,
  args: Readonly<Record<string, unknown>>,
  deadlineMs: number,
  signal: AbortSignal
): Promise<LocalSmokeCommandObservation> {
  const observation = await session.sendCommand(name, args, deadlineMs, signal);
  if (!observation.accepted || observation.ackCount !== 1) {
    throw new LocalSmokeRunError(
      'failure',
      `${name} was not accepted exactly once.`
    );
  }
  return observation;
}

async function completedCommand(
  session: LocalSmokeSession,
  name: CommandName,
  args: Readonly<Record<string, unknown>>,
  deadlineMs: number,
  signal: AbortSignal
): Promise<void> {
  const observation = await acceptedCommand(
    session,
    name,
    args,
    deadlineMs,
    signal
  );
  await expectTerminal(session, observation, 'completed', signal);
}

async function expectTerminal(
  session: LocalSmokeSession,
  observation: LocalSmokeCommandObservation,
  expected: TerminalOutcome,
  signal: AbortSignal
): Promise<void> {
  const terminal = await session.waitForTerminal(observation, signal);
  if (
    terminal.outcome !== expected ||
    observation.terminalCount !== 1
  ) {
    throw new LocalSmokeRunError(
      'failure',
      `${observation.name} did not produce the expected terminal result.`
    );
  }
}

function validateObservations(
  observations: readonly LocalSmokeCommandObservation[]
): void {
  for (const observation of observations) {
    if (observation.ackCount !== 1) {
      throw new LocalSmokeRunError('failure', 'Command acknowledgment count was invalid.');
    }
    if (observation.accepted && observation.terminalCount !== 1) {
      throw new LocalSmokeRunError('failure', 'Accepted command terminal count was invalid.');
    }
    if (!observation.accepted && observation.terminalCount !== 0) {
      throw new LocalSmokeRunError('failure', 'Rejected command produced a terminal result.');
    }
  }
}

function summaryFor(
  result: LocalSmokeSummary['result'],
  startedAt: Date,
  finishedAt: Date,
  config: LocalSmokeConfig,
  session: LocalSmokeSession,
  emergencyStopWasConfirmed: boolean,
  cleanLeaveConfirmed: boolean,
  cleanShutdownConfirmed: boolean
): LocalSmokeSummary {
  return Object.freeze({
    result,
    startedAt: startedAt.toISOString(),
    finishedAt: finishedAt.toISOString(),
    serverHost: config.serverHost,
    serverPort: config.serverPort,
    version: config.version,
    botUsername: config.botUsername,
    ownerUsername: config.ownerUsername,
    commands: Object.freeze(session.observations().map((observation) =>
      Object.freeze({
        name: observation.name,
        ackReceived: observation.ackCount === 1,
        accepted: observation.accepted,
        terminalOutcome: observation.terminalOutcome,
        failureCode:
          observation.failureCode ?? observation.terminalFailureCode
      })
    )),
    maximumObservedOwnerDistance: session.maximumObservedOwnerDistance(),
    emergencyStopConfirmed: emergencyStopWasConfirmed,
    cleanLeaveConfirmed,
    cleanShutdownConfirmed
  });
}

function localSafetyChecklist(config: LocalSmokeConfig): string {
  return [
    'LOCAL MINECRAFT SMOKE SAFETY CHECKLIST',
    '- The already-running server is private and disposable.',
    '- It is Minecraft Java 1.21.11.',
    '- Offline mode is intentional for this test.',
    '- The human owner is already logged in.',
    '- Owner and bot will remain in the Overworld.',
    '- Nearby terrain is flat, clear, loaded, and hazard-free.',
    '- You accept bounded forward walking during this test.',
    `- Endpoint: ${config.serverHost}:${config.serverPort} (open port only; version not inferred).`
  ].join('\n');
}

function usableGeneratedToken(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length >= 32 &&
    value.length <= 256 &&
    /^[A-Za-z0-9_-]+$/u.test(value)
  );
}

function safeNow(now: () => Date): Date {
  const value = now();
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
    throw new LocalSmokeRunError('failure', 'Local smoke clock is invalid.');
  }
  return value;
}

function normalizeRunError(error: unknown): LocalSmokeRunError {
  if (error instanceof LocalSmokeRunError) return error;
  return new LocalSmokeRunError('failure', 'Local smoke failed safely.');
}

function defaultTimers(): LocalSmokeTimers {
  return Object.freeze({
    setTimeout: (callback: () => void, milliseconds: number) =>
      setTimeout(callback, milliseconds),
    clearTimeout: (timer: LocalSmokeTimer) =>
      clearTimeout(timer as NodeJS.Timeout)
  });
}

type FramePredicate = (message: ProtocolMessage) => boolean;

type FrameWaiter = {
  predicate: FramePredicate;
  resolve: (message: ProtocolMessage) => void;
  reject: (error: Error) => void;
  signal: AbortSignal;
  onAbort: () => void;
};

class RealLocalSmokeSession implements LocalSmokeSession {
  readonly #now: () => Date;
  readonly #adapter: MinecraftMovementAdapter;
  readonly #frames: ProtocolMessage[] = [];
  readonly #waiters = new Set<FrameWaiter>();
  readonly #observations: LocalSmokeCommandObservation[] = [];

  #server: WebSocketServer | undefined;
  #httpServer: HttpServer | undefined;
  readonly #controllerSockets = new Set<Socket>();
  #socket: WebSocket | undefined;
  #runtime: MinecraftSidecarRuntime | undefined;
  #runtimeRun: Promise<MinecraftSidecarRuntimeExit> | undefined;
  #connectionId = `smoke-${randomUUID()}`;
  #gammaSequence = 0;
  #config: LocalSmokeConfig | undefined;
  #maximumOwnerDistance: number | null = null;
  #emergencyConfirmed = false;
  #failureHandler: (() => void) | undefined;
  #intentionalStop = false;
  #controllerClosing = false;
  #closed = false;

  constructor(now: () => Date) {
    this.#now = now;
    this.#adapter = new MineflayerMinecraftAdapter();
  }

  setFailureHandler(handler: (() => void) | undefined): void {
    this.#failureHandler = handler;
  }

  async start(
    config: LocalSmokeConfig,
    controlToken: string,
    signal: AbortSignal
  ): Promise<void> {
    if (signal.aborted) throw new LocalSmokeRunError('abort', 'Smoke start aborted.');
    this.#config = config;
    const httpServer = createServer();
    this.#httpServer = httpServer;
    httpServer.on('connection', (socket) => {
      socket.on('error', () => undefined);
      if (this.#controllerClosing) {
        socket.destroy();
        return;
      }
      this.#controllerSockets.add(socket);
      socket.once('close', () => {
        this.#controllerSockets.delete(socket);
      });
    });
    httpServer.on('error', () => {
      this.#failSession('Temporary controller transport failed.');
    });
    httpServer.on('clientError', (_error, socket) => {
      socket.on('error', () => undefined);
      socket.destroy();
    });
    const server = new WebSocketServer({
      server: httpServer,
      path: CONTROL_PATH,
      perMessageDeflate: false,
      maxPayload: 65_536
    });
    this.#server = server;
    server.on('error', () => {
      this.#failSession('Temporary controller failed.');
    });
    httpServer.listen({ host: '127.0.0.1', port: 0 });
    await abortableEvent(httpServer, 'listening', signal);
    throwIfStopped(signal);
    const address = httpServer.address();
    if (address === null || typeof address === 'string') {
      throw new LocalSmokeRunError('failure', 'Temporary controller address was invalid.');
    }
    if ([8_000, 8_001].includes(address.port)) {
      this.#controllerClosing = true;
      await closeController(
        server,
        httpServer,
        this.#controllerSockets
      );
      this.#server = undefined;
      this.#httpServer = undefined;
      throw new LocalSmokeRunError(
        'failure',
        'Temporary controller received a reserved local service port.'
      );
    }
    const connection = this.#nextConnection(controlToken, signal);
    const runtimeConfig: MinecraftSidecarRuntimeConfig = Object.freeze({
      controlWebSocketUrl:
        `ws://127.0.0.1:${address.port}${CONTROL_PATH}`,
      controlToken,
      sidecarInstanceId: `local-smoke-${randomUUID()}`,
      heartbeatSeconds: 5,
      minecraftServerHost: config.serverHost,
      minecraftServerPort: config.serverPort,
      minecraftVersion: config.version,
      minecraftAccountMode: config.accountMode,
      minecraftBotUsername: config.botUsername,
      minecraftOwnerUsername: config.ownerUsername
    });
    this.#runtime = new MinecraftSidecarRuntime(runtimeConfig, {
      now: this.#now,
      createMinecraftAdapter: () => this.#adapter
    });
    this.#runtimeRun = this.#runtime.run();
    void this.#runtimeRun.then(
      () => this.#failSession('Sidecar runtime exited unexpectedly.'),
      () => this.#failSession('Sidecar runtime failed unexpectedly.')
    );
    const socket = await connection;
    this.#socket = socket;
    const hello = await this.#waitFor((message) => message.type === 'hello', signal);
    if (
      hello.type !== 'hello' ||
      hello.payload.minecraft_library_version !== '4.37.1' ||
      hello.payload.pathfinder_version !== 'not-installed'
    ) {
      throw new LocalSmokeRunError('failure', 'Sidecar hello was invalid.');
    }
    throwIfStopped(signal);
    this.#send({
      protocol: 'gamma.minecraft',
      version: 1,
      type: 'welcome',
      message_id: `smoke-welcome-${randomUUID()}`,
      connection_id: this.#connectionId,
      sent_at: safeNow(this.#now).toISOString(),
      sequence: this.#gammaSequence++,
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
    await Promise.all([
      this.#waitFor((message) => message.type === 'sidecar_status', signal),
      this.#waitFor((message) => message.type === 'minecraft_status', signal),
      this.#waitFor((message) => message.type === 'state_snapshot', signal)
    ]);
    throwIfStopped(signal);
  }

  async assertStartupDisconnected(): Promise<void> {
    const runtime = this.#runtime;
    const minecraftStatus = [...this.#frames].reverse().find(
      (message) => message.type === 'minecraft_status'
    );
    const stateSnapshot = [...this.#frames].reverse().find(
      (message) => message.type === 'state_snapshot'
    );
    if (
      runtime === undefined ||
      runtime.status().minecraftConnectionState !== 'disconnected' ||
      runtime.status().companionState !== 'DISCONNECTED' ||
      this.#adapter.state().connectionState !== 'disconnected' ||
      minecraftStatus?.type !== 'minecraft_status' ||
      minecraftStatus.payload.connection_state !== 'disconnected' ||
      minecraftStatus.payload.negotiated_version != null ||
      minecraftStatus.payload.dimension != null ||
      stateSnapshot?.type !== 'state_snapshot' ||
      stateSnapshot.payload.minecraft_connection_state !== 'disconnected' ||
      stateSnapshot.payload.companion_state !== 'DISCONNECTED' ||
      stateSnapshot.payload.dimension != null
    ) {
      throw new LocalSmokeRunError('failure', 'Startup state was not disconnected.');
    }
  }

  async assertMinecraftDisconnected(): Promise<void> {
    const runtimeState = this.#runtime?.status();
    const minecraftStatus = [...this.#frames].reverse().find(
      (message) => message.type === 'minecraft_status'
    );
    const stateSnapshot = [...this.#frames].reverse().find(
      (message) => message.type === 'state_snapshot'
    );
    if (
      runtimeState?.minecraftConnectionState !== 'disconnected' ||
      this.#adapter.state().connectionState !== 'disconnected' ||
      minecraftStatus?.type !== 'minecraft_status' ||
      minecraftStatus.payload.connection_state !== 'disconnected' ||
      minecraftStatus.payload.negotiated_version != null ||
      minecraftStatus.payload.dimension != null ||
      stateSnapshot?.type !== 'state_snapshot' ||
      stateSnapshot.payload.minecraft_connection_state !== 'disconnected' ||
      stateSnapshot.payload.companion_state !== runtimeState.companionState ||
      stateSnapshot.payload.dimension != null
    ) {
      throw new LocalSmokeRunError('failure', 'Minecraft disconnect was not confirmed.');
    }
  }

  async assertConnectedOwner(): Promise<void> {
    const config = this.#requireConfig();
    const state = this.#adapter.state();
    const owner = this.#adapter.getPlayer(config.ownerUsername);
    const bot = this.#adapter.getBotPosition();
    const minecraftStatus = [...this.#frames].reverse().find(
      (message) => message.type === 'minecraft_status'
    );
    const stateSnapshot = [...this.#frames].reverse().find(
      (message) => message.type === 'state_snapshot'
    );
    if (
      this.#runtime?.status().minecraftConnectionState !== 'connected' ||
      state.connectionState !== 'connected' ||
      !state.spawned ||
      !state.alive ||
      state.negotiatedVersion !== config.version ||
      state.dimension !== 'minecraft:overworld' ||
      owner === undefined ||
      owner.dimension !== 'minecraft:overworld' ||
      bot === undefined ||
      minecraftStatus?.type !== 'minecraft_status' ||
      minecraftStatus.payload.connection_state !== 'connected' ||
      minecraftStatus.payload.negotiated_version !== config.version ||
      minecraftStatus.payload.dimension !== 'minecraft:overworld' ||
      stateSnapshot?.type !== 'state_snapshot' ||
      stateSnapshot.payload.minecraft_connection_state !== 'connected' ||
      !stateSnapshot.payload.owner_present ||
      stateSnapshot.payload.dimension !== 'minecraft:overworld' ||
      stateSnapshot.payload.rounded_position == null
    ) {
      throw new LocalSmokeRunError('failure', 'Connected owner state was not confirmed.');
    }
    const distance = positionDistance(bot, owner.position);
    if (!Number.isFinite(distance)) {
      throw new LocalSmokeRunError('failure', 'Owner distance exceeded the safe limit.');
    }
    this.#recordOwnerDistance(distance);
    if (distance > OWNER_DISTANCE_LIMIT) {
      throw new LocalSmokeRunError('failure', 'Owner distance exceeded the safe limit.');
    }
  }

  async assertOwnerWithin(maximumDistance: number): Promise<void> {
    if (
      !Number.isFinite(maximumDistance) ||
      maximumDistance <= 0 ||
      maximumDistance > OWNER_DISTANCE_LIMIT
    ) {
      throw new LocalSmokeRunError('failure', 'Owner-distance assertion was invalid.');
    }
    const config = this.#requireConfig();
    const bot = this.#requireBotPosition();
    const owner = this.#adapter.getPlayer(config.ownerUsername);
    if (owner === undefined || owner.dimension !== 'minecraft:overworld') {
      throw new LocalSmokeRunError('failure', 'Owner was unavailable after movement.');
    }
    const distance = positionDistance(bot, owner.position);
    if (!Number.isFinite(distance)) {
      throw new LocalSmokeRunError('failure', 'Owner arrival distance was not confirmed.');
    }
    this.#recordOwnerDistance(distance);
    if (distance > maximumDistance) {
      throw new LocalSmokeRunError('failure', 'Owner arrival distance was not confirmed.');
    }
  }

  captureBotPosition(): SafePosition {
    const position = this.#requireBotPosition();
    return Object.freeze({ x: position.x, y: position.y, z: position.z });
  }

  async assertBotMovedFrom(
    position: SafePosition,
    minimumDistance: number
  ): Promise<void> {
    if (
      !finitePosition(position) ||
      !Number.isFinite(minimumDistance) ||
      minimumDistance <= 0 ||
      minimumDistance > 4
    ) {
      throw new LocalSmokeRunError('failure', 'Movement assertion was invalid.');
    }
    const current = this.#requireBotPosition();
    if (positionDistance(position, current) < minimumDistance) {
      throw new LocalSmokeRunError('failure', 'Expected safe bot movement was not observed.');
    }
  }

  async assertBotStayedNear(
    position: SafePosition,
    maximumDistance: number
  ): Promise<void> {
    if (
      !finitePosition(position) ||
      !Number.isFinite(maximumDistance) ||
      maximumDistance < 0 ||
      maximumDistance > 1
    ) {
      throw new LocalSmokeRunError('failure', 'Stationary assertion was invalid.');
    }
    const current = this.#requireBotPosition();
    if (positionDistance(position, current) > maximumDistance) {
      throw new LocalSmokeRunError('failure', 'Unexpected bot movement was observed.');
    }
  }

  async assertStationary(signal: AbortSignal): Promise<void> {
    throwIfStopped(signal);
    await abortableDelay(250, signal);
    const settled = this.captureBotPosition();
    await abortableDelay(500, signal);
    await this.assertBotStayedNear(settled, 0.125);
    throwIfStopped(signal);
  }

  async sendCommand(
    name: CommandName,
    args: Readonly<Record<string, unknown>>,
    deadlineMs: number,
    signal: AbortSignal
  ): Promise<LocalSmokeCommandObservation> {
    throwIfStopped(signal);
    if (!Number.isFinite(deadlineMs) || deadlineMs <= 0 || deadlineMs > 60_000) {
      throw new LocalSmokeRunError('failure', 'Command deadline was invalid.');
    }
    const commandId = `smoke-command-${randomUUID()}`;
    const traceId = `smoke-trace-${randomUUID()}`;
    const sentAt = safeNow(this.#now);
    const observation: LocalSmokeCommandObservation = {
      name,
      commandId,
      traceId,
      ackCount: 0,
      accepted: false,
      failureCode: null,
      terminalCount: 0,
      terminalOutcome: null,
      terminalFailureCode: null
    };
    this.#observations.push(observation);
    throwIfStopped(signal);
    this.#send({
      protocol: 'gamma.minecraft',
      version: 1,
      type: 'command',
      message_id: `smoke-message-${randomUUID()}`,
      connection_id: this.#connectionId,
      sent_at: sentAt.toISOString(),
      sequence: this.#gammaSequence++,
      trace_id: traceId,
      command_id: commandId,
      payload: {
        name,
        deadline_at: new Date(sentAt.getTime() + deadlineMs).toISOString(),
        arguments: args
      }
    });
    const ack = await this.#waitFor(
      (message) => message.type === 'command_ack' && message.command_id === commandId,
      signal
    );
    if (ack.type !== 'command_ack') {
      throw new LocalSmokeRunError('failure', 'Command acknowledgment was invalid.');
    }
    observation.ackCount = this.#count(commandId, 'command_ack');
    observation.accepted = ack.payload.accepted;
    observation.failureCode = ack.payload.failure?.code ?? null;
    await this.#sampleOwnerDistance();
    throwIfStopped(signal);
    return observation;
  }

  async waitForTerminal(
    observation: LocalSmokeCommandObservation,
    signal: AbortSignal
  ): Promise<LocalSmokeTerminal> {
    throwIfStopped(signal);
    const result = await this.#waitFor(
      (message) =>
        message.type === 'terminal_result' &&
        message.command_id === observation.commandId,
      signal
    );
    if (result.type !== 'terminal_result') {
      throw new LocalSmokeRunError('failure', 'Terminal result was invalid.');
    }
    observation.terminalCount = this.#count(
      observation.commandId,
      'terminal_result'
    );
    observation.terminalOutcome = result.payload.outcome;
    observation.terminalFailureCode = result.payload.failure?.code ?? null;
    await this.#sampleOwnerDistance();
    throwIfStopped(signal);
    return Object.freeze({
      outcome: result.payload.outcome,
      failureCode: result.payload.failure?.code ?? null
    });
  }

  async waitForState(state: CompanionState, signal: AbortSignal): Promise<void> {
    throwIfStopped(signal);
    const firstNewFrame = this.#frames.length;
    const currentState = this.#runtime?.status().companionState;
    const latestSnapshot = [...this.#frames].reverse().find(
      (message) => message.type === 'state_snapshot'
    );
    if (
      currentState !== state ||
      latestSnapshot?.type !== 'state_snapshot' ||
      latestSnapshot.payload.companion_state !== state
    ) {
      await this.#waitFor(
        (message) =>
          message.type === 'state_snapshot' &&
          message.payload.companion_state === state,
        signal,
        firstNewFrame
      );
    }
    if (this.#runtime?.status().companionState !== state) {
      throw new LocalSmokeRunError('failure', 'Companion state did not stabilize.');
    }
    this.#emergencyConfirmed = this.#runtime?.status().emergencyStopActive ?? false;
    await this.#sampleOwnerDistance();
    throwIfStopped(signal);
  }

  async shutdownGamma(signal: AbortSignal): Promise<void> {
    throwIfStopped(signal);
    this.#intentionalStop = true;
    this.#send({
      protocol: 'gamma.minecraft',
      version: 1,
      type: 'shutdown',
      message_id: `smoke-shutdown-${randomUUID()}`,
      connection_id: this.#connectionId,
      sent_at: safeNow(this.#now).toISOString(),
      sequence: this.#gammaSequence++,
      trace_id: `smoke-shutdown-trace-${randomUUID()}`,
      payload: {
        reason: 'Local smoke completed.',
        leave_minecraft: true
      }
    });
    const run = this.#runtimeRun;
    if (run === undefined) {
      throw new LocalSmokeRunError('failure', 'Sidecar runtime was not started.');
    }
    const result = await abortable(run, signal);
    if (result.category !== 'gamma_shutdown') {
      throw new LocalSmokeRunError('failure', 'Sidecar shutdown was not clean.');
    }
    throwIfStopped(signal);
  }

  async emergencyCleanup(): Promise<void> {
    this.#intentionalStop = true;
    this.#emergencyConfirmed = true;
    this.#adapter.stopAllControls();
    try {
      await this.#runtime?.shutdown('requested');
    } catch {
      // Runtime cleanup is bounded below by direct adapter cleanup.
    }
    try {
      await this.#adapter.disconnect();
    } catch {
      // Adapter disconnect never exposes raw server errors.
    }
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    this.#intentionalStop = true;
    this.#controllerClosing = true;
    this.#failureHandler = undefined;
    for (const waiter of this.#waiters) {
      waiter.signal.removeEventListener('abort', waiter.onAbort);
      waiter.reject(new LocalSmokeRunError('failure', 'Control session closed.'));
    }
    this.#waiters.clear();
    try {
      await this.#runtime?.shutdown('requested');
    } catch {
      // Already-stopped runtimes are safe.
    }
    const socket = this.#socket;
    if (socket !== undefined && socket.readyState !== WebSocket.CLOSED) {
      socket.terminate();
    }
    const server = this.#server;
    const httpServer = this.#httpServer;
    if (server !== undefined && httpServer !== undefined) {
      await closeController(server, httpServer, this.#controllerSockets);
      if (
        httpServer.listening ||
        server.clients.size !== 0 ||
        this.#controllerSockets.size !== 0
      ) {
        throw new LocalSmokeRunError(
          'failure',
          'Temporary controller did not close cleanly.'
        );
      }
    }
  }

  observations(): readonly LocalSmokeCommandObservation[] {
    for (const observation of this.#observations) {
      observation.ackCount = this.#count(observation.commandId, 'command_ack');
      observation.terminalCount = this.#count(
        observation.commandId,
        'terminal_result'
      );
    }
    return this.#observations;
  }

  maximumObservedOwnerDistance(): number | null {
    return this.#maximumOwnerDistance === null
      ? null
      : Math.round(this.#maximumOwnerDistance * 100) / 100;
  }

  emergencyStopConfirmed(): boolean {
    return this.#runtime?.status().emergencyStopActive ?? this.#emergencyConfirmed;
  }

  async #nextConnection(
    controlToken: string,
    signal: AbortSignal
  ): Promise<WebSocket> {
    const server = this.#server;
    if (server === undefined) {
      throw new LocalSmokeRunError('failure', 'Controller server was not created.');
    }
    const connection = once(server, 'connection', { signal }) as Promise<
      [WebSocket, IncomingMessage]
    >;
    const [socket, request] = await connection;
    if (
      request.url !== CONTROL_PATH ||
      request.headers.authorization !== `Bearer ${controlToken}`
    ) {
      socket.close(1008, 'connection not authorized');
      throw new LocalSmokeRunError('failure', 'Temporary control authentication failed.');
    }
    socket.on('message', (data, isBinary) => {
      this.#receiveFrame(data, isBinary);
    });
    socket.once('error', () => {
      this.#failSession('Temporary control connection failed.');
    });
    socket.once('close', () => {
      this.#failSession('Temporary control connection closed.');
    });
    return socket;
  }

  #receiveFrame(data: RawData, isBinary: boolean): void {
    if (isBinary) {
      this.#failSession('Binary control frame was rejected.');
      return;
    }
    let message: ProtocolMessage;
    try {
      message = parseProtocolMessage(rawDataToBuffer(data).toString('utf8'));
    } catch {
      this.#failSession('Invalid control frame was rejected.');
      return;
    }
    this.#frames.push(message);
    for (const waiter of [...this.#waiters]) {
      if (!waiter.predicate(message)) continue;
      this.#waiters.delete(waiter);
      waiter.signal.removeEventListener('abort', waiter.onAbort);
      waiter.resolve(message);
    }
  }

  #waitFor(
    predicate: FramePredicate,
    signal: AbortSignal,
    firstFrame = 0
  ): Promise<ProtocolMessage> {
    const existing = this.#frames.slice(firstFrame).find(predicate);
    if (existing !== undefined) return Promise.resolve(existing);
    if (signal.aborted) {
      return Promise.reject(stopReason(signal, 'Control wait aborted.'));
    }
    return new Promise<ProtocolMessage>((resolvePromise, rejectPromise) => {
      const waiter: FrameWaiter = {
        predicate,
        resolve: resolvePromise,
        reject: rejectPromise,
        signal,
        onAbort: () => undefined
      };
      waiter.onAbort = () => {
        this.#waiters.delete(waiter);
        rejectPromise(stopReason(signal, 'Control wait aborted.'));
      };
      this.#waiters.add(waiter);
      signal.addEventListener('abort', waiter.onAbort, { once: true });
      if (signal.aborted) waiter.onAbort();
    });
  }

  #send(value: unknown): void {
    const socket = this.#socket;
    if (socket?.readyState !== WebSocket.OPEN) {
      throw new LocalSmokeRunError('failure', 'Temporary control connection is unavailable.');
    }
    let message: ProtocolMessage;
    try {
      message = parseProtocolMessage(value);
    } catch {
      throw new LocalSmokeRunError('failure', 'Temporary control message was invalid.');
    }
    socket.send(JSON.stringify(message), { binary: false, compress: false });
  }

  #count(commandId: string, type: 'command_ack' | 'terminal_result'): number {
    return this.#frames.filter(
      (message) =>
        message.type === type &&
        'command_id' in message &&
        message.command_id === commandId
    ).length;
  }

  async #sampleOwnerDistance(): Promise<void> {
    const config = this.#config;
    if (config === undefined) return;
    const bot = this.#adapter.getBotPosition();
    const owner = this.#adapter.getPlayer(config.ownerUsername);
    if (bot === undefined || owner === undefined) return;
    const distance = positionDistance(bot, owner.position);
    if (!Number.isFinite(distance)) {
      throw new LocalSmokeRunError('failure', 'Owner distance exceeded the safe limit.');
    }
    this.#recordOwnerDistance(distance);
    if (distance > OWNER_DISTANCE_LIMIT) {
      throw new LocalSmokeRunError('failure', 'Owner distance exceeded the safe limit.');
    }
  }

  #failWaiters(message: string): void {
    for (const waiter of this.#waiters) {
      waiter.signal.removeEventListener('abort', waiter.onAbort);
      waiter.reject(new LocalSmokeRunError('failure', message));
    }
    this.#waiters.clear();
  }

  #failSession(message: string): void {
    if (this.#intentionalStop || this.#closed) return;
    this.#failWaiters(message);
    this.#failureHandler?.();
  }

  #requireConfig(): LocalSmokeConfig {
    if (this.#config === undefined) {
      throw new LocalSmokeRunError('failure', 'Smoke session was not configured.');
    }
    return this.#config;
  }

  #requireBotPosition(): SafePosition {
    const position = this.#adapter.getBotPosition();
    if (position === undefined || !finitePosition(position)) {
      throw new LocalSmokeRunError('failure', 'Bot position was unavailable.');
    }
    return position;
  }

  #recordOwnerDistance(distance: number): void {
    this.#maximumOwnerDistance = Math.max(
      this.#maximumOwnerDistance ?? 0,
      distance
    );
  }
}

function rawDataToBuffer(data: RawData): Buffer {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.concat(data);
  return Buffer.from(data);
}

function positionDistance(first: SafePosition, second: SafePosition): number {
  return Math.hypot(
    second.x - first.x,
    second.y - first.y,
    second.z - first.z
  );
}

function finitePosition(position: SafePosition): boolean {
  return (
    Number.isFinite(position.x) &&
    Number.isFinite(position.y) &&
    Number.isFinite(position.z)
  );
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(stopReason(signal, 'Delay aborted.'));
  return new Promise<void>((resolvePromise, rejectPromise) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolvePromise();
    }, milliseconds);
    const onAbort = (): void => {
      clearTimeout(timer);
      rejectPromise(stopReason(signal, 'Delay aborted.'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    return Promise.reject(stopReason(signal, 'Operation aborted.'));
  }
  return new Promise<T>((resolvePromise, rejectPromise) => {
    const onAbort = (): void => {
      rejectPromise(stopReason(signal, 'Operation aborted.'));
    };
    signal.addEventListener('abort', onAbort, { once: true });
    void promise.then(
      (value) => {
        signal.removeEventListener('abort', onAbort);
        resolvePromise(value);
      },
      () => {
        signal.removeEventListener('abort', onAbort);
        rejectPromise(
          signal.aborted
            ? stopReason(signal, 'Operation aborted.')
            : new LocalSmokeRunError('failure', 'Operation failed safely.')
        );
      }
    );
  });
}

function throwIfStopped(signal: AbortSignal): void {
  if (signal.aborted) throw stopReason(signal, 'Operation aborted.');
}

function stopReason(signal: AbortSignal, fallback: string): LocalSmokeRunError {
  return signal.reason instanceof LocalSmokeRunError
    ? signal.reason
    : new LocalSmokeRunError('abort', fallback);
}

async function closeController(
  server: WebSocketServer,
  httpServer: HttpServer,
  sockets: Set<Socket>
): Promise<void> {
  const forceClosed = (): void => {
    for (const client of server.clients) client.terminate();
    for (const socket of sockets) {
      socket.on('error', () => undefined);
      socket.destroy();
    }
    httpServer.closeAllConnections();
  };
  forceClosed();
  const closed = Promise.all([
    closeWebSocketServer(server),
    closeHttpServer(httpServer)
  ]).then(() => undefined);
  forceClosed();
  await boundedControllerClose(closed, forceClosed);
}

function closeWebSocketServer(server: WebSocketServer): Promise<void> {
  const wasListening = server.address() !== null;
  return new Promise<void>((resolvePromise, rejectPromise) => {
    try {
      server.close((error) => {
        if (error !== undefined && wasListening) {
          rejectPromise(
            new LocalSmokeRunError(
              'failure',
              'Temporary controller did not close cleanly.'
            )
          );
          return;
        }
        resolvePromise();
      });
    } catch {
      rejectPromise(
        new LocalSmokeRunError(
          'failure',
          'Temporary controller did not close cleanly.'
        )
      );
    }
  });
}

function closeHttpServer(server: HttpServer): Promise<void> {
  const wasListening = server.listening;
  return new Promise<void>((resolvePromise, rejectPromise) => {
    try {
      server.close((error) => {
        if (error !== undefined && wasListening) {
          rejectPromise(
            new LocalSmokeRunError(
              'failure',
              'Temporary controller transport did not close cleanly.'
            )
          );
          return;
        }
        resolvePromise();
      });
    } catch {
      rejectPromise(
        new LocalSmokeRunError(
          'failure',
          'Temporary controller transport did not close cleanly.'
        )
      );
    }
  });
}

function boundedControllerClose(
  close: Promise<void>,
  forceClosed: () => void
): Promise<void> {
  return new Promise<void>((resolvePromise, rejectPromise) => {
    let settled = false;
    const finish = (error?: LocalSmokeRunError): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error === undefined) resolvePromise();
      else rejectPromise(error);
    };
    const timer = setTimeout(() => {
      forceClosed();
      finish(
        new LocalSmokeRunError(
          'failure',
          'Temporary controller cleanup timed out.'
        )
      );
    }, 2_000);
    void close.then(
      () => finish(),
      () => finish(
        new LocalSmokeRunError(
          'failure',
          'Temporary controller did not close cleanly.'
        )
      )
    );
  });
}

function abortableEvent(
  emitter: NodeJS.EventEmitter,
  event: string,
  signal: AbortSignal
): Promise<void> {
  return abortable(
    once(emitter, event).then(() => undefined),
    signal
  );
}

function isDirectExecution(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && pathToFileURL(resolve(entry)).href === import.meta.url;
}

if (isDirectExecution()) {
  void runLocalSmokeMain().then((exitCode) => {
    process.exitCode = exitCode;
  });
}
