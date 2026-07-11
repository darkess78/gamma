import type { MinecraftSidecarRuntimeConfig } from './config.js';

export type MinecraftConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'connected';

export type MinecraftDimension =
  | 'minecraft:overworld'
  | 'minecraft:the_nether'
  | 'minecraft:the_end';

export type MinecraftDisconnectCategory =
  | 'requested'
  | 'ended'
  | 'kicked'
  | 'error';

export type SafePosition = Readonly<{
  x: number;
  y: number;
  z: number;
}>;

export type MinecraftRoundedPosition = SafePosition;

export type ObservedPlayer = Readonly<{
  username: string;
  position: SafePosition;
  dimension: MinecraftDimension;
}>;

export type MinecraftOwnerState = Readonly<{
  username: string;
  uuid: null;
  roundedPosition: MinecraftRoundedPosition;
  distance: number;
  sameDimension: boolean;
}>;

export type ForwardSafety =
  | Readonly<{ kind: 'safe'; candidate: SafePosition }>
  | Readonly<{ kind: 'blocked' }>
  | Readonly<{ kind: 'unsupported_drop' }>
  | Readonly<{ kind: 'hazard' }>
  | Readonly<{ kind: 'liquid' }>
  | Readonly<{ kind: 'unloaded' }>
  | Readonly<{ kind: 'dimension_mismatch' }>;

export type MinecraftAdapterState = Readonly<{
  connectionState: MinecraftConnectionState;
  spawned: boolean;
  alive: boolean;
  negotiatedVersion: string | null;
  dimension: MinecraftDimension | null;
  roundedPosition: MinecraftRoundedPosition | null;
  health: number | null;
  hunger: number | null;
  lastDisconnectCategory: MinecraftDisconnectCategory | null;
}>;

export type MinecraftAdapterEvent =
  | Readonly<{ type: 'connecting' }>
  | Readonly<{ type: 'spawned' }>
  | Readonly<{ type: 'disconnected'; category: MinecraftDisconnectCategory }>
  | Readonly<{ type: 'kicked' }>
  | Readonly<{ type: 'error' }>
  | Readonly<{ type: 'death' }>
  | Readonly<{ type: 'respawn' }>
  | Readonly<{ type: 'health' }>;

export type MinecraftAdapterEventHandler = (
  event: MinecraftAdapterEvent
) => void;

export type MinecraftAdapterConnectionConfig = Pick<
  MinecraftSidecarRuntimeConfig,
  | 'minecraftServerHost'
  | 'minecraftServerPort'
  | 'minecraftVersion'
  | 'minecraftAccountMode'
  | 'minecraftBotUsername'
>;

/**
 * Narrow lifecycle boundary around Minecraft. Callers cannot access a
 * Mineflayer bot or invoke arbitrary game operations through this interface.
 */
export interface MinecraftAdapter {
  connect(
    config: MinecraftAdapterConnectionConfig,
    signal: AbortSignal
  ): Promise<void>;
  disconnect(): Promise<void>;
  stopAllControls(): void;
  state(): MinecraftAdapterState;
  setEventHandler(handler: MinecraftAdapterEventHandler | undefined): void;
}

/**
 * The complete movement authority exposed to the companion executor. The
 * implementation may observe Mineflayer, but raw Mineflayer objects never
 * cross this boundary.
 */
export interface MinecraftMovementAdapter extends MinecraftAdapter {
  getBotPosition(): SafePosition | undefined;
  getDimension(): MinecraftDimension | undefined;
  getPlayer(username: string): ObservedPlayer | undefined;
  lookAt(position: SafePosition): Promise<void>;
  setForward(active: boolean): void;
  clearControls(): void;
  inspectForwardStep(target: ObservedPlayer): ForwardSafety;
  onMovementTick(handler: () => void): () => void;
}

export function isMinecraftMovementAdapter(
  adapter: MinecraftAdapter
): adapter is MinecraftMovementAdapter {
  const candidate = adapter as Partial<MinecraftMovementAdapter>;
  return (
    typeof candidate.getBotPosition === 'function' &&
    typeof candidate.getDimension === 'function' &&
    typeof candidate.getPlayer === 'function' &&
    typeof candidate.lookAt === 'function' &&
    typeof candidate.setForward === 'function' &&
    typeof candidate.clearControls === 'function' &&
    typeof candidate.inspectForwardStep === 'function' &&
    typeof candidate.onMovementTick === 'function'
  );
}

export function disconnectedMinecraftAdapterState(
  category: MinecraftDisconnectCategory | null = null
): MinecraftAdapterState {
  return Object.freeze({
    connectionState: 'disconnected',
    spawned: false,
    alive: false,
    negotiatedVersion: null,
    dimension: null,
    roundedPosition: null,
    health: null,
    hunger: null,
    lastDisconnectCategory: category
  });
}
