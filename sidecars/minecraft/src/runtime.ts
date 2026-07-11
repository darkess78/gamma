import {
  MinecraftControlClient,
  MinecraftControlClientError,
  type GammaControlMessage,
  type MinecraftControlClientOptions,
  type MinecraftControlDisconnect,
  type MinecraftControlClientStatus,
  type SidecarOutboundMessage
} from './control-client.js';
import type {
  MinecraftSidecarConfig,
  MinecraftSidecarRuntimeConfig
} from './config.js';
import { MinecraftCommandDispatcher } from './command-dispatcher.js';
import type { MinecraftAdapter } from './minecraft-adapter.js';
import {
  MINEFLAYER_LIBRARY_VERSION,
  MineflayerMinecraftAdapter
} from './mineflayer-runtime.js';
import type {
  CommandMessage,
  CompanionState,
  WelcomeMessage
} from './protocol.js';
import {
  connectedSidecarStatus,
  disconnectedMinecraftStatus,
  disconnectedStateSnapshot,
  runtimeStatus,
  type MinecraftSidecarExitCategory,
  type MinecraftSidecarLifecycle,
  type MinecraftSidecarRuntimeStatus
} from './status.js';

export type MinecraftSidecarRuntimeExit = Readonly<{
  category: MinecraftSidecarExitCategory;
}>;

export interface MinecraftControlClientLike {
  connect(): Promise<WelcomeMessage>;
  close(): Promise<void>;
  waitForDisconnect(): Promise<Readonly<MinecraftControlDisconnect>>;
  sendHeartbeat(companionState?: CompanionState): Promise<void>;
  sendSidecarMessage(message: SidecarOutboundMessage): Promise<void>;
  setMessageHandler(handler: ((message: GammaControlMessage) => void | Promise<void>) | undefined): void;
  status(): Readonly<MinecraftControlClientStatus>;
}

export type MinecraftRuntimeTimer = Readonly<{
  unref?: () => void;
}>;

export type MinecraftSidecarRuntimeDependencies = Readonly<{
  createControlClient?: (options: MinecraftControlClientOptions) => MinecraftControlClientLike;
  now?: () => Date;
  monotonicNowMs?: () => number;
  setInterval?: (callback: () => void, milliseconds: number) => MinecraftRuntimeTimer;
  clearInterval?: (timer: MinecraftRuntimeTimer) => void;
  createMinecraftAdapter?: () => MinecraftAdapter;
}>;

type StopDeferred = {
  promise: Promise<MinecraftSidecarExitCategory>;
  resolve: (category: MinecraftSidecarExitCategory) => void;
  settled: boolean;
};

export class MinecraftSidecarRuntime {
  readonly #config: MinecraftSidecarConfig;
  readonly #client: MinecraftControlClientLike;
  readonly #now: () => Date;
  readonly #monotonicNowMs: () => number;
  readonly #setInterval: (callback: () => void, milliseconds: number) => MinecraftRuntimeTimer;
  readonly #clearInterval: (timer: MinecraftRuntimeTimer) => void;
  readonly #stopDeferred = stopDeferred();
  readonly #dispatcher: MinecraftCommandDispatcher | undefined;

  #runPromise: Promise<MinecraftSidecarRuntimeExit> | undefined;
  #lifecycle: MinecraftSidecarLifecycle = 'idle';
  #controlReady = false;
  #companionState: CompanionState = 'DISCONNECTED';
  #heartbeatTimer: MinecraftRuntimeTimer | undefined;
  #heartbeatInFlight = false;
  #emergencyStopActive = false;
  #exitCategory: MinecraftSidecarExitCategory | null = null;
  #requestedCategory: MinecraftSidecarExitCategory | null = null;
  #startedAtMs = 0;

