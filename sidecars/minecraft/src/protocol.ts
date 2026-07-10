import { z } from 'zod';

export const PROTOCOL_NAME = 'gamma.minecraft' as const;
export const PROTOCOL_VERSION = 1 as const;
export const MAX_COMMAND_DEADLINE_SECONDS = 900 as const;

export const MESSAGE_TYPES = [
  'hello',
  'welcome',
  'heartbeat',
  'sidecar_status',
  'minecraft_status',
  'command',
  'command_ack',
  'command_progress',
  'state_snapshot',
  'terminal_result',
  'cancel_command',
  'emergency_stop',
  'shutdown',
  'protocol_error'
] as const;

export const COMMAND_NAMES = [
  'join',
  'leave',
  'follow_owner',
  'wait_here',
  'come_here',
  'look_at_owner',
  'report_status',
  'stop',
  'emergency_stop'
] as const;

export const TERMINAL_OUTCOMES = [
  'completed',
  'cancelled',
  'failed',
  'rejected',
  'timed_out'
] as const;

export const FAILURE_CODES = [
  'FEATURE_DISABLED',
  'SIDECAR_UNAVAILABLE',
  'PROTOCOL_MISMATCH',
  'MINECRAFT_NOT_CONNECTED',
  'OWNER_NOT_CONFIGURED',
  'OWNER_NOT_PRESENT',
  'UNAUTHORIZED_REQUESTER',
  'INVALID_COMMAND',
  'INVALID_STATE',
  'COMMAND_ALREADY_ACTIVE',
  'DESTINATION_UNAVAILABLE',
  'PATH_NOT_FOUND',
  'PATH_STALLED',
  'OWNER_TOO_FAR_AWAY',
  'DEADLINE_EXCEEDED',
  'SAFETY_POLICY_BLOCKED',
  'UNSUPPORTED_DIMENSION',
  'SIDECAR_DISCONNECTED',
  'MINECRAFT_SERVER_DISCONNECTED',
  'BOT_DEAD',
  'EMERGENCY_STOP_ACTIVE',
  'INTERNAL_SIDECAR_ERROR',
  'INTERNAL_GAMMA_ERROR'
] as const;

export const COMPANION_STATES = [
  'DISCONNECTED',
  'IDLE',
  'FOLLOWING',
  'WAITING',
  'RETURNING',
  'FLEEING',
  'DEAD',
  'STOPPED'
] as const;

export type MessageType = (typeof MESSAGE_TYPES)[number];
export type CommandName = (typeof COMMAND_NAMES)[number];
export type TerminalOutcome = (typeof TERMINAL_OUTCOMES)[number];
export type FailureCode = (typeof FAILURE_CODES)[number];
export type CompanionState = (typeof COMPANION_STATES)[number];

const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const PROFILE_ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const SAFE_TEXT_PATTERN = /^[^\x00-\x1f\x7f]+$/;
const VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.+_-]*$/;
const UTC_TIMESTAMP_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]00:00)$/;

const boundedIdSchema = z.string().min(1).max(128).regex(IDENTIFIER_PATTERN);
const profileIdSchema = z.string().min(1).max(64).regex(PROFILE_ID_PATTERN);
const safeReasonSchema = z.string().trim().min(1).max(160).regex(SAFE_TEXT_PATTERN);
const safeDetailSchema = z.string().trim().min(1).max(256).regex(SAFE_TEXT_PATTERN);
const safeDisplayNameSchema = z.string().trim().min(1).max(64).regex(SAFE_TEXT_PATTERN);
const boundedVersionSchema = z.string().min(1).max(32).regex(VERSION_PATTERN);

function isUtcTimestamp(value: string): boolean {
  const match = UTC_TIMESTAMP_PATTERN.exec(value);
  if (match === null) return false;
  const parts = match.slice(1, 7).map(Number);
  const [year, month, day, hour, minute, second] = parts;
  if (
    year === undefined ||
    month === undefined ||
    day === undefined ||
    hour === undefined ||
    minute === undefined ||
    second === undefined ||
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  ) {
    return false;
  }
  const parsed = new Date(0);
  parsed.setUTCFullYear(year, month - 1, day);
  parsed.setUTCHours(hour, minute, second, 0);
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day &&
    parsed.getUTCHours() === hour &&
    parsed.getUTCMinutes() === minute &&
    parsed.getUTCSeconds() === second &&
    Number.isFinite(Date.parse(value))
  );
}

