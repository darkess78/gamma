import type {
  MinecraftAdapter,
  MinecraftAdapterConnectionConfig,
  MinecraftAdapterEvent,
  MinecraftAdapterState
} from './minecraft-adapter.js';
import type {
  CancelCommandMessage,
  CommandMessage,
  CommandName,
  CompanionState,
  EmergencyStopMessage,
  FailureCode,
  TerminalOutcome
} from './protocol.js';
import type { SidecarOutboundMessage } from './control-client.js';
import {
  connectedSidecarStatus,
  minecraftStatus,
  stateSnapshot
} from './status.js';

const JOIN_TIMEOUT_MS = 45_000;
const DEFAULT_COMMAND_CACHE_CAPACITY = 1_000;
const DEFAULT_COMMAND_CACHE_TTL_MS = 600_000;

export type MinecraftDispatcherTimer = Readonly<{
  unref?: () => void;
}>;

export type MinecraftCommandDispatcherStatus = Readonly<{
  companionState: CompanionState;
  emergencyStopActive: boolean;
  minecraft: MinecraftAdapterState;
  activeCommand: Readonly<{
    commandId: string;
    commandName: CommandName;
  }> | null;
}>;

export type MinecraftCommandDispatcherDependencies = Readonly<{
  config: MinecraftAdapterConnectionConfig;
  adapter: MinecraftAdapter;
  send: (message: SidecarOutboundMessage) => Promise<void>;
  now?: () => Date;
  monotonicNowMs?: () => number;
  uptimeSeconds?: () => number;
  setTimeout?: (callback: () => void, milliseconds: number) => MinecraftDispatcherTimer;
  clearTimeout?: (timer: MinecraftDispatcherTimer) => void;
  onDeliveryFailure?: () => void;
}>;

type ActiveCommand = {
  message: CommandMessage;
  startedAtMs: number;
  controller: AbortController;
  terminalized: boolean;
  timedOut: boolean;
};

type TerminalRecord = Readonly<{
  outcome: TerminalOutcome;
  failureCode: FailureCode | null;
}>;

type CommandAckDraft = Extract<SidecarOutboundMessage, { type: 'command_ack' }>;
type TerminalResultDraft = Extract<
  SidecarOutboundMessage,
  { type: 'terminal_result' }
>;

type CachedCommand = {
  fingerprint: string;
  acknowledgment: CommandAckDraft;
  terminal: TerminalResultDraft | null;
  expiresAtMs: number;
};

type Correlation = Readonly<{
  traceId: string;
  commandId: string;
}>;