  constructor(
    config: MinecraftSidecarConfig | MinecraftSidecarRuntimeConfig,
    dependencies: MinecraftSidecarRuntimeDependencies = {}
  ) {
    this.#config = config;
    this.#now = dependencies.now ?? (() => new Date());
    this.#monotonicNowMs = dependencies.monotonicNowMs ?? (() => performance.now());
    this.#setInterval =
      dependencies.setInterval ??
      ((callback, milliseconds) => setInterval(callback, milliseconds));
    this.#clearInterval =
      dependencies.clearInterval ??
      ((timer) => clearInterval(timer as NodeJS.Timeout));
    const createClient =
      dependencies.createControlClient ??
      ((options: MinecraftControlClientOptions) => new MinecraftControlClient(options));
    const minecraftEnabled = isRuntimeConfig(config);
    this.#client = createClient({
      url: config.controlWebSocketUrl,
      controlToken: config.controlToken,
      hello: {
        sidecarInstanceId: config.sidecarInstanceId,
        sidecarBuild: '0.1.0',
        nodeVersion: process.versions.node,
        minecraftLibraryVersion: minecraftEnabled
          ? MINEFLAYER_LIBRARY_VERSION
          : 'not-installed',
        pathfinderVersion: 'not-installed'
      },
      now: this.#now
    });
    this.#dispatcher = undefined;
    if (minecraftEnabled) {
      const adapter =
        dependencies.createMinecraftAdapter?.() ?? new MineflayerMinecraftAdapter();
      this.#dispatcher = new MinecraftCommandDispatcher({
        config: Object.freeze({
          minecraftServerHost: config.minecraftServerHost,
          minecraftServerPort: config.minecraftServerPort,
          minecraftVersion: config.minecraftVersion,
          minecraftAccountMode: config.minecraftAccountMode,
          minecraftBotUsername: config.minecraftBotUsername
        }),
        adapter,
        send: (message) => this.#client.sendSidecarMessage(message),
        now: this.#now,
        monotonicNowMs: this.#monotonicNowMs,
        uptimeSeconds: () => this.#uptimeSeconds(),
        onDeliveryFailure: () => this.#requestStop('control_delivery_failed')
      });
    }
  }

  run(): Promise<MinecraftSidecarRuntimeExit> {
    this.#runPromise ??= this.#run();
    return this.#runPromise;
  }

  async shutdown(reason: 'signal' | 'requested' = 'requested'): Promise<void> {
    this.#requestStop(reason);
    if (this.#runPromise !== undefined) {
      await this.#dispatcher?.controlDisconnected();
      try {
        await this.#client.close();
      } catch {
        // The run loop owns the bounded exit category.
      }
      await this.#runPromise;
    }
  }

  status(): MinecraftSidecarRuntimeStatus {
    const dispatcherStatus = this.#dispatcher?.status();
    return runtimeStatus({
      lifecycle: this.#lifecycle,
      controlReady: this.#controlReady,
      companionState: dispatcherStatus?.companionState ?? this.#companionState,
      minecraftConnectionState:
        dispatcherStatus?.minecraft.connectionState ?? 'disconnected',
      heartbeatActive: this.#heartbeatTimer !== undefined,
      emergencyStopActive:
        dispatcherStatus?.emergencyStopActive ?? this.#emergencyStopActive,
      exitCategory: this.#exitCategory
    });
  }

  async #run(): Promise<MinecraftSidecarRuntimeExit> {
    this.#lifecycle = 'connecting';
    this.#startedAtMs = this.#safeMonotonicNow();
    this.#client.setMessageHandler((message) => this.#handleGammaMessage(message));
    let category: MinecraftSidecarExitCategory | null = null;
    try {
      if (this.#stopDeferred.settled) {
        category = await this.#stopDeferred.promise;
      } else {
        const welcome = await this.#client.connect();
        this.#controlReady = true;
        this.#lifecycle = 'running';
        this.#dispatcher?.beginSession(
          welcome.payload.command_cache_capacity,
          welcome.payload.command_cache_ttl_seconds
        );
        if (this.#dispatcher === undefined) {
          await this.#sendCurrentStatus();
          await this.#client.sendSidecarMessage({
            type: 'state_snapshot',
            payload: disconnectedStateSnapshot(this.#companionState)
          });
        } else {
          await this.#dispatcher.publishCurrentStatus();
        }
        this.#startHeartbeat(
          Math.min(this.#config.heartbeatSeconds, welcome.payload.heartbeat_interval_seconds)
        );
        category = await this.#waitForStopOrDisconnect();
      }
    } catch (error: unknown) {
      category = this.#categoryForFailure(error);
    } finally {
      this.#lifecycle = 'stopping';
      this.#clearHeartbeat();
      this.#client.setMessageHandler(undefined);
      await this.#dispatcher?.controlDisconnected();
      try {
        await this.#client.close();
      } catch {
        category ??= 'control_disconnected';
      }
      this.#controlReady = false;
      this.#lifecycle = 'stopped';
    }
    this.#exitCategory = category ?? 'control_disconnected';
    return Object.freeze({ category: this.#exitCategory });
  }

  async #waitForStopOrDisconnect(): Promise<MinecraftSidecarExitCategory> {
    const stopped = this.#stopDeferred.promise.then((category) => ({
      source: 'stop' as const,
      category
    }));
    const disconnected = this.#client.waitForDisconnect().then((disconnect) => ({
      source: 'disconnect' as const,
      disconnect
    }));
    const result = await Promise.race([stopped, disconnected]);
    if (result.source === 'stop') return result.category;
    return result.disconnect.kind === 'protocol_error'
      ? 'protocol_error'
      : 'control_disconnected';
  }

  async #handleGammaMessage(message: GammaControlMessage): Promise<void> {
    const dispatcher = this.#dispatcher;
    if (dispatcher !== undefined) {
      switch (message.type) {
        case 'command':
          void dispatcher.handleCommand(message);
          return;
        case 'cancel_command':
          void dispatcher.cancelCommand(message);
          return;
        case 'emergency_stop':
          void dispatcher.emergencyStop(message);
          return;
        case 'shutdown':
          this.#requestStop('gamma_shutdown');
          return;
        case 'protocol_error':
          this.#requestStop('protocol_error');
          return;
      }
    }
    switch (message.type) {
      case 'command':
        await this.#handleCommand(message);
        return;
      case 'emergency_stop':
        this.#activateEmergencyStop();
        await this.#sendCurrentStatus();
        await this.#client.sendSidecarMessage({
          type: 'state_snapshot',
          trace_id: message.trace_id,
          command_id: message.command_id,
          payload: disconnectedStateSnapshot(this.#companionState)
        });
        return;
      case 'shutdown':
        this.#requestStop('gamma_shutdown');
        return;
      case 'protocol_error':
        this.#requestStop('protocol_error');
        return;
      case 'cancel_command':
        return;
    }
  }

  async #handleCommand(message: CommandMessage): Promise<void> {
    if (message.payload.name === 'emergency_stop') {
      await this.#handleEmergencyStopCommand(message);
      return;
    }
    if (message.payload.name !== 'report_status') {
      await this.#sendRejectedAck(
        message,
        'SAFETY_POLICY_BLOCKED',
        'Command execution is unavailable in the runtime shell.'
      );
      return;
    }
    if (Date.parse(message.payload.deadline_at) <= this.#safeNow().getTime()) {
      await this.#sendRejectedAck(
        message,
        'DEADLINE_EXCEEDED',
        'Command deadline has passed.'
      );
      return;
    }

    const startedAt = this.#safeMonotonicNow();
    await this.#client.sendSidecarMessage({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: true,
        command_name: 'report_status',
        failure: null
      }
    });
    await this.#sendCurrentStatus();
    await this.#client.sendSidecarMessage({
      type: 'state_snapshot',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: disconnectedStateSnapshot(this.#companionState, {
        commandId: message.command_id,
        commandName: 'report_status'
      })
    });
    await this.#client.sendSidecarMessage({
      type: 'terminal_result',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        command_name: 'report_status',
        outcome: 'completed',
        elapsed_ms: this.#elapsedMs(startedAt),
        safe_detail: 'Disconnected Minecraft status reported.',
        failure: null
      }
    });
  }

  async #handleEmergencyStopCommand(message: CommandMessage): Promise<void> {
    this.#activateEmergencyStop();
    const startedAt = this.#safeMonotonicNow();
    await this.#client.sendSidecarMessage({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: true,
        command_name: 'emergency_stop',
        failure: null
      }
    });
    await this.#sendCurrentStatus();
    await this.#client.sendSidecarMessage({
      type: 'state_snapshot',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: disconnectedStateSnapshot('STOPPED', {
        commandId: message.command_id,
        commandName: 'emergency_stop'
      })
    });
    await this.#client.sendSidecarMessage({
      type: 'terminal_result',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        command_name: 'emergency_stop',
        outcome: 'completed',
        elapsed_ms: this.#elapsedMs(startedAt),
        safe_detail: 'Local non-moving emergency state confirmed.',
        failure: null
      }
    });
  }

  async #sendRejectedAck(
    message: CommandMessage,
    code: 'SAFETY_POLICY_BLOCKED' | 'DEADLINE_EXCEEDED',
    safeDetail: string
  ): Promise<void> {
    await this.#client.sendSidecarMessage({
      type: 'command_ack',
      trace_id: message.trace_id,
      command_id: message.command_id,
      payload: {
        accepted: false,
        command_name: message.payload.name,
        failure: {
          code,
          safe_detail: safeDetail,
          retriable: false
        }
      }
    });
  }

  async #sendCurrentStatus(): Promise<void> {
    const messages: readonly SidecarOutboundMessage[] = [
      {
        type: 'sidecar_status',
        payload: connectedSidecarStatus(this.#companionState, this.#uptimeSeconds())
      },
      {
        type: 'minecraft_status',
        payload: disconnectedMinecraftStatus(this.#companionState)
      }
    ];
    for (const message of messages) await this.#client.sendSidecarMessage(message);
  }

  #activateEmergencyStop(): void {
    this.#emergencyStopActive = true;
    this.#companionState = 'STOPPED';
  }

  #startHeartbeat(seconds: number): void {
    if (this.#heartbeatTimer !== undefined) return;
    const timer = this.#setInterval(() => {
      void this.#heartbeatTick();
    }, seconds * 1_000);
    timer.unref?.();
    this.#heartbeatTimer = timer;
  }

  async #heartbeatTick(): Promise<void> {
    if (this.#heartbeatInFlight || this.#lifecycle !== 'running') return;
    this.#heartbeatInFlight = true;
    try {
      await this.#client.sendHeartbeat(
        this.#dispatcher?.status().companionState ?? this.#companionState
      );
    } catch {
      this.#requestStop('control_delivery_failed');
    } finally {
      this.#heartbeatInFlight = false;
    }
  }

  #clearHeartbeat(): void {
    const timer = this.#heartbeatTimer;
    if (timer !== undefined) {
      this.#clearInterval(timer);
      this.#heartbeatTimer = undefined;
    }
  }

  #requestStop(category: MinecraftSidecarExitCategory): void {
    if (this.#stopDeferred.settled) return;
    this.#stopDeferred.settled = true;
    this.#requestedCategory = category;
    if (this.#lifecycle === 'connecting' || this.#lifecycle === 'running') {
      this.#lifecycle = 'stopping';
    }
    this.#stopDeferred.resolve(category);
  }

  #categoryForFailure(error: unknown): MinecraftSidecarExitCategory {
    if (this.#stopDeferred.settled) {
      return this.#requestedCategory ?? 'requested';
    }
    if (error instanceof MinecraftControlClientError) {
      if (error.category === 'delivery_failed') return 'control_delivery_failed';
      if (error.category === 'protocol_violation') return 'protocol_error';
    }
    return this.#controlReady ? 'control_delivery_failed' : 'control_unavailable';
  }

  #safeNow(): Date {
    const value = this.#now();
    if (!(value instanceof Date) || !Number.isFinite(value.getTime())) return new Date(0);
    return value;
  }

  #safeMonotonicNow(): number {
    const value = this.#monotonicNowMs();
    return Number.isFinite(value) ? Math.max(0, value) : 0;
  }

  #uptimeSeconds(): number {
    return Math.max(0, (this.#safeMonotonicNow() - this.#startedAtMs) / 1_000);
  }

  #elapsedMs(startedAt: number): number {
    return Math.max(
      0,
      Math.min(86_400_000, Math.floor(this.#safeMonotonicNow() - startedAt))
    );
  }
}

function stopDeferred(): StopDeferred {
  let resolvePromise!: (category: MinecraftSidecarExitCategory) => void;
  const promise = new Promise<MinecraftSidecarExitCategory>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise, settled: false };
}

function isRuntimeConfig(
  config: MinecraftSidecarConfig | MinecraftSidecarRuntimeConfig
): config is MinecraftSidecarRuntimeConfig {
  return (
    'minecraftServerHost' in config &&
    'minecraftServerPort' in config &&
    'minecraftVersion' in config &&
    'minecraftAccountMode' in config &&
    'minecraftBotUsername' in config
  );
}
