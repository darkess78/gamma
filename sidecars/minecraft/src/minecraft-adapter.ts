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

export type MinecraftRoundedPosition = Readonly<{
  x: number;
  y: number;
  z: number;
}>;

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
 * Narrow boundary around Minecraft. Callers cannot access a Mineflayer bot or
 * invoke arbitrary game operations through this interface.
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