const utcTimestampSchema = z.string().refine(isUtcTimestamp, 'Expected a valid UTC timestamp');
const sequenceSchema = z.number().int().min(0);
const commandNameSchema = z.enum(COMMAND_NAMES);
const terminalOutcomeSchema = z.enum(TERMINAL_OUTCOMES);
const failureCodeSchema = z.enum(FAILURE_CODES);
const companionStateSchema = z.enum(COMPANION_STATES);
const sidecarConnectionStateSchema = z.enum(['connecting', 'connected', 'stale', 'disconnected']);
const minecraftConnectionStateSchema = z.enum(['disconnected', 'connecting', 'connected']);
const minecraftDimensionSchema = z.enum([
  'minecraft:overworld',
  'minecraft:the_nether',
  'minecraft:the_end'
]);

const failureSchema = z.strictObject({
  code: failureCodeSchema,
  safe_detail: safeDetailSchema.nullish(),
  retriable: z.boolean()
});

const roundedPositionSchema = z.strictObject({
  x: z.number().int().min(-30_000_000).max(30_000_000),
  y: z.number().int().min(-2_048).max(2_048),
  z: z.number().int().min(-30_000_000).max(30_000_000)
});

const joinArgumentsSchema = z.strictObject({
  connection_profile_id: profileIdSchema.nullish()
});
const leaveArgumentsSchema = z.strictObject({ reason: safeReasonSchema.nullish() });
const followOwnerArgumentsSchema = z.strictObject({
  follow_distance: z.number().min(2).max(6).default(3),
  lease_duration_seconds: z.number().int().min(5).max(900).default(300)
});
const waitHereArgumentsSchema = z.strictObject({ reason: safeReasonSchema.nullish() });
const comeHereArgumentsSchema = z.strictObject({
  arrival_distance: z.number().min(2).max(4).default(3)
});
const lookAtOwnerArgumentsSchema = z.strictObject({
  duration_seconds: z.number().min(0.1).max(10).default(2)
});
const reportStatusArgumentsSchema = z.strictObject({
  detail_level: z.enum(['basic', 'standard']).default('standard')
});
const stopArgumentsSchema = z.strictObject({ reason: safeReasonSchema.nullish() });
const emergencyStopArgumentsSchema = z.strictObject({ reason: safeReasonSchema.nullish() });

const commandPayloadSchema = z.discriminatedUnion('name', [
  z.strictObject({
    name: z.literal('join'),
    deadline_at: utcTimestampSchema,
    arguments: joinArgumentsSchema.default({})
  }),
  z.strictObject({
    name: z.literal('leave'),
    deadline_at: utcTimestampSchema,
    arguments: leaveArgumentsSchema.default({})
  }),
  z.strictObject({
    name: z.literal('follow_owner'),
    deadline_at: utcTimestampSchema,
    arguments: followOwnerArgumentsSchema.default({
      follow_distance: 3,
      lease_duration_seconds: 300
    })
  }),
  z.strictObject({
    name: z.literal('wait_here'),
    deadline_at: utcTimestampSchema,
    arguments: waitHereArgumentsSchema.default({})
  }),
  z.strictObject({
    name: z.literal('come_here'),
    deadline_at: utcTimestampSchema,
    arguments: comeHereArgumentsSchema.default({ arrival_distance: 3 })
  }),
  z.strictObject({
    name: z.literal('look_at_owner'),
    deadline_at: utcTimestampSchema,
    arguments: lookAtOwnerArgumentsSchema.default({ duration_seconds: 2 })
  }),
  z.strictObject({
    name: z.literal('report_status'),
    deadline_at: utcTimestampSchema,
    arguments: reportStatusArgumentsSchema.default({ detail_level: 'standard' })
  }),
  z.strictObject({
    name: z.literal('stop'),
    deadline_at: utcTimestampSchema,
    arguments: stopArgumentsSchema.default({})
  }),
  z.strictObject({
    name: z.literal('emergency_stop'),
    deadline_at: utcTimestampSchema,
    arguments: emergencyStopArgumentsSchema.default({})
  })
]);

const commonEnvelopeShape = {
  protocol: z.literal(PROTOCOL_NAME),
  version: z.literal(PROTOCOL_VERSION),
  message_id: boundedIdSchema,
  sent_at: utcTimestampSchema,
  sequence: sequenceSchema
};

const connectedEnvelopeShape = {
  ...commonEnvelopeShape,
  connection_id: boundedIdSchema
};

const commandEnvelopeShape = {
  ...connectedEnvelopeShape,
  trace_id: boundedIdSchema,
  command_id: boundedIdSchema
};

