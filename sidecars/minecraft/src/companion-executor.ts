import {
  CompanionStateMachine,
  type CompanionOperationalState,
  type CompanionStateTransitionOptions
} from './companion-state.js';
import type {
  ForwardSafety,
  MinecraftMovementAdapter,
  MinecraftOwnerState,
  ObservedPlayer,
  SafePosition
} from './minecraft-adapter.js';
import type { CommandName, FailureCode, TerminalOutcome } from './protocol.js';

const OVERWORLD = 'minecraft:overworld';
const OWNER_MAXIMUM_DISTANCE = 32;
const OWNER_LOSS_GRACE_MS = 10_000;
const PATH_STALL_MS = 5_000;
const BLOCKED_RETRY_DELAY_MS = 1_000;
const MAXIMUM_BLOCKED_RETRIES = 3;
const MAXIMUM_FOLLOW_MS = 900_000;
const MAXIMUM_COME_MS = 60_000;
const PROGRESS_INTERVAL_MS = 5_000;
const MATERIAL_POSITION_DELTA = 0.2;

export type CompanionExecutionResult = Readonly<{
  outcome: Extract<
    TerminalOutcome,
    'completed' | 'cancelled' | 'failed' | 'timed_out'
  >;
  safeDetail: string;
  failureCode: FailureCode | null;
  retriable: boolean;
}>;

export type CompanionProgress = Readonly<{
  phase: 'started' | 'moving' | 'waiting' | 'retrying';
  safeDetail: string;
  ownerDistance: number | null;
  retries: number;
}>;

export type CompanionExecutorDependencies = Readonly<{
  now?: () => Date;
  monotonicNowMs?: () => number;
  setTimeout?: (
    callback: () => void,
    milliseconds: number
  ) => CompanionExecutorTimer;
  clearTimeout?: (timer: CompanionExecutorTimer) => void;
  onStateChange?: (state: CompanionOperationalState) => void;
  onProgress?: (progress: CompanionProgress) => void;
}>;

export type CompanionExecutorTimer = Readonly<{ unref?: () => void }>;

type MovementKind = 'follow' | 'come';

type BlockedEpisode = {
  kind: Exclude<ForwardSafety['kind'], 'safe'>;
  retries: number;
  retryAtMs: number;
  startedAtMs: number;
};

type MovementSession = {
  id: number;
  kind: MovementKind;
  arrivalDistance: number;
  deadlineAtMs: number;
  signal: AbortSignal;
  abortHandler: () => void;
  cleanupTick: () => void;
  deadlineTimer: CompanionExecutorTimer | undefined;
  resolve: (result: CompanionExecutionResult) => void;
  settled: boolean;
  target: SafePosition | null;
  ownerLostAtMs: number | null;
  ownerLossTimer: CompanionExecutorTimer | undefined;
  blocked: BlockedEpisode | null;
  lastPosition: SafePosition | undefined;
  lastMaterialProgressAtMs: number;
  lastProgressAtMs: number;
  lastProgressSignature: string | null;
  lookInFlight: boolean;
  steeringEpoch: number;
};

