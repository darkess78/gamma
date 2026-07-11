import { randomUUID } from 'node:crypto';
import { isIP } from 'node:net';

import {
  validateMinecraftControlToken,
  validateMinecraftControlUrl
} from './control-client.js';

export const DEFAULT_CONTROL_WEBSOCKET_URL =
  'ws://127.0.0.1:8000/v1/minecraft/control';
export const DEFAULT_HEARTBEAT_SECONDS = 5;
export const DEFAULT_MINECRAFT_SERVER_HOST = '127.0.0.1';
export const DEFAULT_MINECRAFT_SERVER_PORT = 25_565;
export const SUPPORTED_MINECRAFT_VERSION = '1.21.11';
export const DEFAULT_MINECRAFT_BOT_USERNAME = 'Shana';

const INSTANCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const MINECRAFT_USERNAME_PATTERN = /^[A-Za-z0-9_]{3,16}$/;

export type MinecraftSidecarEnvironment = Readonly<
  Record<string, string | undefined>
>;

export type MinecraftSidecarConfig = Readonly<{
  controlWebSocketUrl: string;
  controlToken: string;
  sidecarInstanceId: string;
  heartbeatSeconds: number;
}>;

export type MinecraftServerConfig = Readonly<{
  minecraftServerHost: string;
  minecraftServerPort: number;
  minecraftVersion: typeof SUPPORTED_MINECRAFT_VERSION;
  minecraftAccountMode: 'offline';
  minecraftBotUsername: string;
  minecraftOwnerUsername: string | null;
}>;

export type MinecraftSidecarRuntimeConfig = Readonly<
  MinecraftSidecarConfig & MinecraftServerConfig
>;

export type MinecraftSidecarConfigDependencies = Readonly<{
  createInstanceId?: () => string;
}>;

export class MinecraftSidecarConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MinecraftSidecarConfigurationError';
  }
}

export function loadMinecraftSidecarConfig(
  environment: MinecraftSidecarEnvironment = process.env,
  dependencies: MinecraftSidecarConfigDependencies = {}
): MinecraftSidecarConfig {
  const controlWebSocketUrl = validateConfigUrl(
    environment.SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL ?? DEFAULT_CONTROL_WEBSOCKET_URL
  );
  const controlToken = validateConfigToken(
    environment.SHANA_MINECRAFT_CONTROL_TOKEN
  );
  const sidecarInstanceId = resolveInstanceId(
    environment.SHANA_MINECRAFT_SIDECAR_INSTANCE_ID,
    dependencies.createInstanceId
  );
  if (sidecarInstanceId.includes(controlToken)) {
    throw new MinecraftSidecarConfigurationError('Sidecar instance ID is invalid');
  }
  const heartbeatSeconds = parseHeartbeatSeconds(
    environment.SHANA_MINECRAFT_HEARTBEAT_SECONDS
  );
  return Object.freeze({
    controlWebSocketUrl,
    controlToken,
    sidecarInstanceId,
    heartbeatSeconds
  });
}

export function loadMinecraftSidecarRuntimeConfig(
  environment: MinecraftSidecarEnvironment = process.env,
  dependencies: MinecraftSidecarConfigDependencies = {}
): MinecraftSidecarRuntimeConfig {
  const control = loadMinecraftSidecarConfig(environment, dependencies);
  return Object.freeze({
    ...control,
    minecraftServerHost: parseMinecraftServerHost(
      environment.SHANA_MINECRAFT_SERVER_HOST ?? DEFAULT_MINECRAFT_SERVER_HOST
    ),
    minecraftServerPort: parseMinecraftServerPort(
      environment.SHANA_MINECRAFT_SERVER_PORT
    ),
    minecraftVersion: parseMinecraftVersion(environment.SHANA_MINECRAFT_VERSION),
    minecraftAccountMode: parseMinecraftAccountMode(
      environment.SHANA_MINECRAFT_ACCOUNT_MODE
    ),
    minecraftBotUsername: parseMinecraftBotUsername(
      environment.SHANA_MINECRAFT_BOT_USERNAME
    ),
    minecraftOwnerUsername: parseMinecraftOwnerUsername(
      environment.SHANA_MINECRAFT_OWNER_USERNAME
    )
  });
}

