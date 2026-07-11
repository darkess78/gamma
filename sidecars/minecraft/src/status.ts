import type {
  CompanionState,
  MinecraftStatusMessage,
  SidecarStatusMessage,
  StateSnapshotMessage
} from './protocol.js';

export type MinecraftSidecarLifecycle =
  | 'idle'
  | 'connecting'
  | 'running'
  | 'stopping'
  | 'stopped';

export type MinecraftSidecarExitCategory =
  | 'gamma_shutdown'
  | 'signal'
  | 'requested'
  | 'control_unavailable'
  | 'control_disconnected'
  | 'control_delivery_failed'
  | 'protocol_error';

export type MinecraftSidecarRuntimeStatus = Readonly<{
  lifecycle: MinecraftSidecarLifecycle;
  controlReady: boolean;
  companionState: CompanionState;
  minecraftConnectionState: 'disconnected';
  heartbeatActive: boolean;
  emergencyStopActive: boolean;
  exitCategory: MinecraftSidecarExitCategory | null;
}>;

export function connectedSidecarStatus(
  companionState: CompanionState,
  uptimeSeconds: number
): SidecarStatusMessage['payload'] {
  return Object.freeze({
    connection_state: 'connected',
    companion_state: companionState,
    uptime_seconds: Math.max(0, Math.min(31_536_000, Math.floor(uptimeSeconds))),
    last_failure: null
  });
}

export function disconnectedMinecraftStatus(
  companionState: CompanionState
): MinecraftStatusMessage['payload'] {
  return Object.freeze({
    connection_state: 'disconnected',
    companion_state: companionState,
    negotiated_version: null,
    dimension: null
  });
}

export function disconnectedStateSnapshot(
  companionState: CompanionState,
  activeCommand:
    | Readonly<{ commandId: string; commandName: 'report_status' | 'emergency_stop' }>
    | null = null
): StateSnapshotMessage['payload'] {
  return Object.freeze({
    sidecar_connection_state: 'connected',
    minecraft_connection_state: 'disconnected',
    companion_state: companionState,
    owner_present: false,
    owner_display_name: null,
    owner_uuid: null,
    dimension: null,
    rounded_position: null,
    health: null,
    hunger: null,
    active_command_id: activeCommand?.commandId ?? null,
    active_command_name: activeCommand?.commandName ?? null,
    last_terminal_outcome: null,
    last_failure_code: null
  });
}

export function runtimeStatus(input: MinecraftSidecarRuntimeStatus): MinecraftSidecarRuntimeStatus {
  return Object.freeze({ ...input });
}
