import { createRequire } from 'node:module';

import {
  disconnectedMinecraftAdapterState,
  type ForwardSafety,
  type MinecraftAdapterConnectionConfig,
  type MinecraftAdapterEvent,
  type MinecraftAdapterEventHandler,
  type MinecraftAdapterState,
  type MinecraftDimension,
  type MinecraftDisconnectCategory,
  type MinecraftMovementAdapter,
  type ObservedPlayer,
  type SafePosition
} from './minecraft-adapter.js';
import {
  classifyDirectSteeringSpace,
  classifyDirectSteeringSupport,
  type DirectSteeringBlock
} from './safety.js';

export const MINEFLAYER_LIBRARY_VERSION = '4.37.1' as const;

const OVERWORLD: MinecraftDimension = 'minecraft:overworld';
const DIRECT_STEP_DISTANCE = 0.45;
const PLAYER_HALF_WIDTH = 0.31;
const POSITION_EPSILON = 0.05;

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
  createVector?: (x: number, y: number, z: number) => unknown;
  setTimeout?: (
    callback: () => void,
    milliseconds: number
  ) => Readonly<{ unref?: () => void }>;
  clearTimeout?: (timer: Readonly<{ unref?: () => void }>) => void;
}>;

type MineflayerPosition = Readonly<{ x: number; y: number; z: number }>;

type MineflayerBlock = DirectSteeringBlock;

type MineflayerPlayer = Readonly<{
  username?: unknown;
  entity?: Readonly<{ position?: MineflayerPosition }>;
}>;

