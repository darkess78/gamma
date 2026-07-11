import type {
  CommandName,
  CompanionState,
  FailureCode,
  TerminalOutcome,
  MinecraftStatusMessage,
  SidecarStatusMessage,
  StateSnapshotMessage
} from './protocol.js';
import type {
  MinecraftAdapterState,
  MinecraftOwnerState
} from './minecraft-adapter.js';

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
  minecraftConnectionState: 'disconnected' | 'connecting' | 'connected';
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
  return minecraftStatus(disconnectedAdapterState(), companionState);
}

export function minecraftStatus(
  state: MinecraftAdapterState,
  companionState: CompanionState
): MinecraftStatusMessage['payload'] {
  return Object.freeze({
    connection_state: state.connectionState,
    companion_state: companionState,
    negotiated_version: state.negotiatedVersion,
    dimension: state.dimension
  });
}

export function disconnectedStateSnapshot(
  companionState: CompanionState,
  activeCommand:
    | Readonly<{ commandId: string; commandName: 'report_status' | 'emergency_stop' }>
    | null = null
): StateSnapshotMessage['payload'] {
  return stateSnapshot(disconnectedAdapterState(), companionState, activeCommand);
}

export function stateSnapshot(
  state: MinecraftAdapterState,
  companionState: CompanionState,
  activeCommand: Readonly<{
    commandId: string;
    commandName: CommandName;
  }> | null = null,
  lastTerminal: Readonly<{
    outcome: TerminalOutcome;
    failureCode: FailureCode | null;
  }> | null = null,
  owner: MinecraftOwnerState | null = null
): StateSnapshotMessage['payload'] {
  const ownerPresent = owner !== null && owner.sameDimension;
  return Object.freeze({
    sidecar_connection_state: 'connected',
    minecraft_connection_state: state.connectionState,
    companion_state: companionState,
    owner_present: ownerPresent,
    owner_display_name: ownerPresent ? owner.username : null,
    owner_uuid: ownerPresent ? owner.uuid : null,
    dimension: state.dimension,
    rounded_position: state.roundedPosition,
    health: state.health,
    hunger: state.hunger,
    active_command_id: activeCommand?.commandId ?? null,
    active_command_name: activeCommand?.commandName ?? null,
    last_terminal_outcome: lastTerminal?.outcome ?? null,
    last_failure_code: lastTerminal?.failureCode ?? null
  });
}

export function runtimeStatus(input: MinecraftSidecarRuntimeStatus): MinecraftSidecarRuntimeStatus {
  return Object.freeze({ ...input });
}

function disconnectedAdapterState(): MinecraftAdapterState {
  return Object.freeze({
    connectionState: 'disconnected',
    spawned: false,
    alive: false,
    negotiatedVersion: null,
    dimension: null,
    roundedPosition: null,
    health: null,
    hunger: null,
    lastDisconnectCategory: null
  });
}
