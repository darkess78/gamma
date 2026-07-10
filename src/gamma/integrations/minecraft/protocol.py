"""Strict, runtime-independent Minecraft companion protocol v1 models."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)


PROTOCOL_NAME = "gamma.minecraft"
PROTOCOL_VERSION = 1
MAX_COMMAND_DEADLINE_SECONDS = 900

BoundedId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ProfileId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]
SafeReason = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=160,
        strip_whitespace=True,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]
SafeDetail = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=256,
        strip_whitespace=True,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]
SafeDisplayName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        strip_whitespace=True,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]
BoundedVersion = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$",
    ),
]


class CommandName(str, Enum):
    JOIN = "join"
    LEAVE = "leave"
    FOLLOW_OWNER = "follow_owner"
    WAIT_HERE = "wait_here"
    COME_HERE = "come_here"
    LOOK_AT_OWNER = "look_at_owner"
    REPORT_STATUS = "report_status"
    STOP = "stop"
    EMERGENCY_STOP = "emergency_stop"


class TerminalOutcome(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class FailureCode(str, Enum):
    FEATURE_DISABLED = "FEATURE_DISABLED"
    SIDECAR_UNAVAILABLE = "SIDECAR_UNAVAILABLE"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    MINECRAFT_NOT_CONNECTED = "MINECRAFT_NOT_CONNECTED"
    OWNER_NOT_CONFIGURED = "OWNER_NOT_CONFIGURED"
    OWNER_NOT_PRESENT = "OWNER_NOT_PRESENT"
    UNAUTHORIZED_REQUESTER = "UNAUTHORIZED_REQUESTER"
    INVALID_COMMAND = "INVALID_COMMAND"
    INVALID_STATE = "INVALID_STATE"
    COMMAND_ALREADY_ACTIVE = "COMMAND_ALREADY_ACTIVE"
    DESTINATION_UNAVAILABLE = "DESTINATION_UNAVAILABLE"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PATH_STALLED = "PATH_STALLED"
    OWNER_TOO_FAR_AWAY = "OWNER_TOO_FAR_AWAY"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    SAFETY_POLICY_BLOCKED = "SAFETY_POLICY_BLOCKED"
    UNSUPPORTED_DIMENSION = "UNSUPPORTED_DIMENSION"
    SIDECAR_DISCONNECTED = "SIDECAR_DISCONNECTED"
    MINECRAFT_SERVER_DISCONNECTED = "MINECRAFT_SERVER_DISCONNECTED"
    BOT_DEAD = "BOT_DEAD"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    INTERNAL_SIDECAR_ERROR = "INTERNAL_SIDECAR_ERROR"
    INTERNAL_GAMMA_ERROR = "INTERNAL_GAMMA_ERROR"


class CompanionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    IDLE = "IDLE"
    FOLLOWING = "FOLLOWING"
    WAITING = "WAITING"
    RETURNING = "RETURNING"
    FLEEING = "FLEEING"
    DEAD = "DEAD"
    STOPPED = "STOPPED"


class SidecarConnectionState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STALE = "stale"
    DISCONNECTED = "disconnected"


class MinecraftConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class MinecraftDimension(str, Enum):
    OVERWORLD = "minecraft:overworld"
    NETHER = "minecraft:the_nether"
    END = "minecraft:the_end"


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Failure(SafeModel):
    code: FailureCode
    safe_detail: SafeDetail | None = None
    retriable: bool


class RoundedPosition(SafeModel):
    x: int = Field(ge=-30_000_000, le=30_000_000)
    y: int = Field(ge=-2_048, le=2_048)
    z: int = Field(ge=-30_000_000, le=30_000_000)


class JoinArguments(SafeModel):
    connection_profile_id: ProfileId | None = None


class LeaveArguments(SafeModel):
    reason: SafeReason | None = None


class FollowOwnerArguments(SafeModel):
    follow_distance: float = Field(default=3.0, ge=2.0, le=6.0)
    lease_duration_seconds: int = Field(default=300, ge=5, le=900)


class WaitHereArguments(SafeModel):
    reason: SafeReason | None = None


class ComeHereArguments(SafeModel):
    arrival_distance: float = Field(default=3.0, ge=2.0, le=4.0)


class LookAtOwnerArguments(SafeModel):
    duration_seconds: float = Field(default=2.0, ge=0.1, le=10.0)


class ReportStatusArguments(SafeModel):
    detail_level: Literal["basic", "standard"] = "standard"


class StopArguments(SafeModel):
    reason: SafeReason | None = None


class EmergencyStopArguments(SafeModel):
    reason: SafeReason | None = None


class CommandPayloadBase(SafeModel):
    deadline_at: AwareDatetime

    @field_validator("deadline_at")
    @classmethod
    def deadline_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "deadline_at")


class JoinCommandPayload(CommandPayloadBase):
    name: Literal["join"]
    arguments: JoinArguments = Field(default_factory=JoinArguments)


class LeaveCommandPayload(CommandPayloadBase):
    name: Literal["leave"]
    arguments: LeaveArguments = Field(default_factory=LeaveArguments)


class FollowOwnerCommandPayload(CommandPayloadBase):
    name: Literal["follow_owner"]
    arguments: FollowOwnerArguments = Field(default_factory=FollowOwnerArguments)


class WaitHereCommandPayload(CommandPayloadBase):
    name: Literal["wait_here"]
    arguments: WaitHereArguments = Field(default_factory=WaitHereArguments)


class ComeHereCommandPayload(CommandPayloadBase):
    name: Literal["come_here"]
    arguments: ComeHereArguments = Field(default_factory=ComeHereArguments)


class LookAtOwnerCommandPayload(CommandPayloadBase):
    name: Literal["look_at_owner"]
    arguments: LookAtOwnerArguments = Field(default_factory=LookAtOwnerArguments)


class ReportStatusCommandPayload(CommandPayloadBase):
    name: Literal["report_status"]
    arguments: ReportStatusArguments = Field(default_factory=ReportStatusArguments)


class StopCommandPayload(CommandPayloadBase):
    name: Literal["stop"]
    arguments: StopArguments = Field(default_factory=StopArguments)


class EmergencyStopCommandPayload(CommandPayloadBase):
    name: Literal["emergency_stop"]
    arguments: EmergencyStopArguments = Field(default_factory=EmergencyStopArguments)


CommandPayload = Annotated[
    JoinCommandPayload
    | LeaveCommandPayload
    | FollowOwnerCommandPayload
    | WaitHereCommandPayload
    | ComeHereCommandPayload
    | LookAtOwnerCommandPayload
    | ReportStatusCommandPayload
    | StopCommandPayload
    | EmergencyStopCommandPayload,
    Field(discriminator="name"),
]


class ProtocolEnvelope(SafeModel):
    protocol: Literal["gamma.minecraft"]
    version: Literal[1]
    type: str
    message_id: BoundedId
    sent_at: AwareDatetime
    sequence: int = Field(ge=0)

    @field_validator("sent_at")
    @classmethod
    def sent_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "sent_at")

    @field_validator("version", "sequence", mode="before")
    @classmethod
    def integer_fields_must_not_be_coerced(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version and sequence must be JSON integers")
        return value


class ConnectedEnvelope(ProtocolEnvelope):
    connection_id: BoundedId


class CommandEnvelope(ConnectedEnvelope):
    trace_id: BoundedId
    command_id: BoundedId


class HelloPayload(SafeModel):
    supported_versions: list[Literal[1]] = Field(min_length=1, max_length=4)
    sidecar_instance_id: BoundedId
    sidecar_build: BoundedVersion
    capabilities: list[Literal["companion_v1"]] = Field(min_length=1, max_length=4)
    node_version: BoundedVersion
    minecraft_library_version: BoundedVersion
    pathfinder_version: BoundedVersion
    companion_state: Literal[CompanionState.DISCONNECTED, CompanionState.IDLE]


class WelcomePayload(SafeModel):
    selected_version: Literal[1]
    heartbeat_interval_seconds: int = Field(ge=1, le=60)
    liveness_timeout_seconds: int = Field(ge=2, le=180)
    maximum_message_bytes: int = Field(ge=1_024, le=1_048_576)
    command_cache_ttl_seconds: int = Field(ge=60, le=86_400)
    command_cache_capacity: int = Field(ge=1, le=10_000)
    minecraft_chat_output_enabled: Literal[False] = False


class HeartbeatPayload(SafeModel):
    companion_state: CompanionState
    last_received_sequence: int | None = Field(default=None, ge=0)


class SidecarStatusPayload(SafeModel):
    connection_state: SidecarConnectionState
    companion_state: CompanionState
    uptime_seconds: int = Field(ge=0, le=31_536_000)
    last_failure: Failure | None = None


class MinecraftStatusPayload(SafeModel):
    connection_state: MinecraftConnectionState
    companion_state: CompanionState
    negotiated_version: BoundedVersion | None = None
    dimension: MinecraftDimension | None = None


class CommandAckPayload(SafeModel):
    accepted: bool
    command_name: CommandName
    failure: Failure | None = None

    @model_validator(mode="after")
    def failure_matches_acceptance(self) -> Self:
        if self.accepted and self.failure is not None:
            raise ValueError("accepted acknowledgments cannot contain a failure")
        if not self.accepted and self.failure is None:
            raise ValueError("rejected acknowledgments require a failure")
        return self


class CommandProgressPayload(SafeModel):
    command_name: CommandName
    phase: Literal["started", "moving", "waiting", "retrying"]
    elapsed_ms: int = Field(ge=0, le=86_400_000)
    safe_detail: SafeDetail | None = None


class StateSnapshotPayload(SafeModel):
    sidecar_connection_state: SidecarConnectionState
    minecraft_connection_state: MinecraftConnectionState
    companion_state: CompanionState
    owner_present: bool
    owner_display_name: SafeDisplayName | None = None
    owner_uuid: UUID | None = None
    dimension: MinecraftDimension | None = None
    rounded_position: RoundedPosition | None = None
    health: float | None = Field(default=None, ge=0.0, le=20.0)
    hunger: int | None = Field(default=None, ge=0, le=20)
    active_command_id: BoundedId | None = None
    active_command_name: CommandName | None = None
    last_terminal_outcome: TerminalOutcome | None = None
    last_failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def active_command_fields_are_paired(self) -> Self:
        if (self.active_command_id is None) != (self.active_command_name is None):
            raise ValueError("active command identifier and name must appear together")
        return self


class TerminalResultPayload(SafeModel):
    command_name: CommandName
    outcome: TerminalOutcome
    elapsed_ms: int = Field(ge=0, le=86_400_000)
    safe_detail: SafeDetail | None = None
    failure: Failure | None = None

    @model_validator(mode="after")
    def failure_matches_outcome(self) -> Self:
        failure_outcomes = {
            TerminalOutcome.FAILED,
            TerminalOutcome.REJECTED,
            TerminalOutcome.TIMED_OUT,
        }
        if self.outcome in failure_outcomes and self.failure is None:
            raise ValueError("failed, rejected, and timed-out results require a failure")
        if self.outcome not in failure_outcomes and self.failure is not None:
            raise ValueError("completed and cancelled results cannot contain a failure")
        return self


class CancelCommandPayload(SafeModel):
    reason: SafeReason | None = None


class EmergencyStopPayload(SafeModel):
    reason: SafeReason | None = None


class ShutdownPayload(SafeModel):
    reason: SafeReason | None = None
    leave_minecraft: Literal[True] = True


class ProtocolErrorPayload(SafeModel):
    code: FailureCode
    safe_detail: SafeDetail
    retriable: bool
    offending_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None


class HelloMessage(ProtocolEnvelope):
    type: Literal["hello"]
    payload: HelloPayload


class WelcomeMessage(ConnectedEnvelope):
    type: Literal["welcome"]
    payload: WelcomePayload


class HeartbeatMessage(ConnectedEnvelope):
    type: Literal["heartbeat"]
    payload: HeartbeatPayload


class SidecarStatusMessage(ConnectedEnvelope):
    type: Literal["sidecar_status"]
    payload: SidecarStatusPayload


class MinecraftStatusMessage(ConnectedEnvelope):
    type: Literal["minecraft_status"]
    payload: MinecraftStatusPayload


class CommandMessage(CommandEnvelope):
    type: Literal["command"]
    payload: CommandPayload

    @model_validator(mode="after")
    def deadline_follows_send_time(self) -> Self:
        if self.payload.deadline_at <= self.sent_at:
            raise ValueError("deadline_at must be later than sent_at")
        if self.payload.deadline_at - self.sent_at > timedelta(
            seconds=MAX_COMMAND_DEADLINE_SECONDS
        ):
            raise ValueError("deadline_at cannot be more than 900 seconds after sent_at")
        return self


class CommandAckMessage(CommandEnvelope):
    type: Literal["command_ack"]
    payload: CommandAckPayload


class CommandProgressMessage(CommandEnvelope):
    type: Literal["command_progress"]
    payload: CommandProgressPayload


class StateSnapshotMessage(ConnectedEnvelope):
    type: Literal["state_snapshot"]
    trace_id: BoundedId | None = None
    command_id: BoundedId | None = None
    payload: StateSnapshotPayload

    @model_validator(mode="after")
    def command_correlation_is_complete(self) -> Self:
        if self.command_id is not None and self.trace_id is None:
            raise ValueError("command-correlated snapshots require trace_id")
        return self


class TerminalResultMessage(CommandEnvelope):
    type: Literal["terminal_result"]
    payload: TerminalResultPayload


class CancelCommandMessage(CommandEnvelope):
    type: Literal["cancel_command"]
    payload: CancelCommandPayload


class EmergencyStopMessage(CommandEnvelope):
    type: Literal["emergency_stop"]
    payload: EmergencyStopPayload


class ShutdownMessage(ConnectedEnvelope):
    type: Literal["shutdown"]
    trace_id: BoundedId
    payload: ShutdownPayload


class ProtocolErrorMessage(ProtocolEnvelope):
    type: Literal["protocol_error"]
    connection_id: BoundedId | None = None
    trace_id: BoundedId | None = None
    command_id: BoundedId | None = None
    payload: ProtocolErrorPayload


ProtocolMessage = Annotated[
    HelloMessage
    | WelcomeMessage
    | HeartbeatMessage
    | SidecarStatusMessage
    | MinecraftStatusMessage
    | CommandMessage
    | CommandAckMessage
    | CommandProgressMessage
    | StateSnapshotMessage
    | TerminalResultMessage
    | CancelCommandMessage
    | EmergencyStopMessage
    | ShutdownMessage
    | ProtocolErrorMessage,
    Field(discriminator="type"),
]

PROTOCOL_MESSAGE_ADAPTER = TypeAdapter(ProtocolMessage)


def parse_protocol_message(data: str | bytes | bytearray) -> ProtocolMessage:
    """Parse one JSON protocol message through the v1 discriminator."""

    return PROTOCOL_MESSAGE_ADAPTER.validate_json(data)


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value