type MineflayerBot = {
  version?: unknown;
  entity?: Readonly<{ position?: MineflayerPosition }>;
  game?: Readonly<{ dimension?: unknown }>;
  health?: unknown;
  food?: unknown;
  targetDigBlock?: unknown;
  usingHeldItem?: boolean;
  players?: Readonly<Record<string, MineflayerPlayer | undefined>>;
  _client: Readonly<{
    socket?: Readonly<{ destroy: () => void }>;
  }>;
  on: (event: string, listener: (...args: unknown[]) => void) => void;
  once: (event: string, listener: (...args: unknown[]) => void) => void;
  off: (event: string, listener: (...args: unknown[]) => void) => void;
  quit: () => void;
  end: () => void;
  clearControlStates: () => void;
  setControlState?: (control: string, active: boolean) => void;
  stopDigging: () => void;
  deactivateItem: () => void;
  lookAt?: (position: unknown, force?: boolean) => Promise<void>;
  blockAt?: (position: unknown, extraInfo?: boolean) => MineflayerBlock | null;
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

type MovementListener = {
  bot: MineflayerBot;
  wrapped: () => void;
  active: boolean;
};

export class MineflayerMinecraftAdapter implements MinecraftMovementAdapter {
  readonly #configuredCreateBot:
    | ((options: Readonly<Record<string, unknown>>) => unknown)
    | undefined;
  readonly #createVector:
    | ((x: number, y: number, z: number) => unknown)
    | undefined;
  readonly #setTimeout: (
    callback: () => void,
    milliseconds: number
  ) => Readonly<{ unref?: () => void }>;
  readonly #clearTimeout: (timer: Readonly<{ unref?: () => void }>) => void;
  readonly #movementListeners = new Set<MovementListener>();

  #active: ActiveBot | undefined;
  #generation = 0;
  #handler: MinecraftAdapterEventHandler | undefined;
  #state: MinecraftAdapterState = disconnectedMinecraftAdapterState();
  #pendingClose: Promise<void> | undefined;

  constructor(dependencies: MineflayerRuntimeDependencies = {}) {
    this.#configuredCreateBot = dependencies.createBot;
    this.#createVector = dependencies.createVector;
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
    if (this.#pendingClose !== undefined) await this.#pendingClose;
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
        this.#detachMovementListeners(bot);
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
          this.#detachMovementListeners(bot);
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
      this.#active = {
        bot,
        generation,
        handlers,
        errorSink,
        settleConnection
      };
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
    this.#detachMovementListeners(active.bot);
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

  clearControls(): void {
    const bot = this.#active?.bot;
    if (bot === undefined) return;
    this.#tryClearControls(bot);
  }

  setForward(active: boolean): void {
    const bot = this.#active?.bot;
    if (bot === undefined || typeof bot.setControlState !== 'function') return;
    if (!this.#tryClearControls(bot)) return;
    if (!active) return;
    try {
      bot.setControlState('forward', true);
    } catch {
      this.clearControls();
    }
  }

  getBotPosition(): SafePosition | undefined {
    return safePosition(this.#active?.bot.entity?.position);
  }

  getDimension(): MinecraftDimension | undefined {
    return minecraftDimension(this.#active?.bot.game?.dimension) ?? undefined;
  }

  getPlayer(username: string): ObservedPlayer | undefined {
    const active = this.#active;
    const dimension = this.getDimension();
    if (
      active === undefined ||
      this.#state.connectionState !== 'connected' ||
      dimension === undefined
    ) {
      return undefined;
    }
    const player = findPlayer(active.bot, username);
    const position = safePosition(player?.entity?.position);
    if (player === undefined || position === undefined) return undefined;
    return Object.freeze({
      username: player.username as string,
      position,
      dimension
    });
  }

  async lookAt(position: SafePosition): Promise<void> {
    const active = this.#active;
    if (
      active === undefined ||
      typeof active.bot.lookAt !== 'function' ||
      !finitePosition(position)
    ) {
      throw new MinecraftAdapterError('invalid_state');
    }
    if (!this.#tryClearControls(active.bot)) {
      throw new MinecraftAdapterError('invalid_state');
    }
    await active.bot.lookAt(
      this.#vector(position.x, position.y, position.z),
      false
    );
  }

  inspectForwardStep(target: ObservedPlayer): ForwardSafety {
    const bot = this.#active?.bot;
    const botPosition = this.getBotPosition();
    const dimension = this.getDimension();
    if (
      bot === undefined ||
      botPosition === undefined ||
      dimension === undefined ||
      dimension !== OVERWORLD ||
      target.dimension !== dimension
    ) {
      return Object.freeze({ kind: 'dimension_mismatch' });
    }
    if (!finitePosition(target.position)) {
      return Object.freeze({ kind: 'unloaded' });
    }
    if (Math.abs(botPosition.y - Math.round(botPosition.y)) > POSITION_EPSILON) {
      return Object.freeze({ kind: 'unsupported_drop' });
    }
    const dx = target.position.x - botPosition.x;
    const dz = target.position.z - botPosition.z;
    const horizontalDistance = Math.hypot(dx, dz);
    if (!Number.isFinite(horizontalDistance) || horizontalDistance <= 0) {
      return Object.freeze({ kind: 'blocked' });
    }
    const step = Math.min(DIRECT_STEP_DISTANCE, horizontalDistance);
    const candidate = freezePosition({
      x: botPosition.x + (dx / horizontalDistance) * step,
      y: Math.round(botPosition.y),
      z: botPosition.z + (dz / horizontalDistance) * step
    });
    if (!finitePosition(candidate) || typeof bot.blockAt !== 'function') {
      return Object.freeze({ kind: 'unloaded' });
    }

    for (const sample of candidateSamples(candidate)) {
      const feet = this.#blockAt(bot, sample.x, candidate.y, sample.z);
      const head = this.#blockAt(bot, sample.x, candidate.y + 1, sample.z);
      const support = this.#blockAt(bot, sample.x, candidate.y - 1, sample.z);
      const feetResult = classifyDirectSteeringSpace(feet);
      if (feetResult !== 'safe') return Object.freeze({ kind: feetResult });
      const headResult = classifyDirectSteeringSpace(head);
      if (headResult !== 'safe') return Object.freeze({ kind: headResult });
      const supportResult = classifyDirectSteeringSupport(support);
      if (supportResult !== 'safe') return Object.freeze({ kind: supportResult });
    }
    return Object.freeze({ kind: 'safe', candidate });
  }

  onMovementTick(handler: () => void): () => void {
    const bot = this.#active?.bot;
    if (bot === undefined) throw new MinecraftAdapterError('invalid_state');
    if ([...this.#movementListeners].some((listener) => listener.bot === bot)) {
      this.#tryClearControls(bot);
      throw new MinecraftAdapterError('invalid_state');
    }
    const listener: MovementListener = {
      bot,
      active: true,
      wrapped: () => {
        if (!listener.active || this.#active?.bot !== bot) return;
        try {
          handler();
        } catch {
          this.clearControls();
          listener.active = false;
          listener.bot.off('physicsTick', listener.wrapped);
          this.#movementListeners.delete(listener);
          this.#emit({ type: 'error' });
        }
      }
    };
    this.#movementListeners.add(listener);
    bot.on('physicsTick', listener.wrapped);
    return () => {
      if (!listener.active) return;
      listener.active = false;
      listener.bot.off('physicsTick', listener.wrapped);
      this.#movementListeners.delete(listener);
    };
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

  #detachMovementListeners(bot: MineflayerBot): void {
    for (const listener of [...this.#movementListeners]) {
      if (listener.bot !== bot) continue;
      listener.active = false;
      listener.bot.off('physicsTick', listener.wrapped);
      this.#movementListeners.delete(listener);
    }
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
          // The fixed fallback still bounds local transport cleanup.
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
    this.#tryClearControls(bot);
    if (bot.targetDigBlock != null) {
      try {
        bot.stopDigging();
      } catch {
        // Stopping a pre-existing action is safe cleanup.
      }
    }
    if (bot.usingHeldItem) {
      try {
        bot.deactivateItem();
      } catch {
        // Stopping a pre-existing action is safe cleanup.
      }
    }
  }

  #tryClearControls(bot: MineflayerBot): boolean {
    try {
      bot.clearControlStates();
      return true;
    } catch {
      // Clearing is best-effort at a failed or already-ended transport boundary.
      return false;
    }
  }

  #readBotState(
    bot: MineflayerBot,
    spawned: boolean,
    alive: boolean
  ): MinecraftAdapterState {
    const position = safePosition(bot.entity?.position);
    const roundedPosition =
      position === undefined
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

  #blockAt(
    bot: MineflayerBot,
    x: number,
    y: number,
    z: number
  ): MineflayerBlock | null {
    try {
      return bot.blockAt?.(
        this.#vector(Math.floor(x), Math.floor(y), Math.floor(z)),
        false
      ) ?? null;
    } catch {
      return null;
    }
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

  #loadVec3(): new (x: number, y: number, z: number) => unknown {
    const require = createRequire(import.meta.url);
    const module = require('vec3') as Readonly<{
      Vec3: new (x: number, y: number, z: number) => unknown;
    }>;
    return module.Vec3;
  }

  #vector(x: number, y: number, z: number): unknown {
    if (this.#createVector !== undefined) return this.#createVector(x, y, z);
    const Vec3 = this.#loadVec3();
    return new Vec3(x, y, z);
  }

  #asBot(value: unknown): MineflayerBot {
    if (typeof value !== 'object' || value === null) {
      throw new MinecraftAdapterError('connection_failed');
    }
    return value as MineflayerBot;
  }
}

function minecraftDimension(value: unknown): MinecraftDimension | null {
  if (value === 'overworld' || value === OVERWORLD) return OVERWORLD;
  if (value === 'the_nether' || value === 'minecraft:the_nether') {
    return 'minecraft:the_nether';
  }
  if (value === 'the_end' || value === 'minecraft:the_end') {
    return 'minecraft:the_end';
  }
  return null;
}

function findPlayer(
  bot: MineflayerBot,
  username: string
): MineflayerPlayer | undefined {
  const normalized = normalizeUsername(username);
  if (normalized === undefined) return undefined;
  const matches = Object.values(bot.players ?? {}).filter((player) => {
    return (
      typeof player?.username === 'string' &&
      normalizeUsername(player.username) === normalized
    );
  });
  return matches.length === 1 ? matches[0] : undefined;
}

function normalizeUsername(value: string): string | undefined {
  if (!/^[A-Za-z0-9_]{3,16}$/u.test(value)) return undefined;
  return value.toLowerCase();
}

function safePosition(value: MineflayerPosition | undefined): SafePosition | undefined {
  if (value === undefined || !finitePosition(value)) return undefined;
  return freezePosition(value);
}

function freezePosition(value: SafePosition): SafePosition {
  return Object.freeze({ x: value.x, y: value.y, z: value.z });
}

function finitePosition(value: SafePosition): boolean {
  return Number.isFinite(value.x) && Number.isFinite(value.y) && Number.isFinite(value.z);
}

function candidateSamples(candidate: SafePosition): readonly SafePosition[] {
  return [
    candidate,
    freezePosition({
      x: candidate.x - PLAYER_HALF_WIDTH,
      y: candidate.y,
      z: candidate.z - PLAYER_HALF_WIDTH
    }),
    freezePosition({
      x: candidate.x - PLAYER_HALF_WIDTH,
      y: candidate.y,
      z: candidate.z + PLAYER_HALF_WIDTH
    }),
    freezePosition({
      x: candidate.x + PLAYER_HALF_WIDTH,
      y: candidate.y,
      z: candidate.z - PLAYER_HALF_WIDTH
    }),
    freezePosition({
      x: candidate.x + PLAYER_HALF_WIDTH,
      y: candidate.y,
      z: candidate.z + PLAYER_HALF_WIDTH
    })
  ];
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