export class CompanionExecutor {
  readonly #adapter: MinecraftMovementAdapter;
  readonly #ownerUsername: string | null;
  readonly #now: () => Date;
  readonly #monotonicNowMs: () => number;
  readonly #setTimeout: (
    callback: () => void,
    milliseconds: number
  ) => CompanionExecutorTimer;
  readonly #clearTimeout: (timer: CompanionExecutorTimer) => void;
  readonly #onStateChange: (state: CompanionOperationalState) => void;
  readonly #onProgress: (progress: CompanionProgress) => void;
  readonly #state = new CompanionStateMachine();

  #activeMovement: MovementSession | undefined;
  #nextMovementId = 1;

  constructor(
    adapter: MinecraftMovementAdapter,
    ownerUsername: string | null,
    dependencies: CompanionExecutorDependencies = {}
  ) {
    this.#adapter = adapter;
    this.#ownerUsername = ownerUsername;
    this.#now = dependencies.now ?? (() => new Date());
    this.#monotonicNowMs = dependencies.monotonicNowMs ?? (() => performance.now());
    this.#setTimeout =
      dependencies.setTimeout ??
      ((callback, milliseconds) => setTimeout(callback, milliseconds));
    this.#clearTimeout =
      dependencies.clearTimeout ??
      ((timer) => clearTimeout(timer as NodeJS.Timeout));
    this.#onStateChange = dependencies.onStateChange ?? (() => undefined);
    this.#onProgress = dependencies.onProgress ?? (() => undefined);
  }

  state(): CompanionOperationalState {
    return this.#state.current();
  }

  activeMovementTarget(): SafePosition | null {
    const target = this.#activeMovement?.target;
    return target === undefined || target === null
      ? null
      : Object.freeze({ ...target });
  }

  owner(): MinecraftOwnerState | null {
    const owner = this.#readOwner();
    const botPosition = this.#adapter.getBotPosition();
    if (owner === undefined || botPosition === undefined) return null;
    const distance = positionDistance(botPosition, owner.position);
    if (!Number.isFinite(distance)) return null;
    return Object.freeze({
      username: owner.username,
      uuid: null,
      roundedPosition: Object.freeze({
        x: boundedInteger(owner.position.x, -30_000_000, 30_000_000),
        y: boundedInteger(owner.position.y, -2_048, 2_048),
        z: boundedInteger(owner.position.z, -30_000_000, 30_000_000)
      }),
      distance: Math.max(0, Math.min(1_000_000, distance)),
      sameDimension: owner.dimension === this.#adapter.getDimension()
    });
  }

  precondition(name: CommandName): FailureCode | null {
    if (!['follow_owner', 'wait_here', 'come_here', 'look_at_owner'].includes(name)) {
      return null;
    }
    const stateFailure = this.#minecraftStateFailure();
    if (stateFailure !== null) return stateFailure;
    if (name === 'wait_here') return null;
    if (this.#ownerUsername === null) return 'OWNER_NOT_CONFIGURED';
    const owner = this.#readOwner();
    if (owner === undefined) return 'OWNER_NOT_PRESENT';
    if (owner.dimension !== this.#adapter.getDimension()) {
      return 'UNSUPPORTED_DIMENSION';
    }
    const botPosition = this.#adapter.getBotPosition();
    if (botPosition === undefined) return 'DESTINATION_UNAVAILABLE';
    const distance = positionDistance(botPosition, owner.position);
    if (!Number.isFinite(distance)) return 'DESTINATION_UNAVAILABLE';
    if (
      (name === 'follow_owner' || name === 'come_here') &&
      distance > OWNER_MAXIMUM_DISTANCE
    ) {
      return 'OWNER_TOO_FAR_AWAY';
    }
    if (this.#activeMovement !== undefined) return 'COMMAND_ALREADY_ACTIVE';
    return null;
  }

  followOwner(
    distance: number,
    leaseDurationSeconds: number,
    deadlineAtMs: number,
    signal: AbortSignal
  ): Promise<CompanionExecutionResult> {
    const effectiveDeadline = Math.min(
      deadlineAtMs,
      this.#safeNowMs() +
        Math.min(MAXIMUM_FOLLOW_MS, Math.max(0, leaseDurationSeconds * 1_000))
    );
    return this.#startMovement('follow', distance, effectiveDeadline, signal);
  }

  comeHere(
    distance: number,
    deadlineAtMs: number,
    signal: AbortSignal
  ): Promise<CompanionExecutionResult> {
    const effectiveDeadline = Math.min(
      deadlineAtMs,
      this.#safeNowMs() + MAXIMUM_COME_MS
    );
    return this.#startMovement('come', distance, effectiveDeadline, signal);
  }

  waitHere(): CompanionExecutionResult {
    this.#clearMovementAuthority();
    if (this.#state.current() !== 'STOPPED') this.#transition('WAITING');
    return completed('Minecraft companion is waiting safely.');
  }

  async lookAtOwner(
    durationSeconds: number,
    deadlineAtMs: number,
    signal: AbortSignal
  ): Promise<CompanionExecutionResult> {
    this.#adapter.clearControls();
    if (signal.aborted) return cancelled();
    if (this.#safeNowMs() >= deadlineAtMs) return timedOut();
    if (!Number.isFinite(durationSeconds) || durationSeconds < 0.1 || durationSeconds > 10) {
      return failed('INVALID_COMMAND', false);
    }
    const stateFailure = this.#minecraftStateFailure();
    if (stateFailure !== null) return failed(stateFailure, false);
    const owner = this.#readOwner();
    if (owner === undefined) return failed('OWNER_NOT_PRESENT', true);
    if (owner.dimension !== this.#adapter.getDimension()) {
      return failed('UNSUPPORTED_DIMENSION', false);
    }
    const durationMs = Math.max(100, Math.min(10_000, durationSeconds * 1_000));
    const remainingMs = Math.min(durationMs, deadlineAtMs - this.#safeNowMs());
    const lookResult = await this.#boundedLook(
      ownerLookPosition(owner),
      remainingMs,
      signal
    );
    if (lookResult === 'failed') {
      this.#adapter.clearControls();
      return failed('DESTINATION_UNAVAILABLE', true);
    }
    this.#adapter.clearControls();
    if (lookResult === 'cancelled') return cancelled();
    if (lookResult === 'timed_out' || this.#safeNowMs() >= deadlineAtMs) {
      return timedOut();
    }
    return completed('Minecraft companion looked at the configured owner.');
  }

  stop(): CompanionExecutionResult {
    this.#clearMovementAuthority();
    const state = this.#adapter.state();
    if (this.#state.current() !== 'STOPPED') {
      if (state.connectionState === 'disconnected') this.#transition('DISCONNECTED');
      else if (!state.alive) this.#transition('DEAD');
      else this.#transition('IDLE');
    }
    return completed('Minecraft movement is stopped.');
  }

  cancelActiveMovement(): void {
    this.#clearMovementAuthority();
  }

  emergencyStop(): void {
    this.#clearMovementAuthority();
    if (this.#state.current() !== 'STOPPED') this.#transition('STOPPED');
  }

  synchronizeState(
    state: CompanionOperationalState,
    options: CompanionStateTransitionOptions = {}
  ): void {
    if (this.#state.current() === state) return;
    this.#state.synchronize(state, options);
    this.#onStateChange(this.#state.current());
  }

  #startMovement(
    kind: MovementKind,
    arrivalDistance: number,
    deadlineAtMs: number,
    signal: AbortSignal
  ): Promise<CompanionExecutionResult> {
    if (this.#activeMovement !== undefined) {
      return Promise.resolve(failed('COMMAND_ALREADY_ACTIVE', false));
    }
    const stateFailure = this.#minecraftStateFailure();
    if (stateFailure !== null) return Promise.resolve(failed(stateFailure, false));
    if (this.#ownerUsername === null) {
      return Promise.resolve(failed('OWNER_NOT_CONFIGURED', false));
    }
    if (signal.aborted) return Promise.resolve(cancelled());
    if (this.#safeNowMs() >= deadlineAtMs) return Promise.resolve(timedOut());

    return new Promise<CompanionExecutionResult>((resolve) => {
      const session: MovementSession = {
        id: this.#nextMovementId++,
        kind,
        arrivalDistance,
        deadlineAtMs,
        signal,
        abortHandler: () => undefined,
        cleanupTick: () => undefined,
        deadlineTimer: undefined,
        resolve,
        settled: false,
        target: null,
        ownerLostAtMs: null,
        ownerLossTimer: undefined,
        blocked: null,
        lastPosition: this.#adapter.getBotPosition(),
        lastMaterialProgressAtMs: this.#safeMonotonicNow(),
        lastProgressAtMs: Number.NEGATIVE_INFINITY,
        lastProgressSignature: null,
        lookInFlight: false,
        steeringEpoch: 0
      };
      session.abortHandler = () => {
        this.#finishMovement(session, cancelled());
      };
      this.#activeMovement = session;
      try {
        session.cleanupTick = this.#adapter.onMovementTick(() => {
          try {
            this.#movementTick(session);
          } catch {
            this.#adapter.clearControls();
            this.#finishMovement(
              session,
              failed('INTERNAL_SIDECAR_ERROR', false)
            );
          }
        });
      } catch {
        this.#activeMovement = undefined;
        this.#adapter.clearControls();
        resolve(failed('SAFETY_POLICY_BLOCKED', false));
        return;
      }
      try {
        this.#transition(kind === 'follow' ? 'FOLLOWING' : 'RETURNING');
      } catch {
        session.cleanupTick();
        this.#activeMovement = undefined;
        this.#adapter.clearControls();
        resolve(failed('INVALID_STATE', false));
        return;
      }
      const remainingMs = Math.max(1, deadlineAtMs - this.#safeNowMs());
      session.deadlineTimer = this.#setTimeout(() => {
        this.#finishMovement(session, timedOut());
      }, remainingMs);
      session.deadlineTimer.unref?.();
      signal.addEventListener('abort', session.abortHandler, { once: true });
      this.#emitProgress(
        session,
        'started',
        kind === 'follow'
          ? 'Following the configured owner on a direct safe route.'
          : 'Moving directly toward the configured owner.',
        null,
        0,
        true
      );
      if (signal.aborted) session.abortHandler();
    });
  }

  #movementTick(session: MovementSession): void {
    if (!this.#isActive(session)) return;
    this.#adapter.clearControls();
    if (session.signal.aborted) {
      this.#finishMovement(session, cancelled());
      return;
    }
    if (this.#safeNowMs() >= session.deadlineAtMs) {
      this.#finishMovement(session, timedOut());
      return;
    }
    const stateFailure = this.#minecraftStateFailure();
    if (stateFailure !== null) {
      const code =
        stateFailure === 'MINECRAFT_NOT_CONNECTED'
          ? 'MINECRAFT_SERVER_DISCONNECTED'
          : stateFailure;
      this.#finishMovement(
        session,
        failed(code, code === 'MINECRAFT_SERVER_DISCONNECTED')
      );
      return;
    }

    const owner = this.#readOwner();
    if (owner === undefined) {
      this.#handleMissingOwner(session);
      return;
    }
    if (owner.dimension !== this.#adapter.getDimension()) {
      this.#finishMovement(session, failed('UNSUPPORTED_DIMENSION', false));
      return;
    }
    const botPosition = this.#adapter.getBotPosition();
    if (botPosition === undefined) {
      this.#finishMovement(session, failed('DESTINATION_UNAVAILABLE', true));
      return;
    }
    const ownerDistance = positionDistance(botPosition, owner.position);
    if (!Number.isFinite(ownerDistance)) {
      this.#finishMovement(session, failed('DESTINATION_UNAVAILABLE', true));
      return;
    }
    if (ownerDistance > OWNER_MAXIMUM_DISTANCE) {
      this.#finishMovement(session, failed('OWNER_TOO_FAR_AWAY', true));
      return;
    }

    if (session.ownerLostAtMs !== null) {
      if (
        this.#safeMonotonicNow() - session.ownerLostAtMs >=
        OWNER_LOSS_GRACE_MS
      ) {
        this.#finishMovement(session, failed('OWNER_NOT_PRESENT', true));
        return;
      }
      if (session.ownerLossTimer !== undefined) {
        this.#clearTimeout(session.ownerLossTimer);
        session.ownerLossTimer = undefined;
      }
      session.ownerLostAtMs = null;
      session.lastMaterialProgressAtMs = this.#safeMonotonicNow();
      session.lastPosition = botPosition;
    }
    session.target = Object.freeze({ ...owner.position });

    if (ownerDistance <= session.arrivalDistance) {
      session.blocked = null;
      session.lookInFlight = false;
      session.steeringEpoch += 1;
      if (session.kind === 'come') {
        this.#finishMovement(
          session,
          completed('Minecraft companion reached the configured owner.')
        );
      } else {
        session.lastPosition = botPosition;
        session.lastMaterialProgressAtMs = this.#safeMonotonicNow();
        this.#emitProgress(
          session,
          'waiting',
          'Holding the configured follow distance.',
          ownerDistance,
          0
        );
      }
      return;
    }

    this.#recordMaterialProgress(session, botPosition);
    const safety = this.#adapter.inspectForwardStep(owner);
    if (safety.kind !== 'safe') {
      this.#handleBlockedStep(session, safety.kind, ownerDistance);
      return;
    }
    session.blocked = null;
    if (
      this.#safeMonotonicNow() - session.lastMaterialProgressAtMs >=
      PATH_STALL_MS
    ) {
      this.#finishMovement(session, failed('PATH_STALLED', true));
      return;
    }
    this.#emitProgress(
      session,
      'moving',
      session.kind === 'follow'
        ? 'Following the configured owner on a direct safe route.'
        : 'Moving directly toward the configured owner.',
      ownerDistance,
      0
    );
    if (session.lookInFlight) return;

    session.lookInFlight = true;
    const steeringEpoch = ++session.steeringEpoch;
    void this.#adapter
      .lookAt(ownerLookPosition(owner))
      .then(() => {
        this.#activateForward(session, steeringEpoch);
      })
      .catch(() => {
        if (!this.#isActive(session) || session.steeringEpoch !== steeringEpoch) {
          return;
        }
        this.#adapter.clearControls();
        session.lookInFlight = false;
        this.#finishMovement(session, failed('DESTINATION_UNAVAILABLE', true));
      });
  }

  #activateForward(session: MovementSession, steeringEpoch: number): void {
    if (!this.#isActive(session) || session.steeringEpoch !== steeringEpoch) return;
    session.lookInFlight = false;
    this.#adapter.clearControls();
    if (session.signal.aborted || this.#safeNowMs() >= session.deadlineAtMs) {
      this.#finishMovement(
        session,
        session.signal.aborted ? cancelled() : timedOut()
      );
      return;
    }
    const stateFailure = this.#minecraftStateFailure();
    if (stateFailure !== null) {
      const code =
        stateFailure === 'MINECRAFT_NOT_CONNECTED'
          ? 'MINECRAFT_SERVER_DISCONNECTED'
          : stateFailure;
      this.#finishMovement(
        session,
        failed(code, code === 'MINECRAFT_SERVER_DISCONNECTED')
      );
      return;
    }
    const owner = this.#readOwner();
    const botPosition = this.#adapter.getBotPosition();
    if (owner === undefined) {
      this.#handleMissingOwner(session);
      return;
    }
    if (
      botPosition === undefined ||
      owner.dimension !== this.#adapter.getDimension()
    ) {
      this.#finishMovement(session, failed('DESTINATION_UNAVAILABLE', true));
      return;
    }
    const distance = positionDistance(botPosition, owner.position);
    if (!Number.isFinite(distance)) {
      this.#finishMovement(session, failed('DESTINATION_UNAVAILABLE', true));
      return;
    }
    if (distance > OWNER_MAXIMUM_DISTANCE) {
      this.#finishMovement(session, failed('OWNER_TOO_FAR_AWAY', true));
      return;
    }
    if (distance <= session.arrivalDistance) {
      if (session.kind === 'come') {
        this.#finishMovement(
          session,
          completed('Minecraft companion reached the configured owner.')
        );
      }
      return;
    }
    const safety = this.#adapter.inspectForwardStep(owner);
    if (safety.kind !== 'safe') {
      this.#handleBlockedStep(session, safety.kind, distance);
      return;
    }
    session.target = Object.freeze({ ...owner.position });
    this.#adapter.setForward(true);
  }

  #handleMissingOwner(session: MovementSession): void {
    if (!this.#isActive(session)) return;
    this.#adapter.clearControls();
    session.target = null;
    session.blocked = null;
    session.lookInFlight = false;
    session.steeringEpoch += 1;
    if (session.kind === 'come') {
      this.#finishMovement(session, failed('OWNER_NOT_PRESENT', true));
      return;
    }
    const now = this.#safeMonotonicNow();
    if (session.ownerLostAtMs === null) {
      session.ownerLostAtMs = now;
      session.ownerLossTimer = this.#setTimeout(() => {
        this.#finishMovement(session, failed('OWNER_NOT_PRESENT', true));
      }, OWNER_LOSS_GRACE_MS);
      session.ownerLossTimer.unref?.();
    }
    session.lastMaterialProgressAtMs = now;
    if (now - session.ownerLostAtMs >= OWNER_LOSS_GRACE_MS) {
      this.#finishMovement(session, failed('OWNER_NOT_PRESENT', true));
      return;
    }
    this.#emitProgress(
      session,
      'waiting',
      'Configured owner is temporarily absent; movement is stopped.',
      null,
      0
    );
  }

  #handleBlockedStep(
    session: MovementSession,
    kind: Exclude<ForwardSafety['kind'], 'safe'>,
    ownerDistance: number
  ): void {
    if (!this.#isActive(session)) return;
    this.#adapter.clearControls();
    session.target = null;
    session.lookInFlight = false;
    session.steeringEpoch += 1;
    const now = this.#safeMonotonicNow();
    if (session.blocked === null) {
      session.blocked = {
        kind,
        retries: 1,
        retryAtMs: now + BLOCKED_RETRY_DELAY_MS,
        startedAtMs: now
      };
    } else if (now < session.blocked.retryAtMs) {
      session.blocked.kind = kind;
      if (
        now - session.blocked.startedAtMs >= PATH_STALL_MS ||
        now - session.lastMaterialProgressAtMs >= PATH_STALL_MS
      ) {
        this.#finishMovement(session, failed(failureForSafety(kind), true));
      }
      return;
    } else {
      session.blocked.kind = kind;
      session.blocked.retries += 1;
      session.blocked.retryAtMs = now + BLOCKED_RETRY_DELAY_MS;
    }
    const retries = session.blocked.retries;
    this.#emitProgress(
      session,
      'retrying',
      'Direct route is blocked; remaining stationary before a bounded retry.',
      ownerDistance,
      retries
    );
    if (
      retries >= MAXIMUM_BLOCKED_RETRIES ||
      now - session.blocked.startedAtMs >= PATH_STALL_MS ||
      now - session.lastMaterialProgressAtMs >= PATH_STALL_MS
    ) {
      this.#finishMovement(session, failed(failureForSafety(kind), true));
    }
  }

  #recordMaterialProgress(
    session: MovementSession,
    currentPosition: SafePosition
  ): void {
    const previous = session.lastPosition;
    if (
      previous === undefined ||
      positionDistance(previous, currentPosition) >= MATERIAL_POSITION_DELTA
    ) {
      session.lastPosition = Object.freeze({ ...currentPosition });
      session.lastMaterialProgressAtMs = this.#safeMonotonicNow();
    }
  }

  #finishMovement(
    session: MovementSession,
    result: CompanionExecutionResult
  ): void {
    if (!this.#isActive(session) || session.settled) return;
    session.settled = true;
    this.#adapter.clearControls();
    session.target = null;
    session.lookInFlight = false;
    session.steeringEpoch += 1;
    session.cleanupTick();
    if (session.deadlineTimer !== undefined) {
      this.#clearTimeout(session.deadlineTimer);
      session.deadlineTimer = undefined;
    }
    if (session.ownerLossTimer !== undefined) {
      this.#clearTimeout(session.ownerLossTimer);
      session.ownerLossTimer = undefined;
    }
    session.signal.removeEventListener('abort', session.abortHandler);
    this.#activeMovement = undefined;
    if (this.#state.current() !== 'STOPPED') {
      const state = this.#adapter.state();
      if (state.connectionState === 'disconnected') this.#transition('DISCONNECTED');
      else if (!state.alive) this.#transition('DEAD');
      else this.#transition('IDLE');
    }
    session.resolve(result);
  }

  #clearMovementAuthority(): void {
    this.#adapter.clearControls();
    const session = this.#activeMovement;
    if (session !== undefined) this.#finishMovement(session, cancelled());
  }

  #minecraftStateFailure(): FailureCode | null {
    const state = this.#adapter.state();
    if (state.connectionState === 'disconnected') return 'MINECRAFT_NOT_CONNECTED';
    if (state.connectionState !== 'connected' || !state.spawned) return 'INVALID_STATE';
    if (!state.alive) return 'BOT_DEAD';
    if (
      state.dimension !== OVERWORLD ||
      this.#adapter.getDimension() !== OVERWORLD
    ) {
      return 'UNSUPPORTED_DIMENSION';
    }
    return null;
  }

  #readOwner(): ObservedPlayer | undefined {
    return this.#ownerUsername === null
      ? undefined
      : this.#adapter.getPlayer(this.#ownerUsername);
  }

  #boundedLook(
    position: SafePosition,
    timeoutMs: number,
    signal: AbortSignal
  ): Promise<'completed' | 'cancelled' | 'timed_out' | 'failed'> {
    if (signal.aborted) return Promise.resolve('cancelled');
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return Promise.resolve('timed_out');
    }
    return new Promise((resolve) => {
      let settled = false;
      let timer: CompanionExecutorTimer | undefined;
      const finish = (
        outcome: 'completed' | 'cancelled' | 'timed_out' | 'failed'
      ): void => {
        if (settled) return;
        settled = true;
        if (timer !== undefined) this.#clearTimeout(timer);
        signal.removeEventListener('abort', onAbort);
        resolve(outcome);
      };
      const onAbort = (): void => finish('cancelled');
      timer = this.#setTimeout(() => finish('timed_out'), timeoutMs);
      timer.unref?.();
      signal.addEventListener('abort', onAbort, { once: true });
      void this.#adapter
        .lookAt(position)
        .then(() => finish('completed'))
        .catch(() => finish('failed'));
      if (signal.aborted) onAbort();
    });
  }

  #transition(state: CompanionOperationalState): void {
    if (this.#state.current() === state) return;
    this.#state.transition(state);
    this.#onStateChange(state);
  }

  #emitProgress(
    session: MovementSession,
    phase: CompanionProgress['phase'],
    safeDetail: string,
    ownerDistance: number | null,
    retries: number,
    force = false
  ): void {
    const signature = `${phase}:${retries}:${distanceBucket(ownerDistance)}`;
    const now = this.#safeMonotonicNow();
    if (!force) {
      if (signature === session.lastProgressSignature) return;
      if (now - session.lastProgressAtMs < PROGRESS_INTERVAL_MS) return;
    }
    session.lastProgressSignature = signature;
    session.lastProgressAtMs = now;
    this.#onProgress(
      Object.freeze({ phase, safeDetail, ownerDistance, retries })
    );
  }

  #isActive(session: MovementSession): boolean {
    return this.#activeMovement === session && !session.settled;
  }

  #safeNowMs(): number {
    const value = this.#now();
    return value instanceof Date && Number.isFinite(value.getTime())
      ? value.getTime()
      : 0;
  }

  #safeMonotonicNow(): number {
    const value = this.#monotonicNowMs();
    return Number.isFinite(value) ? Math.max(0, value) : 0;
  }
}

