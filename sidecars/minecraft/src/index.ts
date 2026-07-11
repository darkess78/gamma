import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
  loadMinecraftSidecarRuntimeConfig,
  type MinecraftSidecarEnvironment,
  type MinecraftSidecarRuntimeConfig
} from './config.js';
import {
  MinecraftSidecarRuntime,
  type MinecraftSidecarRuntimeExit
} from './runtime.js';
import type { MinecraftSidecarExitCategory } from './status.js';

export type MinecraftSignalTarget = Pick<NodeJS.Process, 'on' | 'off'>;

export type MinecraftSidecarMainRuntime = Readonly<{
  run: () => Promise<MinecraftSidecarRuntimeExit>;
  shutdown: (reason: 'signal' | 'requested') => Promise<void>;
}>;

export type MinecraftSidecarMainDependencies = Readonly<{
  environment?: MinecraftSidecarEnvironment;
  createRuntime?: (config: MinecraftSidecarRuntimeConfig) => MinecraftSidecarMainRuntime;
  signalTarget?: MinecraftSignalTarget;
  log?: (message: string) => void;
}>;

export function installSignalHandlers(
  requestShutdown: (reason: 'signal') => void | Promise<void>,
  target: MinecraftSignalTarget = process
): () => void {
  let disposed = false;
  const handler = (): void => {
    try {
      void Promise.resolve(requestShutdown('signal')).catch(() => undefined);
    } catch {
      // Signal handling remains bounded and never emits raw errors.
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

export async function runMinecraftSidecarMain(
  dependencies: MinecraftSidecarMainDependencies = {}
): Promise<number> {
  const log = dependencies.log ?? ((message: string) => console.log(message));
  let config: MinecraftSidecarRuntimeConfig;
  let runtime: MinecraftSidecarMainRuntime;
  try {
    config = loadMinecraftSidecarRuntimeConfig(
      dependencies.environment ?? process.env
    );
    runtime = (dependencies.createRuntime ?? ((value) => new MinecraftSidecarRuntime(value)))(
      config
    );
  } catch {
    log('Minecraft sidecar configuration is invalid.');
    return 2;
  }

  const disposeSignals = installSignalHandlers(
    (reason) => runtime.shutdown(reason),
    dependencies.signalTarget ?? process
  );
  log('Minecraft sidecar starting.');
  try {
    const result = await runtime.run();
    log(exitMessage(result.category));
    return successfulExit(result.category) ? 0 : 1;
  } catch {
    log('Minecraft sidecar stopped: control unavailable.');
    return 1;
  } finally {
    disposeSignals();
  }
}

function successfulExit(category: MinecraftSidecarExitCategory): boolean {
  return ['gamma_shutdown', 'signal', 'requested'].includes(category);
}

function exitMessage(category: MinecraftSidecarExitCategory): string {
  const messages: Record<MinecraftSidecarExitCategory, string> = {
    gamma_shutdown: 'Minecraft sidecar stopped: Gamma shutdown.',
    signal: 'Minecraft sidecar stopped: signal.',
    requested: 'Minecraft sidecar stopped: requested.',
    control_unavailable: 'Minecraft sidecar stopped: control unavailable.',
    control_disconnected: 'Minecraft sidecar stopped: control disconnected.',
    control_delivery_failed: 'Minecraft sidecar stopped: control delivery failed.',
    protocol_error: 'Minecraft sidecar stopped: protocol error.'
  };
  return messages[category];
}

function isDirectExecution(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && pathToFileURL(resolve(entry)).href === import.meta.url;
}

if (isDirectExecution()) {
  void runMinecraftSidecarMain().then((exitCode) => {
    process.exitCode = exitCode;
  });
}