function validateConfigUrl(value: string): string {
  try {
    return validateMinecraftControlUrl(value);
  } catch {
    throw new MinecraftSidecarConfigurationError('Minecraft control URL is invalid');
  }
}

function validateConfigToken(value: string | undefined): string {
  try {
    validateMinecraftControlToken(value);
  } catch {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft control token is required and must be header-safe'
    );
  }
  return value;
}

function resolveInstanceId(
  configured: string | undefined,
  createInstanceId: (() => string) | undefined
): string {
  let value = configured;
  if (value === undefined) {
    try {
      const generated = (createInstanceId ?? randomUUID)();
      value = `sidecar-${generated}`;
    } catch {
      throw new MinecraftSidecarConfigurationError('Sidecar instance ID generation failed');
    }
  }
  if (
    value.length < 1 ||
    value.length > 128 ||
    !INSTANCE_ID_PATTERN.test(value)
  ) {
    throw new MinecraftSidecarConfigurationError('Sidecar instance ID is invalid');
  }
  return value;
}

function parseHeartbeatSeconds(value: string | undefined): number {
  if (value === undefined) return DEFAULT_HEARTBEAT_SECONDS;
  if (!/^(?:[1-9]|[1-5][0-9]|60)$/.test(value)) {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft heartbeat interval must be an integer from 1 through 60 seconds'
    );
  }
  return Number(value);
}

function parseMinecraftServerHost(value: string): string {
  if (!isLiteralLoopbackAddress(value)) {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft server host must be a literal loopback address'
    );
  }
  return value;
}

function isLiteralLoopbackAddress(value: string): boolean {
  const family = isIP(value);
  if (family === 4) return parseIpv4(value)?.[0] === 127;
  if (family !== 6) return false;
  const normalized = value.toLowerCase();
  if (normalized === '::1' || normalized === '0:0:0:0:0:0:0:1') return true;
  const mapped = /^(?:::ffff:|0:0:0:0:0:ffff:)(\d+\.\d+\.\d+\.\d+)$/u.exec(
    normalized
  );
  return mapped?.[1] !== undefined && parseIpv4(mapped[1])?.[0] === 127;
}

function parseIpv4(value: string): readonly number[] | null {
  const parts = value.split('.');
  if (parts.length !== 4) return null;
  const parsed: number[] = [];
  for (const part of parts) {
    if (!/^(?:0|[1-9]\d{0,2})$/u.test(part)) return null;
    const octet = Number(part);
    if (octet > 255) return null;
    parsed.push(octet);
  }
  return parsed;
}

function parseMinecraftServerPort(value: string | undefined): number {
  if (value === undefined) return DEFAULT_MINECRAFT_SERVER_PORT;
  if (!/^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/u.test(value)) {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft server port must be an integer from 1 through 65535'
    );
  }
  return Number(value);
}

function parseMinecraftVersion(
  value: string | undefined
): typeof SUPPORTED_MINECRAFT_VERSION {
  if (value !== undefined && value !== SUPPORTED_MINECRAFT_VERSION) {
    throw new MinecraftSidecarConfigurationError(
      `Minecraft version must be exactly ${SUPPORTED_MINECRAFT_VERSION}`
    );
  }
  return SUPPORTED_MINECRAFT_VERSION;
}

function parseMinecraftAccountMode(value: string | undefined): 'offline' {
  if (value !== undefined && value !== 'offline') {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft account mode must be offline'
    );
  }
  return 'offline';
}

function parseMinecraftBotUsername(value: string | undefined): string {
  const username = value ?? DEFAULT_MINECRAFT_BOT_USERNAME;
  if (!MINECRAFT_USERNAME_PATTERN.test(username)) {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft bot username must be 3 through 16 letters, digits, or underscores'
    );
  }
  return username;
}

function parseMinecraftOwnerUsername(value: string | undefined): string | null {
  if (value === undefined) return null;
  if (!MINECRAFT_USERNAME_PATTERN.test(value)) {
    throw new MinecraftSidecarConfigurationError(
      'Minecraft owner username must be 3 through 16 letters, digits, or underscores'
    );
  }
  return value;
}