function completed(safeDetail: string): CompanionExecutionResult {
  return result('completed', safeDetail, null, false);
}

function cancelled(): CompanionExecutionResult {
  return result('cancelled', 'Minecraft movement was cancelled.', null, false);
}

function timedOut(): CompanionExecutionResult {
  return result(
    'timed_out',
    'Minecraft movement deadline was exceeded.',
    'DEADLINE_EXCEEDED',
    true
  );
}

function failed(
  failureCode: FailureCode,
  retriable: boolean,
  safeDetail = safeDetailForFailure(failureCode)
): CompanionExecutionResult {
  return result('failed', safeDetail, failureCode, retriable);
}

function result(
  outcome: CompanionExecutionResult['outcome'],
  safeDetail: string,
  failureCode: FailureCode | null,
  retriable: boolean
): CompanionExecutionResult {
  return Object.freeze({ outcome, safeDetail, failureCode, retriable });
}

function failureForSafety(
  kind: Exclude<ForwardSafety['kind'], 'safe'>
): FailureCode {
  switch (kind) {
    case 'blocked':
      return 'PATH_NOT_FOUND';
    case 'unsupported_drop':
    case 'unloaded':
      return 'DESTINATION_UNAVAILABLE';
    case 'hazard':
    case 'liquid':
    case 'dimension_mismatch':
      return 'SAFETY_POLICY_BLOCKED';
  }
}