export const helloMessageSchema = z.strictObject({
  ...commonEnvelopeShape,
  type: z.literal('hello'),
  payload: z.strictObject({
    supported_versions: z.array(z.literal(1)).min(1).max(4),
    sidecar_instance_id: boundedIdSchema,
    sidecar_build: boundedVersionSchema,
    capabilities: z.array(z.literal('companion_v1')).min(1).max(4),
    node_version: boundedVersionSchema,
    minecraft_library_version: boundedVersionSchema,
    pathfinder_version: boundedVersionSchema,
    companion_state: z.enum(['DISCONNECTED', 'IDLE'])
  })
});

export const welcomeMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('welcome'),
  payload: z.strictObject({
    selected_version: z.literal(1),
    heartbeat_interval_seconds: z.number().int().min(1).max(60),
    liveness_timeout_seconds: z.number().int().min(2).max(180),
    maximum_message_bytes: z.number().int().min(1_024).max(1_048_576),
    command_cache_ttl_seconds: z.number().int().min(60).max(86_400),
    command_cache_capacity: z.number().int().min(1).max(10_000),
    minecraft_chat_output_enabled: z.literal(false).default(false)
  })
});

export const heartbeatMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('heartbeat'),
  payload: z.strictObject({
    companion_state: companionStateSchema,
    last_received_sequence: sequenceSchema.nullish()
  })
});

export const sidecarStatusMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('sidecar_status'),
  payload: z.strictObject({
    connection_state: sidecarConnectionStateSchema,
    companion_state: companionStateSchema,
    uptime_seconds: z.number().int().min(0).max(31_536_000),
    last_failure: failureSchema.nullish()
  })
});

export const minecraftStatusMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('minecraft_status'),
  payload: z.strictObject({
    connection_state: minecraftConnectionStateSchema,
    companion_state: companionStateSchema,
    negotiated_version: boundedVersionSchema.nullish(),
    dimension: minecraftDimensionSchema.nullish()
  })
});

export const commandMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('command'),
  payload: commandPayloadSchema
});

const commandAckPayloadSchema = z
  .strictObject({
    accepted: z.boolean(),
    command_name: commandNameSchema,
    failure: failureSchema.nullish()
  })
  .superRefine((payload, context) => {
    if (payload.accepted && payload.failure != null) {
      context.addIssue({ code: 'custom', message: 'Accepted acknowledgments cannot contain a failure' });
    }
    if (!payload.accepted && payload.failure == null) {
      context.addIssue({ code: 'custom', message: 'Rejected acknowledgments require a failure' });
    }
  });

export const commandAckMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('command_ack'),
  payload: commandAckPayloadSchema
});

export const commandProgressMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('command_progress'),
  payload: z.strictObject({
    command_name: commandNameSchema,
    phase: z.enum(['started', 'moving', 'waiting', 'retrying']),
    elapsed_ms: z.number().int().min(0).max(86_400_000),
    safe_detail: safeDetailSchema.nullish()
  })
});

const stateSnapshotPayloadSchema = z
  .strictObject({
    sidecar_connection_state: sidecarConnectionStateSchema,
    minecraft_connection_state: minecraftConnectionStateSchema,
    companion_state: companionStateSchema,
    owner_present: z.boolean(),
    owner_display_name: safeDisplayNameSchema.nullish(),
    owner_uuid: z.uuid().nullish(),
    dimension: minecraftDimensionSchema.nullish(),
    rounded_position: roundedPositionSchema.nullish(),
    health: z.number().min(0).max(20).nullish(),
    hunger: z.number().int().min(0).max(20).nullish(),
    active_command_id: boundedIdSchema.nullish(),
    active_command_name: commandNameSchema.nullish(),
    last_terminal_outcome: terminalOutcomeSchema.nullish(),
    last_failure_code: failureCodeSchema.nullish()
  })
  .superRefine((payload, context) => {
    if ((payload.active_command_id == null) !== (payload.active_command_name == null)) {
      context.addIssue({
        code: 'custom',
        message: 'Active command identifier and name must appear together'
      });
    }
  });

export const stateSnapshotMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('state_snapshot'),
  trace_id: boundedIdSchema.nullish(),
  command_id: boundedIdSchema.nullish(),
  payload: stateSnapshotPayloadSchema
});

