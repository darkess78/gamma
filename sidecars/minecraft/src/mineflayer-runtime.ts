import { createRequire } from 'node:module';

import {
  disconnectedMinecraftAdapterState,
  type MinecraftAdapter,
  type MinecraftAdapterConnectionConfig,
  type MinecraftAdapterEvent,
  type MinecraftAdapterEventHandler,
  type MinecraftAdapterState,
  type MinecraftDimension,
  type MinecraftDisconnectCategory
} from './minecraft-adapter.js';

export const MINEFLAYER_LIBRARY_VERSION = '4.37.1' as const;

export type MinecraftAdapterFailureCategory =
  | 'invalid_state'
  | 'connection_failed'
  | 'aborted';

export class MinecraftAdapterError extends Error {
  readonly category: MinecraftAdapterFailureCategory;

  constructor(category: MinecraftAdapterFailureCategory) {
    super('Minecraft adapter operation failed');
    this.name = 'MinecraftAdapterError';
    this.category = category;
  }
}

export type MineflayerRuntimeDependencies = Readonly<{
  createBot?: (options: Readonly<Record<string, unknown>>) => unknown;
  setTimeout?: (
    callback: () => void,
    milliseconds: number
  ) => Readonly<{ unref?: () => void }>;
  clearTimeout?: (timer: Readonly<{ unref?: () => void }>) => void;
}>;

type MineflayerBot = {
  version?: unknown;
  entity?: Readonly<{
    position?: Readonly<{ x: number; y: number; z: number }>;
  }>;
  game?: Readonly<{ dimension?: unknown }>;
  health?: unknown;
  food?: unknown;
  targetDigBlock?: unknown;
  usingHeldItem?: boolean;
  _client: Readonly<{
    socket?: Readonly<{ destroy: () => void }>;
  }>;
  on: (event: string, listener: (...args: unknown[]) => void) => void;
  once: (event: string, listener: (...args: unknown[]) => void) => void;
  off: (event: string, listener: (...args: unknown[]) => void) => void;
  quit: () => void;
  end: () => void;
  clearControlStates: () => void;
  stopDigging: () => void;
  deactivateItem: () => void;
};

type BotHandlers = Readonly<{
  spawn: () => void;
  kicked: () => void;
  error: () => void;
  end: () => void;
  death: () => void;
  respawn: () => void;
  health: () => void;
}>;

type ActiveBot = {
  bot: MineflayerBot;
  generation: number;
  handlers: BotHandlers;
  errorSink: () => void;
  settleConnection: (error?: MinecraftAdapterError) => void;
};