function safeDetailForFailure(code: FailureCode): string {
  const details: Partial<Record<FailureCode, string>> = {
    MINECRAFT_NOT_CONNECTED: 'Minecraft is not connected.',
    MINECRAFT_SERVER_DISCONNECTED: 'Minecraft server disconnected.',
    OWNER_NOT_CONFIGURED: 'Minecraft owner is not configured.',
    OWNER_NOT_PRESENT: 'Configured Minecraft owner is not present.',
    OWNER_TOO_FAR_AWAY: 'Configured Minecraft owner is too far away.',
    DESTINATION_UNAVAILABLE: 'The direct destination cannot be proven safe.',
    PATH_NOT_FOUND: 'The direct route is blocked.',
    PATH_STALLED: 'Direct movement made no safe progress.',
    SAFETY_POLICY_BLOCKED: 'Terrain safety policy stopped movement.',
    BOT_DEAD: 'Minecraft bot is dead.',
    UNSUPPORTED_DIMENSION: 'Minecraft movement is restricted to the Overworld.',
    INVALID_STATE: 'Minecraft state does not allow movement.',
    INVALID_COMMAND: 'Minecraft command arguments are invalid.',
    COMMAND_ALREADY_ACTIVE: 'Another movement command is active.'
  };
  return details[code] ?? 'Minecraft movement failed safely.';
}

function ownerLookPosition(owner: ObservedPlayer): SafePosition {
  return Object.freeze({
    x: owner.position.x,
    y: owner.position.y + 1.6,
    z: owner.position.z
  });
}

function positionDistance(first: SafePosition, second: SafePosition): number {
  return Math.hypot(
    second.x - first.x,
    second.y - first.y,
    second.z - first.z
  );
}

function boundedInteger(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, Math.round(value)));
}

function distanceBucket(value: number | null): string {
  return value === null || !Number.isFinite(value)
    ? 'none'
    : String(Math.round(value));
}