export class MinecraftCommandDispatcher {
  readonly #minecraftConfig: MinecraftAdapterConnectionConfig;
  readonly #adapter: MinecraftAdapter;
  readonly #send: (message: SidecarOutboundMessage) => Promise<void>;
  readonly #now: () => Date;
  readonly #monotonicNowMs: () => number;
  readonly #uptimeSeconds: () => number;
  readonly #setTimeout: (
    callback: () => void,
    milliseconds: number
  ) => MinecraftDispatcherTimer;
  readonly #clearTimeout: (timer: MinecraftDispatcherTimer) => void;
  readonly #onDeliveryFailure: () => void;
  readonly #commandCache = new Map<string, CachedCommand>();

  #active: ActiveCommand | undefined;
  #commandCacheCapacity = DEFAULT_COMMAND_CACHE_CAPACITY;
  #commandCacheTtlMs = DEFAULT_COMMAND_CACHE_TTL_MS;
  #companionState: CompanionState = 'DISCONNECTED';
  #emergencyStopActive = false;
  #emergencyLeaveCompleted = false;
  #lastTerminal: TerminalRecord | null = null;
  #acceptingEvents = true;
  #eventChain: Promise<void> = Promise.resolve();

  constructor(dependencies: MinecraftCommandDispatcherDependencies) {
    this.#minecraftConfig = Object.freeze({
      minecraftServerHost: dependencies.config.minecraftServerHost,
      minecraftServerPort: dependencies.config.minecraftServerPort,
      minecraftVersion: dependencies.config.minecraftVersion,
      minecraftAccountMode: dependencies.config.minecraftAccountMode,
      minecraftBotUsername: dependencies.config.minecraftBotUsername
    });
    this.#adapter = dependencies.adapter;
    this.#send = dependencies.send;
    this.#now = dependencies.now ?? (() => new Date());
    this.#monotonicNowMs = dependencies.monotonicNowMs ?? (() => performance.now());
    this.#uptimeSeconds = dependencies.uptimeSeconds ?? (() => 0);
    this.#setTimeout =
      dependencies.setTimeout ??
      ((callback, milliseconds) => setTimeout(callback, milliseconds));
    this.#clearTimeout =
      dependencies.clearTimeout ??
      ((timer) => clearTimeout(timer as NodeJS.Timeout));
    this.#onDeliveryFailure = dependencies.onDeliveryFailure ?? (() => undefined);
    this.#adapter.setEventHandler((event) => this.#queueAdapterEvent(event));
  }

  status(): MinecraftCommandDispatcherStatus {
    const active = this.#active;
    return Object.freeze({
      companionState: this.#companionState,
      emergencyStopActive: this.#emergencyStopActive,
      minecraft: this.#adapter.state(),
      activeCommand:
        active === undefined
          ? null
          : Object.freeze({
              commandId: active.message.command_id,
              commandName: active.message.payload.name
            })
    });
  }

  beginSession(commandCacheCapacity: number, commandCacheTtlSeconds: number): void {
    this.#commandCache.clear();
    this.#commandCacheCapacity = Math.max(
      1,
      Math.min(10_000, Math.floor(commandCacheCapacity))
    );
    this.#commandCacheTtlMs = Math.max(
      60_000,
      Math.min(86_400_000, Math.floor(commandCacheTtlSeconds * 1_000))
    );
  }

  async publishCurrentStatus(correlation: Correlation | null = null): Promise<void> {
    try {
      await this.#publishCurrentStatus(correlation);
    } catch {
      this.#onDeliveryFailure();
    }
  }

  async handleCommand(message: CommandMessage): Promise<void> {
    try {
      const duplicate = this.#cachedCommand(message.command_id);
      if (duplicate !== undefined) {
        if (duplicate.fingerprint !== commandFingerprint(message)) {
          await this.#sendRejectedAckUncached(
            message,
            'INVALID_COMMAND',
            'Command identifier conflicts with an earlier command.'
          );
          return;
        }
        await this.#send(duplicate.acknowledgment);
        if (duplicate.terminal !== null) await this.#send(duplicate.terminal);
        return;
      }
      await this.#handleCommand(message);
    } catch {
      const active = this.#active;
      if (active?.message.command_id === message.command_id) {
        active.controller.abort();
        this.#adapter.stopAllControls();
        this.#active = undefined;
      }
      this.#onDeliveryFailure();
    }
  }

  async cancelCommand(message: CancelCommandMessage): Promise<void> {
    const active = this.#active;
    if (active === undefined || active.message.command_id !== message.command_id) {
      const cached = this.#cachedCommand(message.command_id);
      if (cached !== undefined) {
        try {
          await this.#send(cached.acknowledgment);
          if (cached.terminal !== null) await this.#send(cached.terminal);
        } catch {
          this.#onDeliveryFailure();
        }
      }
      return;
    }
    try {
      active.controller.abort();
      this.#adapter.stopAllControls();
      if (active.message.payload.name === 'join') {
        await this.#safeDisconnect();
      }
      await this.#finish(active, 'cancelled', 'Command cancelled.', null);
    } catch {
      this.#onDeliveryFailure();
    }
  }

  /** The latch and control clear occur synchronously before this method first awaits. */
  async emergencyStop(message: EmergencyStopMessage): Promise<void> {
    this.#emergencyStopActive = true;
    this.#emergencyLeaveCompleted = false;
    this.#companionState = 'STOPPED';
    this.#adapter.stopAllControls();
    const active = this.#active;
    if (active !== undefined) active.controller.abort();
    try {
      if (active?.message.payload.name === 'join') await this.#safeDisconnect();
      if (active !== undefined) {
        await this.#finish(active, 'cancelled', 'Command stopped safely.', null);
      }
      await this.#publishCurrentStatus({
        traceId: message.trace_id,
        commandId: message.command_id
      });
    } catch {
      this.#onDeliveryFailure();
    }
  }

  /** Clear local authority immediately and disconnect without sending new frames. */
  async controlDisconnected(): Promise<void> {
    this.#acceptingEvents = false;
    this.#adapter.setEventHandler(undefined);
    const active = this.#active;
    if (active !== undefined) active.controller.abort();
    this.#active = undefined;
    this.#adapter.stopAllControls();
    await this.#safeDisconnect();
    this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DISCONNECTED';
  }

  async waitForEvents(): Promise<void> {
    await this.#eventChain;
  }

  async #handleCommand(message: CommandMessage): Promise<void> {
    if (this.#deadlineExpired(message)) {
      await this.#sendRejectedAck(
        message,
        'DEADLINE_EXCEEDED',
        'Command deadline has passed.'
      );
      return;
    }

    const name = message.payload.name;
    if (isMovementCommand(name)) {
      await this.#sendRejectedAck(
        message,
        this.#emergencyStopActive
          ? 'EMERGENCY_STOP_ACTIVE'
          : 'SAFETY_POLICY_BLOCKED',
        this.#emergencyStopActive
          ? 'Emergency stop is active.'
          : 'Movement is unavailable until the pathfinder phase.'
      );
      return;
    }
    if (name === 'emergency_stop') {
      this.#emergencyStopActive = true;
      this.#emergencyLeaveCompleted = false;
      this.#companionState = 'STOPPED';
      this.#adapter.stopAllControls();
      const interrupted = this.#active;
      if (interrupted !== undefined) {
        interrupted.controller.abort();
        if (interrupted.message.payload.name === 'join') {
          await this.#safeDisconnect();
        }
        await this.#finish(
          interrupted,
          'cancelled',
          'Command stopped safely.',
          null
        );
      }
    }
    if (this.#active !== undefined) {
      await this.#sendRejectedAck(
        message,
        'COMMAND_ALREADY_ACTIVE',
        'Another command is active.'
      );
      return;
    }
    const stateFailure = this.#commandStateFailure(message);
    if (stateFailure !== null) {
      await this.#sendRejectedAck(message, stateFailure, safeDetailFor(stateFailure));
      return;
    }

    const active: ActiveCommand = {
      message,
      startedAtMs: this.#safeMonotonicNow(),
      controller: new AbortController(),
      terminalized: false,
      timedOut: false
    };
    this.#active = active;

    await this.#sendAcceptedAck(message);
    if (active.terminalized || this.#active !== active) return;
    switch (name) {
      case 'join':
        await this.#executeJoin(active);
        return;
      case 'leave':
        await this.#executeLeave(active);
        return;
      case 'report_status':
        await this.#executeReportStatus(active);
        return;
      case 'stop':
        await this.#executeStop(active);
        return;
      case 'emergency_stop':
        await this.#executeEmergencyStop(active);
        return;
      case 'follow_owner':
      case 'wait_here':
      case 'come_here':
      case 'look_at_owner':
        return;
    }
  }

  #commandStateFailure(message: CommandMessage): FailureCode | null {
    const state = this.#adapter.state();
    switch (message.payload.name) {
      case 'join':
        if (message.payload.arguments.connection_profile_id != null) {
          return 'INVALID_COMMAND';
        }
        if (this.#emergencyStopActive && !this.#emergencyLeaveCompleted) {
          return 'EMERGENCY_STOP_ACTIVE';
        }
        return state.connectionState === 'disconnected' ? null : 'INVALID_STATE';
      case 'leave':
      case 'report_status':
      case 'stop':
      case 'emergency_stop':
        return null;
      case 'follow_owner':
      case 'wait_here':
      case 'come_here':
      case 'look_at_owner':
        return 'SAFETY_POLICY_BLOCKED';
    }
  }

  async #executeJoin(active: ActiveCommand): Promise<void> {
    const connection = this.#adapter.connect(
      this.#minecraftConfig,
      active.controller.signal
    );
    void connection.catch(() => undefined);
    await this.#publishCurrentStatus(this.#correlation(active));
    const remainingMs = Math.max(
      1,
      Math.min(
        JOIN_TIMEOUT_MS,
        Date.parse(active.message.payload.deadline_at) - this.#safeNow().getTime()
      )
    );
    const timer = this.#setTimeout(() => {
      active.timedOut = true;
      active.controller.abort();
    }, remainingMs);
    timer.unref?.();
    try {
      await connection;
      if (active.terminalized || this.#active !== active) return;
      const state = this.#adapter.state();
      if (
        state.connectionState !== 'connected' ||
        !state.spawned ||
        !state.alive
      ) {
        await this.#failJoin(active, 'INVALID_STATE', 'Minecraft spawn was not established.');
        return;
      }
      if (state.negotiatedVersion !== this.#minecraftConfig.minecraftVersion) {
        await this.#failJoin(
          active,
          'PROTOCOL_MISMATCH',
          'Minecraft version did not match the configured version.'
        );
        return;
      }
      if (state.dimension !== 'minecraft:overworld') {
        await this.#failJoin(
          active,
          'UNSUPPORTED_DIMENSION',
          'Minecraft spawned outside the Overworld.'
        );
        return;
      }
      if (this.#emergencyStopActive && this.#emergencyLeaveCompleted) {
        this.#emergencyStopActive = false;
        this.#emergencyLeaveCompleted = false;
      }
      this.#companionState = 'IDLE';
      await this.#publishCurrentStatus(this.#correlation(active));
      await this.#finish(active, 'completed', 'Minecraft joined safely.', null);
    } catch {
      if (active.terminalized || this.#active !== active) return;
      this.#adapter.stopAllControls();
      await this.#safeDisconnect();
      this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DISCONNECTED';
      await this.#publishCurrentStatus(this.#correlation(active));
      if (active.timedOut) {
        await this.#finish(
          active,
          'timed_out',
          'Minecraft join deadline was exceeded.',
          failure('DEADLINE_EXCEEDED', 'Minecraft join deadline was exceeded.', true)
        );
      } else if (active.controller.signal.aborted) {
        await this.#finish(active, 'cancelled', 'Minecraft join was cancelled.', null);
      } else {
        await this.#finish(
          active,
          'failed',
          'Minecraft connection failed safely.',
          failure(
            'MINECRAFT_SERVER_DISCONNECTED',
            'Minecraft connection failed safely.',
            true
          )
        );
      }
    } finally {
      this.#clearTimeout(timer);
    }
  }

  async #failJoin(
    active: ActiveCommand,
    code: FailureCode,
    safeDetail: string
  ): Promise<void> {
    this.#adapter.stopAllControls();
    await this.#safeDisconnect();
    this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DISCONNECTED';
    await this.#publishCurrentStatus(this.#correlation(active));
    await this.#finish(
      active,
      'failed',
      safeDetail,
      failure(code, safeDetail, false)
    );
  }

  async #executeLeave(active: ActiveCommand): Promise<void> {
    this.#adapter.stopAllControls();
    await this.#safeDisconnect();
    if (this.#emergencyStopActive) {
      this.#emergencyLeaveCompleted = true;
      this.#companionState = 'STOPPED';
    } else {
      this.#companionState = 'DISCONNECTED';
    }
    await this.#publishCurrentStatus(this.#correlation(active));
    await this.#finish(active, 'completed', 'Minecraft left safely.', null);
  }

  async #executeReportStatus(active: ActiveCommand): Promise<void> {
    await this.#publishCurrentStatus(this.#correlation(active));
    await this.#finish(active, 'completed', 'Minecraft status reported.', null);
  }

  async #executeStop(active: ActiveCommand): Promise<void> {
    this.#adapter.stopAllControls();
    const state = this.#adapter.state();
    if (this.#emergencyStopActive) this.#companionState = 'STOPPED';
    else if (state.connectionState === 'disconnected') this.#companionState = 'DISCONNECTED';
    else if (!state.alive) this.#companionState = 'DEAD';
    else this.#companionState = 'IDLE';
    await this.#publishCurrentStatus(this.#correlation(active));
    await this.#finish(active, 'completed', 'Minecraft controls are stopped.', null);
  }

  async #executeEmergencyStop(active: ActiveCommand): Promise<void> {
    await this.#publishCurrentStatus(this.#correlation(active));
    await this.#finish(active, 'completed', 'Local emergency stop is active.', null);
  }

  async #sendAcceptedAck(message: CommandMessage): Promise<void> {
    const acknowledgment: CommandAckDraft = Object.freeze({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: true,
        command_name: message.payload.name,
        failure: null
      }
    });
    this.#cacheCommand(message, acknowledgment);
    await this.#send(acknowledgment);
  }

  async #sendRejectedAck(
    message: CommandMessage,
    code: FailureCode,
    safeDetail: string
  ): Promise<void> {
    const acknowledgment: CommandAckDraft = Object.freeze({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: false,
        command_name: message.payload.name,
        failure: failure(code, safeDetail, false)
      }
    });
    this.#cacheCommand(message, acknowledgment);
    await this.#send(acknowledgment);
  }

  async #sendRejectedAckUncached(
    message: CommandMessage,
    code: FailureCode,
    safeDetail: string
  ): Promise<void> {
    await this.#send({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: false,
        command_name: message.payload.name,
        failure: failure(code, safeDetail, false)
      }
    });
  }

  async #finish(
    active: ActiveCommand,
    outcome: TerminalOutcome,
    safeDetail: string,
    terminalFailure: ReturnType<typeof failure> | null
  ): Promise<void> {
    if (active.terminalized || this.#active !== active) return;
    active.terminalized = true;
    try {
      const terminal: TerminalResultDraft = Object.freeze({
        type: 'terminal_result',
        trace_id: active.message.trace_id,
        command_id: active.message.command_id,
        payload: {
          command_name: active.message.payload.name,
          outcome,
          elapsed_ms: this.#elapsedMs(active.startedAtMs),
          safe_detail: safeDetail,
          failure: terminalFailure
        }
      });
      const cached = this.#commandCache.get(active.message.command_id);
      if (cached !== undefined) cached.terminal = terminal;
      await this.#send(terminal);
      this.#lastTerminal = Object.freeze({
        outcome,
        failureCode: terminalFailure?.code ?? null
      });
    } finally {
      if (this.#active === active) this.#active = undefined;
    }
  }

  async #publishCurrentStatus(correlation: Correlation | null): Promise<void> {
    const state = this.#adapter.state();
    const active = this.#active;
    const activeSummary =
      active === undefined
        ? null
        : Object.freeze({
            commandId: active.message.command_id,
            commandName: active.message.payload.name
          });
    const messages: SidecarOutboundMessage[] = [
      {
        type: 'sidecar_status',
        payload: connectedSidecarStatus(
          this.#companionState,
          this.#safeUptimeSeconds()
        )
      },
      {
        type: 'minecraft_status',
        payload: minecraftStatus(state, this.#companionState)
      }
    ];
    const snapshot: SidecarOutboundMessage = {
      type: 'state_snapshot',
      payload: stateSnapshot(
        state,
        this.#companionState,
        activeSummary,
        this.#lastTerminal
      ),
      ...(correlation === null
        ? {}
        : {
            trace_id: correlation.traceId,
            command_id: correlation.commandId
          })
    };
    messages.push(snapshot);
    for (const message of messages) await this.#send(message);
  }

  #queueAdapterEvent(event: MinecraftAdapterEvent): void {
    if (!this.#acceptingEvents) return;
    this.#eventChain = this.#eventChain
      .then(() => this.#handleAdapterEvent(event))
      .catch(() => {
        this.#onDeliveryFailure();
      });
  }

  async #handleAdapterEvent(event: MinecraftAdapterEvent): Promise<void> {
    if (!this.#acceptingEvents) return;
    const active = this.#active;
    if (
      active !== undefined &&
      (active.message.payload.name === 'join' || active.message.payload.name === 'leave') &&
      ['connecting', 'spawned'].includes(event.type)
    ) {
      return;
    }
    switch (event.type) {
      case 'connecting':
      case 'spawned':
        await this.#publishCurrentStatus(null);
        return;
      case 'kicked':
      case 'error':
        this.#adapter.stopAllControls();
        return;
      case 'disconnected':
        this.#adapter.stopAllControls();
        this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DISCONNECTED';
        if (
          event.category === 'requested' &&
          active !== undefined &&
          (active.message.payload.name === 'join' || active.message.payload.name === 'leave')
        ) {
          return;
        }
        await this.#publishCurrentStatus(null);
        if (active !== undefined) {
          await this.#finish(
            active,
            'failed',
            'Minecraft server disconnected.',
            failure(
              'MINECRAFT_SERVER_DISCONNECTED',
              'Minecraft server disconnected.',
              true
            )
          );
        }
        return;
      case 'death':
        this.#adapter.stopAllControls();
        this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DEAD';
        await this.#publishCurrentStatus(null);
        if (active !== undefined) {
          await this.#finish(
            active,
            'failed',
            'Minecraft bot died.',
            failure('BOT_DEAD', 'Minecraft bot died.', false)
          );
        }
        return;
      case 'respawn':
        if (this.#adapter.state().dimension !== 'minecraft:overworld') {
          this.#adapter.stopAllControls();
          await this.#safeDisconnect();
          this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'DISCONNECTED';
        } else {
          this.#companionState = this.#emergencyStopActive ? 'STOPPED' : 'IDLE';
        }
        await this.#publishCurrentStatus(null);
        return;
      case 'health':
        await this.#publishCurrentStatus(null);
        return;
    }
  }

  #correlation(active: ActiveCommand): Correlation {
    return Object.freeze({
      traceId: active.message.trace_id,
      commandId: active.message.command_id
    });
  }

  #deadlineExpired(message: CommandMessage): boolean {
    return Date.parse(message.payload.deadline_at) <= this.#safeNow().getTime();
  }

  #cachedCommand(commandId: string): CachedCommand | undefined {
    this.#pruneCommandCache();
    return this.#commandCache.get(commandId);
  }

  #cacheCommand(message: CommandMessage, acknowledgment: CommandAckDraft): void {
    this.#pruneCommandCache();
    while (this.#commandCache.size >= this.#commandCacheCapacity) {
      const evictable = [...this.#commandCache.keys()].find(
        (commandId) => commandId !== this.#active?.message.command_id
      );
      if (evictable === undefined) return;
      this.#commandCache.delete(evictable);
    }
    this.#commandCache.set(message.command_id, {
      fingerprint: commandFingerprint(message),
      acknowledgment,
      terminal: null,
      expiresAtMs: this.#safeMonotonicNow() + this.#commandCacheTtlMs
    });
  }

  #pruneCommandCache(): void {
    const now = this.#safeMonotonicNow();
    for (const [commandId, cached] of this.#commandCache) {
      if (
        cached.expiresAtMs <= now &&
        commandId !== this.#active?.message.command_id
      ) {
        this.#commandCache.delete(commandId);
      }
    }
  }

  async #safeDisconnect(): Promise<void> {
    try {
      await this.#adapter.disconnect();
    } catch {
      // The adapter boundary already cleared controls and exposes only safe state.
    }
  }

  #safeNow(): Date {
    const value = this.#now();
    return value instanceof Date && Number.isFinite(value.getTime())
      ? value
      : new Date(0);
  }

  #safeMonotonicNow(): number {
    const value = this.#monotonicNowMs();
    return Number.isFinite(value) ? Math.max(0, value) : 0;
  }

  #safeUptimeSeconds(): number {
    const value = this.#uptimeSeconds();
    return Number.isFinite(value) ? Math.max(0, value) : 0;
  }

  #elapsedMs(startedAt: number): number {
    return Math.max(
      0,
      Math.min(86_400_000, Math.floor(this.#safeMonotonicNow() - startedAt))
    );
  }
}

function isMovementCommand(name: CommandName): boolean {
  return ['follow_owner', 'wait_here', 'come_here', 'look_at_owner'].includes(name);
}

function failure(
  code: FailureCode,
  safeDetail: string,
  retriable: boolean
): Readonly<{
  code: FailureCode;
  safe_detail: string;
  retriable: boolean;
}> {
  return Object.freeze({ code, safe_detail: safeDetail, retriable });
}

function safeDetailFor(code: FailureCode): string {
  const details: Partial<Record<FailureCode, string>> = {
    INVALID_COMMAND: 'Connection profiles are unavailable in local offline mode.',
    INVALID_STATE: 'Minecraft connection state does not allow this command.',
    EMERGENCY_STOP_ACTIVE: 'A completed leave is required before a fresh join.'
  };
  return details[code] ?? 'Command cannot be executed in the current state.';
}

function commandFingerprint(message: CommandMessage): string {
  return JSON.stringify({
    trace_id: message.trace_id,
    payload: message.payload
  });
}