const terminalResultPayloadSchema = z
  .strictObject({
    command_name: commandNameSchema,
    outcome: terminalOutcomeSchema,
    elapsed_ms: z.number().int().min(0).max(86_400_000),
    safe_detail: safeDetailSchema.nullish(),
    failure: failureSchema.nullish()
  })
  .superRefine((payload, context) => {
    const requiresFailure = ['failed', 'rejected', 'timed_out'].includes(payload.outcome);
    if (requiresFailure && payload.failure == null) {
      context.addIssue({ code: 'custom', message: 'This terminal outcome requires a failure' });
    }
    if (!requiresFailure && payload.failure != null) {
      context.addIssue({ code: 'custom', message: 'This terminal outcome cannot contain a failure' });
    }
  });

export const terminalResultMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('terminal_result'),
  payload: terminalResultPayloadSchema
});

export const cancelCommandMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('cancel_command'),
  payload: z.strictObject({ reason: safeReasonSchema.nullish() })
});

export const emergencyStopMessageSchema = z.strictObject({
  ...commandEnvelopeShape,
  type: z.literal('emergency_stop'),
  payload: z.strictObject({ reason: safeReasonSchema.nullish() })
});

export const shutdownMessageSchema = z.strictObject({
  ...connectedEnvelopeShape,
  type: z.literal('shutdown'),
  trace_id: boundedIdSchema,
  payload: z.strictObject({
    reason: safeReasonSchema.nullish(),
    leave_minecraft: z.literal(true).default(true)
  })
});

export const protocolErrorMessageSchema = z.strictObject({
  ...commonEnvelopeShape,
  type: z.literal('protocol_error'),
  connection_id: boundedIdSchema.nullish(),
  trace_id: boundedIdSchema.nullish(),
  command_id: boundedIdSchema.nullish(),
  payload: z.strictObject({
    code: failureCodeSchema,
    safe_detail: safeDetailSchema,
    retriable: z.boolean(),
    offending_type: z.string().min(1).max(64).nullish()
  })
});

const protocolMessageUnionSchema = z.discriminatedUnion('type', [
  helloMessageSchema,
  welcomeMessageSchema,
  heartbeatMessageSchema,
  sidecarStatusMessageSchema,
  minecraftStatusMessageSchema,
  commandMessageSchema,
  commandAckMessageSchema,
  commandProgressMessageSchema,
  stateSnapshotMessageSchema,
  terminalResultMessageSchema,
  cancelCommandMessageSchema,
  emergencyStopMessageSchema,
  shutdownMessageSchema,
  protocolErrorMessageSchema
]);

export const protocolMessageSchema = protocolMessageUnionSchema.superRefine((message, context) => {
  if (message.type === 'command') {
    const sentAt = Date.parse(message.sent_at);
    const deadlineAt = Date.parse(message.payload.deadline_at);
    const delta = deadlineAt - sentAt;
    if (delta <= 0) {
      context.addIssue({ code: 'custom', message: 'deadline_at must be later than sent_at' });
    } else if (delta > MAX_COMMAND_DEADLINE_SECONDS * 1_000) {
      context.addIssue({
        code: 'custom',
        message: 'deadline_at cannot be more than 900 seconds after sent_at'
      });
    }
  }
  if (
    message.type === 'state_snapshot' &&
    message.command_id != null &&
    message.trace_id == null
  ) {
    context.addIssue({
      code: 'custom',
      message: 'Command-correlated snapshots require trace_id'
    });
  }
});

export type HelloMessage = z.infer<typeof helloMessageSchema>;
export type WelcomeMessage = z.infer<typeof welcomeMessageSchema>;
export type HeartbeatMessage = z.infer<typeof heartbeatMessageSchema>;
export type SidecarStatusMessage = z.infer<typeof sidecarStatusMessageSchema>;
export type MinecraftStatusMessage = z.infer<typeof minecraftStatusMessageSchema>;
export type CommandMessage = z.infer<typeof commandMessageSchema>;
export type CommandAckMessage = z.infer<typeof commandAckMessageSchema>;
export type CommandProgressMessage = z.infer<typeof commandProgressMessageSchema>;
export type StateSnapshotMessage = z.infer<typeof stateSnapshotMessageSchema>;
export type TerminalResultMessage = z.infer<typeof terminalResultMessageSchema>;
export type CancelCommandMessage = z.infer<typeof cancelCommandMessageSchema>;
export type EmergencyStopMessage = z.infer<typeof emergencyStopMessageSchema>;
export type ShutdownMessage = z.infer<typeof shutdownMessageSchema>;
export type ProtocolErrorMessage = z.infer<typeof protocolErrorMessageSchema>;
export type ProtocolMessage = z.infer<typeof protocolMessageSchema>;

export function parseProtocolMessage(input: unknown): ProtocolMessage {
  return protocolMessageSchema.parse(input);
}