export class MineflayerMinecraftAdapter implements MinecraftAdapter {
  readonly #configuredCreateBot:
    | ((options: Readonly<Record<string, unknown>>) => unknown)
    | undefined;
  readonly #setTimeout: (
    callback: () => void,
    milliseconds: number
  ) => Readonly<{ unref?: () => void }>;
  readonly #clearTimeout: (timer: Readonly<{ unref?: () => void }>) => void;

  #active: ActiveBot | undefined;
  #generation = 0;
  #handler: MinecraftAdapterEventHandler | undefined;
  #state: MinecraftAdapterState = disconnectedMinecraftAdapterState();
  #pendingClose: Promise<void> | undefined;

  constructor(dependencies: MineflayerRuntimeDependencies = {}) {
    this.#configuredCreateBot = dependencies.createBot;
    this.#setTimeout =
      dependencies.setTimeout ??
      ((callback, milliseconds) => setTimeout(callback, milliseconds));
    this.#clearTimeout =
      dependencies.clearTimeout ??
      ((timer) => clearTimeout(timer as NodeJS.Timeout));
  }

  async connect(
    config: MinecraftAdapterConnectionConfig,
    signal: AbortSignal
  ): Promise<void> {
    if (this.#active !== undefined || this.#state.connectionState !== 'disconnected') {
      throw new MinecraftAdapterError('invalid_state');
    }
    if (signal.aborted) throw new MinecraftAdapterError('aborted');

    const generation = ++this.#generation;
    this.#state = Object.freeze({
      ...disconnectedMinecraftAdapterState(),
      connectionState: 'connecting'
    });
    this.#emit({ type: 'connecting' });

    let bot: MineflayerBot;
    try {
      bot = this.#asBot(this.#createBot()({
        host: config.minecraftServerHost,
        port: config.minecraftServerPort,
        username: config.minecraftBotUsername,
        version: config.minecraftVersion,
        auth: config.minecraftAccountMode,
        profilesFolder: false,
        chat: 'disabled',
        defaultChatPatterns: false,
        hideErrors: true,
        logErrors: false,
        respawn: true
      }));
    } catch {
      this.#state = disconnectedMinecraftAdapterState('error');
      this.#emit({ type: 'error' });
      this.#emit({ type: 'disconnected', category: 'error' });
      throw new MinecraftAdapterError('connection_failed');
    }

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let hasSpawned = false;
      let respawnPending = false;
      const onAbort = (): void => {
        terminate('requested', new MinecraftAdapterError('aborted'), true);
      };
      const settleConnection = (error?: MinecraftAdapterError): void => {
        if (settled) return;
        settled = true;
        signal.removeEventListener('abort', onAbort);
        if (error === undefined) resolve();
        else reject(error);
      };
      const terminate = (
        category: MinecraftDisconnectCategory,
        error: MinecraftAdapterError,
        requestEnd: boolean
      ): void => {
        if (!this.#isCurrent(bot, generation)) {
          settleConnection(error);
          return;
        }
        this.#stopBotControls(bot);
        const current = this.#active;
        this.#detach(current);
        this.#active = undefined;
        ++this.#generation;
        if (requestEnd) {
          this.#trackPendingClose(this.#requestBotEnd(bot, current?.errorSink));
        } else if (current !== undefined) {
          bot.off('error', current.errorSink);
        }
        this.#state = disconnectedMinecraftAdapterState(category);
        this.#emit({ type: 'disconnected', category });
        settleConnection(error);
      };
      const handlers: BotHandlers = {
        spawn: () => {
          if (!this.#isCurrent(bot, generation)) return;
          this.#state = this.#readBotState(bot, true, true);
          if (hasSpawned || respawnPending) this.#emit({ type: 'respawn' });
          else this.#emit({ type: 'spawned' });
          hasSpawned = true;
          respawnPending = false;
          settleConnection();
        },
        kicked: () => {
          if (!this.#isCurrent(bot, generation)) return;
          this.#emit({ type: 'kicked' });
          terminate('kicked', new MinecraftAdapterError('connection_failed'), true);
        },
        error: () => {
          if (!this.#isCurrent(bot, generation)) return;
          this.#emit({ type: 'error' });
          terminate('error', new MinecraftAdapterError('connection_failed'), true);
        },
        end: () => {
          if (!this.#isCurrent(bot, generation)) return;
          terminate('ended', new MinecraftAdapterError('connection_failed'), false);
        },
        death: () => {
          if (!this.#isCurrent(bot, generation)) return;
          this.#stopBotControls(bot);
          this.#state = this.#readBotState(bot, true, false);
          this.#emit({ type: 'death' });
        },
        respawn: () => {
          if (!this.#isCurrent(bot, generation)) return;
          respawnPending = true;
          this.#state = this.#readBotState(bot, true, false);
        },
        health: () => {
          if (!this.#isCurrent(bot, generation)) return;
          this.#state = this.#readBotState(
            bot,
            this.#state.spawned,
            this.#state.alive
          );
          this.#emit({ type: 'health' });
        }
      };
      const errorSink = (): void => undefined;
      this.#active = { bot, generation, handlers, errorSink, settleConnection };
      this.#attach(bot, handlers, errorSink);
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  }

  async disconnect(): Promise<void> {
    const active = this.#active;
    if (active === undefined) {
      await this.#pendingClose;
      return;
    }
    ++this.#generation;
    this.#stopBotControls(active.bot);
    this.#detach(active);
    this.#active = undefined;
    this.#state = disconnectedMinecraftAdapterState('requested');
    active.settleConnection(new MinecraftAdapterError('aborted'));
    this.#emit({ type: 'disconnected', category: 'requested' });
    const pending = this.#requestBotEnd(active.bot, active.errorSink);
    this.#trackPendingClose(pending);
    await pending;
  }

  stopAllControls(): void {
    const bot = this.#active?.bot;
    if (bot !== undefined) this.#stopBotControls(bot);
  }

  state(): MinecraftAdapterState {
    const bot = this.#active?.bot;
    if (bot !== undefined && this.#state.spawned) {
      this.#state = this.#readBotState(bot, true, this.#state.alive);
    }
    return Object.freeze({
      ...this.#state,
      roundedPosition:
        this.#state.roundedPosition === null
          ? null
          : Object.freeze({ ...this.#state.roundedPosition })
    });
  }

  setEventHandler(handler: MinecraftAdapterEventHandler | undefined): void {
    this.#handler = handler;
  }

  #isCurrent(bot: MineflayerBot, generation: number): boolean {
    return (
      this.#generation === generation &&
      this.#active?.bot === bot &&
      this.#active.generation === generation
    );
  }

  #attach(
    bot: MineflayerBot,
    handlers: BotHandlers,
    errorSink: () => void
  ): void {
    bot.on('error', errorSink);
    bot.on('spawn', handlers.spawn);
    bot.on('kicked', handlers.kicked);
    bot.on('error', handlers.error);
    bot.on('end', handlers.end);
    bot.on('death', handlers.death);
    bot.on('respawn', handlers.respawn);
    bot.on('health', handlers.health);
  }

  #detach(active: ActiveBot | undefined): void {
    if (active === undefined) return;
    const { bot, handlers } = active;
    bot.off('spawn', handlers.spawn);
    bot.off('kicked', handlers.kicked);
    bot.off('error', handlers.error);
    bot.off('end', handlers.end);
    bot.off('death', handlers.death);
    bot.off('respawn', handlers.respawn);
    bot.off('health', handlers.health);
  }

  #requestBotEnd(
    bot: MineflayerBot,
    errorSink: (() => void) | undefined
  ): Promise<void> {
    return new Promise<void>((resolve) => {
      let settled = false;
      let forcedClose: Readonly<{ unref?: () => void }> | undefined;
      const finish = (): void => {
        if (settled) return;
        settled = true;
        if (forcedClose !== undefined) this.#clearTimeout(forcedClose);
        bot.off('end', finish);
        if (errorSink !== undefined) bot.off('error', errorSink);
        resolve();
      };
      bot.once('end', finish);
      try {
        bot.quit();
      } catch {
        try {
          bot.end();
        } catch {
          // The forced close below still bounds local transport cleanup.
        }
      }
      if (settled) return;
      forcedClose = this.#setTimeout(() => {
        try {
          bot._client.socket?.destroy();
        } catch {
          // The fixed fallback only guarantees local transport cleanup.
        }
        finish();
      }, 1_000);
      forcedClose.unref?.();
    });
  }

  #trackPendingClose(pending: Promise<void>): void {
    this.#pendingClose = pending;
    void pending.finally(() => {
      if (this.#pendingClose === pending) this.#pendingClose = undefined;
    });
  }

  #stopBotControls(bot: MineflayerBot): void {
    try {
      bot.clearControlStates();
    } catch {
      // Clearing is best-effort at a failed or already-ended transport boundary.
    }
    if (bot.targetDigBlock != null) {
      try {
        bot.stopDigging();
      } catch {
        // Stop every active low-level action even when another stop failed.
      }
    }
    if (bot.usingHeldItem) {
      try {
        bot.deactivateItem();
      } catch {
        // Item use is never allowed to continue after a stop request.
      }
    }
  }

  #readBotState(
    bot: MineflayerBot,
    spawned: boolean,
    alive: boolean
  ): MinecraftAdapterState {
    const position = bot.entity?.position;
    const roundedPosition =
      position === undefined ||
      !Number.isFinite(position.x) ||
      !Number.isFinite(position.y) ||
      !Number.isFinite(position.z)
        ? null
        : Object.freeze({
            x: boundedInteger(position.x, -30_000_000, 30_000_000),
            y: boundedInteger(position.y, -2_048, 2_048),
            z: boundedInteger(position.z, -30_000_000, 30_000_000)
          });
    return Object.freeze({
      connectionState: 'connected',
      spawned,
      alive,
      negotiatedVersion:
        typeof bot.version === 'string' &&
        bot.version.length <= 32 &&
        /^[A-Za-z0-9][A-Za-z0-9.+_-]*$/u.test(bot.version)
          ? bot.version
          : null,
      dimension: minecraftDimension(bot.game?.dimension),
      roundedPosition,
      health: boundedNumber(bot.health, 0, 20),
      hunger: boundedIntegerOrNull(bot.food, 0, 20),
      lastDisconnectCategory: null
    });
  }

  #emit(event: MinecraftAdapterEvent): void {
    try {
      this.#handler?.(Object.freeze(event));
    } catch {
      // Adapter observers cannot destabilize the Mineflayer event loop.
    }
  }

  #createBot(): (options: Readonly<Record<string, unknown>>) => unknown {
    if (this.#configuredCreateBot !== undefined) return this.#configuredCreateBot;
    const require = createRequire(import.meta.url);
    const module = require('mineflayer') as Readonly<{
      createBot: (options: Readonly<Record<string, unknown>>) => unknown;
    }>;
    return module.createBot;
  }

  #asBot(value: unknown): MineflayerBot {
    if (typeof value !== 'object' || value === null) {
      throw new MinecraftAdapterError('connection_failed');
    }
    return value as MineflayerBot;
  }
}

function minecraftDimension(value: unknown): MinecraftDimension | null {
  if (value === 'overworld' || value === 'minecraft:overworld') {
    return 'minecraft:overworld';
  }
  if (value === 'the_nether' || value === 'minecraft:the_nether') {
    return 'minecraft:the_nether';
  }
  if (value === 'the_end' || value === 'minecraft:the_end') {
    return 'minecraft:the_end';
  }
  return null;
}

function boundedInteger(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, Math.round(value)));
}

function boundedIntegerOrNull(
  value: unknown,
  minimum: number,
  maximum: number
): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return boundedInteger(value, minimum, maximum);
}

function boundedNumber(
  value: unknown,
  minimum: number,
  maximum: number
): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.max(minimum, Math.min(maximum, value));
}
