import { randomUUID } from 'node:crypto';

import {
  validateMinecraftControlToken,
  validateMinecraftControlUrl
} from './control-client.js';

export const DEFAULT_CONTROL_WEBSOCKET_URL =
  'ws://127.0.0.1:8000/v1/minecraft/control';
export const DEFAULT_HEARTBEAT_SECONDS = 5;

const INSTANCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

export type MinecraftSidecarEnvironment = Readonly<
  Record<string, string | undefined>
>;

export type MinecraftSidecarConfig = Readonly<{
  controlWebSocketUrl: string;
  controlToken: string;
  sidecarInstanceId: string;
  heartbeatSeconds: number;
}>;

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
