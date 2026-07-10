"""In-process, transport-independent Minecraft companion coordination core."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from gamma.integrations.minecraft.protocol import (
    MAX_COMMAND_DEADLINE_SECONDS,
    PROTOCOL_VERSION,
    CancelCommandMessage,
    CancelCommandPayload,
    CommandAckMessage,
    CommandAckPayload,
    CommandMessage,
    CommandName,
    CommandPayload,
    CommandPayloadBase,
    CommandProgressMessage,
    CompanionState,
    EmergencyStopMessage,
    EmergencyStopPayload,
    FailureCode,
    HeartbeatMessage,
    HelloMessage,
    MinecraftConnectionState,
    MinecraftStatusMessage,
    ProtocolErrorMessage,
    ProtocolErrorPayload,
    ProtocolMessage,
    SidecarConnectionState,
    SidecarStatusMessage,
    StateSnapshotMessage,
    TerminalOutcome,
    TerminalResultMessage,
    WelcomeMessage,
    WelcomePayload,
)
from gamma.integrations.minecraft.transport import MinecraftTransport


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class SubmissionDisposition(str, Enum):
    SENT = "sent"
    REJECTED = "rejected"
    ACTIVE_DUPLICATE = "active_duplicate"
    CACHED_DUPLICATE = "cached_duplicate"


class ReceiveDisposition(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class CancellationDisposition(str, Enum):
    SENT = "sent"
    ALREADY_PENDING = "already_pending"
    NO_ACTIVE_COMMAND = "no_active_command"
    REJECTED = "rejected"


class EmergencyStopDisposition(str, Enum):
    DELIVERED = "delivered"
    LATCHED_LOCALLY = "latched_locally"
    ALREADY_LATCHED = "already_latched"


@dataclass(frozen=True, slots=True)
class TerminalSummary:
    command_id: str
    trace_id: str
    command_name: CommandName
    outcome: TerminalOutcome
    completed_at: datetime
    safe_detail: str | None = None
    failure_code: FailureCode | None = None


@dataclass(frozen=True, slots=True)
class ActiveCommandStatus:
    command_id: str
    trace_id: str
    command_name: CommandName
    sent_at: datetime
    deadline_at: datetime
    acknowledged: bool
    cancellation_pending: bool
    progress_phase: str | None
    progress_elapsed_ms: int | None
    progress_safe_detail: str | None


@dataclass(frozen=True, slots=True)
class CoordinatorStatus:
    enabled: bool
    transport_attached: bool
    handshake_complete: bool
    sidecar_healthy: bool
    connection_id: str | None
    last_heartbeat_at: datetime | None
    minecraft_connection_state: MinecraftConnectionState
    companion_state: CompanionState
    owner_present: bool
    active_command: ActiveCommandStatus | None
    emergency_stop_active: bool
    interrupted_command_id: str | None
    last_terminal: TerminalSummary | None
    last_failure_code: FailureCode | None
    sidecar_instance_id: str | None
    sidecar_build: str | None


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    disposition: SubmissionDisposition
    command_id: str | None = None
    trace_id: str | None = None
    failure_code: FailureCode | None = None
    active: ActiveCommandStatus | None = None
    terminal: TerminalSummary | None = None


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    disposition: ReceiveDisposition
    failure_code: FailureCode | None = None
    terminal: TerminalSummary | None = None


@dataclass(frozen=True, slots=True)
class CancellationResult:
    disposition: CancellationDisposition
    command_id: str | None = None
    failure_code: FailureCode | None = None


@dataclass(frozen=True, slots=True)
class EmergencyStopResult:
    disposition: EmergencyStopDisposition
    command_id: str
    trace_id: str
    interrupted_command_id: str | None
    delivered: bool


@dataclass(slots=True)
class _ActiveCommand:
    command_id: str
    trace_id: str
    command_name: CommandName
    fingerprint: tuple[Any, ...]
    sent_at: datetime
    deadline_at: datetime
    connection_id: str
    ack_accepted: bool | None = None
    ack_fingerprint: tuple[Any, ...] | None = None
    cancellation_pending: bool = False
    progress_phase: str | None = None
    progress_elapsed_ms: int | None = None
    progress_safe_detail: str | None = None


@dataclass(frozen=True, slots=True)
class _CompletedCommand:
    fingerprint: tuple[Any, ...]
    connection_id: str
    ack_fingerprint: tuple[Any, ...] | None
    result_fingerprint: tuple[Any, ...] | None
    locally_terminalized: bool
    terminal: TerminalSummary


_MOVEMENT_COMMANDS = {
    CommandName.FOLLOW_OWNER,
    CommandName.WAIT_HERE,
    CommandName.COME_HERE,
    CommandName.LOOK_AT_OWNER,
    CommandName.STOP,
}

_ALLOWED_STATES: dict[CommandName, set[CompanionState]] = {
    CommandName.FOLLOW_OWNER: {CompanionState.IDLE, CompanionState.WAITING},
    CommandName.WAIT_HERE: {
        CompanionState.IDLE,
        CompanionState.FOLLOWING,
        CompanionState.RETURNING,
    },
    CommandName.COME_HERE: {
        CompanionState.IDLE,
        CompanionState.WAITING,
        CompanionState.FOLLOWING,
    },
    CommandName.LOOK_AT_OWNER: {
        CompanionState.IDLE,
        CompanionState.WAITING,
        CompanionState.FOLLOWING,
    },
    CommandName.STOP: {
        CompanionState.IDLE,
        CompanionState.FOLLOWING,
        CompanionState.WAITING,
        CompanionState.RETURNING,
        CompanionState.FLEEING,
    },
}


class MinecraftCoordinator:
    """Coordinate one bounded command without owning any network or game I/O.

    Completed command IDs are remembered only while present in the bounded
    in-memory cache. An evicted ID is treated as a new submission.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        transport: MinecraftTransport | None = None,
        clock: Clock | None = None,
        message_id_factory: IdFactory | None = None,
        command_id_factory: IdFactory | None = None,
        trace_id_factory: IdFactory | None = None,
        connection_id_factory: IdFactory | None = None,
        heartbeat_interval_seconds: int = 5,
        liveness_timeout_seconds: int = 15,
        terminal_cache_capacity: int = 1_000,
        terminal_cache_ttl_seconds: int = 600,
    ) -> None:
        if not 1 <= heartbeat_interval_seconds <= 60:
            raise ValueError("heartbeat_interval_seconds must be between 1 and 60")
        if not 2 <= liveness_timeout_seconds <= 180:
            raise ValueError("liveness_timeout_seconds must be between 2 and 180")
        if not 1 <= terminal_cache_capacity <= 10_000:
            raise ValueError("terminal_cache_capacity must be between 1 and 10000")
        if not 60 <= terminal_cache_ttl_seconds <= 86_400:
            raise ValueError("terminal_cache_ttl_seconds must be between 60 and 86400")
        self.enabled = enabled
        self._initial_enabled = enabled
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._message_id_factory = message_id_factory or (lambda: str(uuid4()))
        self._command_id_factory = command_id_factory or (lambda: str(uuid4()))
        self._trace_id_factory = trace_id_factory or (lambda: str(uuid4()))
        self._connection_id_factory = connection_id_factory or (lambda: str(uuid4()))
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._liveness_timeout_seconds = liveness_timeout_seconds
        self._terminal_cache_capacity = terminal_cache_capacity
        self._terminal_cache_ttl = timedelta(seconds=terminal_cache_ttl_seconds)
        self._outbound_sequence = 0
        self._connection_id: str | None = None
        self._handshake_complete = False
        self._sidecar_healthy = False
        self._last_heartbeat_at: datetime | None = None
        self._last_heartbeat_sequence: int | None = None
        self._state_fresh = False
        self._sidecar_instance_id: str | None = None
        self._sidecar_build: str | None = None
        self._minecraft_connection_state = MinecraftConnectionState.DISCONNECTED
        self._companion_state = CompanionState.DISCONNECTED
        self._owner_present = False
        self._active: _ActiveCommand | None = None
        self._terminal_cache: OrderedDict[str, _CompletedCommand] = OrderedDict()
        self._last_terminal: TerminalSummary | None = None
        self._last_failure_code: FailureCode | None = None
        self._emergency_stop_active = False
        self._emergency_command_id: str | None = None
        self._emergency_trace_id: str | None = None
        self._interrupted_command_id: str | None = None
        self._emergency_leave_completed = False

    def attach_transport(self, transport: MinecraftTransport) -> None:
        """Attach an in-process delivery target, replacing any prior session."""

        if transport is self._transport:
            return
        if self._transport is not None or self._handshake_complete:
            self._invalidate_session(clear_transport=True)
        self._transport = transport

    def detach_transport(self, transport: MinecraftTransport | None = None) -> bool:
        """Report transport loss; a stale adapter cannot detach its replacement."""

        if transport is not None and transport is not self._transport:
            return False
        if self._transport is None and not self._handshake_complete:
            return False
        self._invalidate_session(clear_transport=True)
        return True

    async def receive(self, message: ProtocolMessage) -> ReceiveResult:
        """Consume one already parsed canonical sidecar message."""

        if isinstance(message, HelloMessage):
            return await self._receive_hello(message)
        if not self._handshake_complete or self._connection_id is None:
            return self._reject_receive(FailureCode.SIDECAR_UNAVAILABLE)
        if getattr(message, "connection_id", None) != self._connection_id:
            return ReceiveResult(ReceiveDisposition.IGNORED, FailureCode.SIDECAR_DISCONNECTED)
        if isinstance(message, HeartbeatMessage):
            if (
                self._last_heartbeat_sequence is not None
                and message.sequence <= self._last_heartbeat_sequence
            ):
                return ReceiveResult(ReceiveDisposition.IGNORED)
            received_at = self._now()
            if self._last_heartbeat_at is None or received_at > self._last_heartbeat_at:
                self._last_heartbeat_at = received_at
            self._last_heartbeat_sequence = message.sequence
            self._sidecar_healthy = True
            self._companion_state = message.payload.companion_state
            return ReceiveResult(ReceiveDisposition.ACCEPTED)
        if isinstance(message, SidecarStatusMessage):
            self._companion_state = message.payload.companion_state
            if message.payload.last_failure is not None:
                self._last_failure_code = message.payload.last_failure.code
            if message.payload.connection_state in {
                SidecarConnectionState.STALE,
                SidecarConnectionState.DISCONNECTED,
            }:
                self._invalidate_session(clear_transport=False)
            else:
                self._sidecar_healthy = message.payload.connection_state == SidecarConnectionState.CONNECTED
            return ReceiveResult(ReceiveDisposition.ACCEPTED)
        if isinstance(message, MinecraftStatusMessage):
            self._minecraft_connection_state = message.payload.connection_state
            self._companion_state = message.payload.companion_state
            self._state_fresh = True
            return ReceiveResult(ReceiveDisposition.ACCEPTED)
        if isinstance(message, StateSnapshotMessage):
            self._minecraft_connection_state = message.payload.minecraft_connection_state
            self._companion_state = message.payload.companion_state
            self._owner_present = message.payload.owner_present
            self._state_fresh = True
            if message.payload.last_failure_code is not None:
                self._last_failure_code = message.payload.last_failure_code
            return ReceiveResult(ReceiveDisposition.ACCEPTED)
        if isinstance(message, CommandAckMessage):
            return self._receive_ack(message)
        if isinstance(message, CommandProgressMessage):
            return self._receive_progress(message)
        if isinstance(message, TerminalResultMessage):
            return self._receive_terminal(message)
        return self._reject_receive(FailureCode.INVALID_COMMAND)

    async def submit_command(
        self,
        payload: CommandPayload,
        *,
        command_id: str | None = None,
        trace_id: str | None = None,
    ) -> SubmissionResult:
        """Validate and send one canonical bounded ordinary command."""

        if not isinstance(payload, CommandPayloadBase):
            return SubmissionResult(SubmissionDisposition.REJECTED, failure_code=FailureCode.INVALID_COMMAND)
        name = CommandName(payload.name)
        if name == CommandName.EMERGENCY_STOP:
            return SubmissionResult(SubmissionDisposition.REJECTED, failure_code=FailureCode.INVALID_COMMAND)
        now = self._now()
        self._prune_terminal_cache(now)
        assigned_command_id = command_id or self._command_id_factory()
        assigned_trace_id = trace_id or self._trace_id_factory()
        fingerprint = self._fingerprint(payload)
        duplicate = self._duplicate_submission(assigned_command_id, fingerprint)
        if duplicate is not None:
            return duplicate
        failure = self._submission_failure(payload, now)
        if failure is not None:
            self._last_failure_code = failure
            return SubmissionResult(
                SubmissionDisposition.REJECTED,
                command_id=assigned_command_id,
                trace_id=assigned_trace_id,
                failure_code=failure,
            )
        assert self._connection_id is not None
        active = _ActiveCommand(
            command_id=assigned_command_id,
            trace_id=assigned_trace_id,
            command_name=name,
            fingerprint=fingerprint,
            sent_at=now,
            deadline_at=payload.deadline_at,
            connection_id=self._connection_id,
        )
        self._active = active
        message = CommandMessage(
            protocol="gamma.minecraft",
            version=PROTOCOL_VERSION,
            type="command",
            message_id=self._message_id_factory(),
            connection_id=self._connection_id,
            sent_at=now,
            sequence=self._next_sequence(),
            trace_id=assigned_trace_id,
            command_id=assigned_command_id,
            payload=payload.model_copy(deep=True),
        )
        if not await self._send(message):
            return SubmissionResult(
                SubmissionDisposition.REJECTED,
                command_id=assigned_command_id,
                trace_id=assigned_trace_id,
                failure_code=FailureCode.SIDECAR_UNAVAILABLE,
                terminal=self._terminal_cache.get(assigned_command_id).terminal
                if assigned_command_id in self._terminal_cache
                else None,
            )
        return SubmissionResult(
            SubmissionDisposition.SENT,
            command_id=assigned_command_id,
            trace_id=assigned_trace_id,
            active=self._active_status(active),
        )

    async def cancel_active_command(
        self,
        command_id: str,
        *,
        reason: str | None = None,
    ) -> CancellationResult:
        active = self._active
        if active is None or active.command_id != command_id:
            return CancellationResult(CancellationDisposition.NO_ACTIVE_COMMAND, command_id=command_id)
        if active.connection_id != self._connection_id:
            return CancellationResult(
                CancellationDisposition.REJECTED,
                command_id=command_id,
                failure_code=FailureCode.SIDECAR_DISCONNECTED,
            )
        if active.cancellation_pending:
            return CancellationResult(CancellationDisposition.ALREADY_PENDING, command_id=command_id)
        try:
            payload = CancelCommandPayload(reason=reason)
        except ValueError:
            return CancellationResult(
                CancellationDisposition.REJECTED,
                command_id=command_id,
                failure_code=FailureCode.INVALID_COMMAND,
            )
        if not self._session_available():
            return CancellationResult(
                CancellationDisposition.REJECTED,
                command_id=command_id,
                failure_code=FailureCode.SIDECAR_UNAVAILABLE,
            )
        active.cancellation_pending = True
        message = CancelCommandMessage(
            protocol="gamma.minecraft",
            version=PROTOCOL_VERSION,
            type="cancel_command",
            message_id=self._message_id_factory(),
            connection_id=active.connection_id,
            sent_at=self._now(),
            sequence=self._next_sequence(),
            trace_id=active.trace_id,
            command_id=active.command_id,
            payload=payload,
        )
        if not await self._send(message):
            return CancellationResult(
                CancellationDisposition.REJECTED,
                command_id=command_id,
                failure_code=FailureCode.SIDECAR_UNAVAILABLE,
            )
        return CancellationResult(CancellationDisposition.SENT, command_id=command_id)

    async def activate_emergency_stop(self, *, reason: str | None = None) -> EmergencyStopResult:
        """Latch local safety immediately and deliver once when a session exists."""

        if self._emergency_stop_active:
            assert self._emergency_command_id is not None
            assert self._emergency_trace_id is not None
            return EmergencyStopResult(
                EmergencyStopDisposition.ALREADY_LATCHED,
                self._emergency_command_id,
                self._emergency_trace_id,
                self._interrupted_command_id,
                False,
            )
        payload = EmergencyStopPayload(reason=reason)
        self._emergency_stop_active = True
        self._emergency_command_id = self._command_id_factory()
        self._emergency_trace_id = self._trace_id_factory()
        self._interrupted_command_id = self._active.command_id if self._active is not None else None
        self._emergency_leave_completed = False
        if not self._session_available():
            return EmergencyStopResult(
                EmergencyStopDisposition.LATCHED_LOCALLY,
                self._emergency_command_id,
                self._emergency_trace_id,
                self._interrupted_command_id,
                False,
            )
        assert self._connection_id is not None
        message = EmergencyStopMessage(
            protocol="gamma.minecraft",
            version=PROTOCOL_VERSION,
            type="emergency_stop",
            message_id=self._message_id_factory(),
            connection_id=self._connection_id,
            sent_at=self._now(),
            sequence=self._next_sequence(),
            trace_id=self._emergency_trace_id,
            command_id=self._emergency_command_id,
            payload=payload,
        )
        delivered = await self._send(message)
        return EmergencyStopResult(
            EmergencyStopDisposition.DELIVERED if delivered else EmergencyStopDisposition.LATCHED_LOCALLY,
            self._emergency_command_id,
            self._emergency_trace_id,
            self._interrupted_command_id,
            delivered,
        )

    def check_liveness(self) -> bool:
        """Evaluate heartbeat staleness and command deadline without sleeping."""

        now = self._now()
        self._prune_terminal_cache(now)
        if not self._handshake_complete or self._last_heartbeat_at is None:
            return False
        if now - self._last_heartbeat_at >= timedelta(seconds=self._liveness_timeout_seconds):
            self._invalidate_session(clear_transport=True)
            return False
        self.check_deadlines(now=now)
        return self._sidecar_healthy

    def check_deadlines(self, *, now: datetime | None = None) -> TerminalSummary | None:
        """Terminalize expired work deterministically; no timer is created."""

        evaluated_at = now or self._now()
        if self._active is None or evaluated_at < self._active.deadline_at:
            return None
        return self._terminalize_local(
            TerminalOutcome.TIMED_OUT,
            FailureCode.DEADLINE_EXCEEDED,
            "Command deadline exceeded.",
            completed_at=evaluated_at,
        )

    def status(self) -> CoordinatorStatus:
        """Return a bounded immutable snapshot with no transport or raw payloads."""

        self._prune_terminal_cache(self._now())
        return CoordinatorStatus(
            enabled=self.enabled,
            transport_attached=self._transport is not None,
            handshake_complete=self._handshake_complete,
            sidecar_healthy=self._sidecar_healthy,
            connection_id=self._connection_id,
            last_heartbeat_at=self._last_heartbeat_at,
            minecraft_connection_state=self._minecraft_connection_state,
            companion_state=self._companion_state,
            owner_present=self._owner_present,
            active_command=self._active_status(self._active) if self._active is not None else None,
            emergency_stop_active=self._emergency_stop_active,
            interrupted_command_id=self._interrupted_command_id,
            last_terminal=self._last_terminal,
            last_failure_code=self._last_failure_code,
            sidecar_instance_id=self._sidecar_instance_id,
            sidecar_build=self._sidecar_build,
        )

    def reset(self) -> None:
        """Reconstruct non-moving process state without providing a safety unlatch."""

        emergency_stop_active = self._emergency_stop_active
        emergency_command_id = self._emergency_command_id
        emergency_trace_id = self._emergency_trace_id
        interrupted_command_id = self._interrupted_command_id

        self._transport = None
        self._connection_id = None
        self._handshake_complete = False
        self._sidecar_healthy = False
        self._last_heartbeat_at = None
        self._last_heartbeat_sequence = None
        self._state_fresh = False
        self._sidecar_instance_id = None
        self._sidecar_build = None
        self._minecraft_connection_state = MinecraftConnectionState.DISCONNECTED
        self._companion_state = CompanionState.DISCONNECTED
        self._owner_present = False
        self._active = None
        self._terminal_cache.clear()
        self._last_terminal = None
        self._last_failure_code = None
        self._emergency_stop_active = emergency_stop_active
        self._emergency_command_id = emergency_command_id
        self._emergency_trace_id = emergency_trace_id
        self._interrupted_command_id = interrupted_command_id
        self._emergency_leave_completed = False
        self._outbound_sequence = 0
        self.enabled = self._initial_enabled

    async def _receive_hello(self, message: HelloMessage) -> ReceiveResult:
        if self._transport is None:
            return self._reject_receive(FailureCode.SIDECAR_UNAVAILABLE)
        versions = set(message.payload.supported_versions)
        capabilities = set(message.payload.capabilities)
        if PROTOCOL_VERSION not in versions or "companion_v1" not in capabilities:
            self._last_failure_code = FailureCode.PROTOCOL_MISMATCH
            error = ProtocolErrorMessage(
                protocol="gamma.minecraft",
                version=PROTOCOL_VERSION,
                type="protocol_error",
                message_id=self._message_id_factory(),
                sent_at=self._now(),
                sequence=0,
                payload=ProtocolErrorPayload(
                    code=FailureCode.PROTOCOL_MISMATCH,
                    safe_detail="No supported Minecraft companion protocol version.",
                    retriable=False,
                    offending_type="hello",
                ),
            )
            await self._send(error, invalidate_on_failure=False)
            return ReceiveResult(ReceiveDisposition.REJECTED, FailureCode.PROTOCOL_MISMATCH)
        if self._handshake_complete:
            self._invalidate_session(clear_transport=False)
        now = self._now()
        self._connection_id = self._connection_id_factory()
        self._handshake_complete = True
        self._sidecar_healthy = True
        self._last_heartbeat_at = now
        self._last_heartbeat_sequence = None
        self._state_fresh = False
        self._sidecar_instance_id = message.payload.sidecar_instance_id
        self._sidecar_build = message.payload.sidecar_build
        self._minecraft_connection_state = MinecraftConnectionState.DISCONNECTED
        self._companion_state = message.payload.companion_state
        self._owner_present = False
        self._outbound_sequence = 0
        welcome = WelcomeMessage(
            protocol="gamma.minecraft",
            version=PROTOCOL_VERSION,
            type="welcome",
            message_id=self._message_id_factory(),
            connection_id=self._connection_id,
            sent_at=now,
            sequence=self._next_sequence(),
            payload=WelcomePayload(
                selected_version=PROTOCOL_VERSION,
                heartbeat_interval_seconds=self._heartbeat_interval_seconds,
                liveness_timeout_seconds=self._liveness_timeout_seconds,
                maximum_message_bytes=65_536,
                command_cache_ttl_seconds=int(self._terminal_cache_ttl.total_seconds()),
                command_cache_capacity=min(self._terminal_cache_capacity, 10_000),
                minecraft_chat_output_enabled=False,
            ),
        )
        if not await self._send(welcome):
            return ReceiveResult(ReceiveDisposition.REJECTED, FailureCode.SIDECAR_UNAVAILABLE)
        return ReceiveResult(ReceiveDisposition.ACCEPTED)

    def _receive_ack(self, message: CommandAckMessage) -> ReceiveResult:
        active = self._active
        if active is None:
            completed = self._terminal_cache.get(message.command_id)
            if completed is not None:
                if completed.locally_terminalized:
                    return ReceiveResult(ReceiveDisposition.IGNORED, terminal=completed.terminal)
                if completed.ack_fingerprint == self._model_fingerprint(message.payload):
                    return ReceiveResult(ReceiveDisposition.IGNORED, terminal=completed.terminal)
                return self._protocol_fault()
            return ReceiveResult(ReceiveDisposition.IGNORED)
        if not self._correlates(active, message):
            return ReceiveResult(ReceiveDisposition.IGNORED)
        if message.payload.command_name != active.command_name:
            return self._protocol_fault()
        ack_fingerprint = self._model_fingerprint(message.payload)
        if active.ack_fingerprint is not None:
            if active.ack_fingerprint == ack_fingerprint:
                return ReceiveResult(ReceiveDisposition.IGNORED)
            return self._protocol_fault()
        active.ack_accepted = message.payload.accepted
        active.ack_fingerprint = ack_fingerprint
        if message.payload.accepted:
            return ReceiveResult(ReceiveDisposition.ACCEPTED)
        assert message.payload.failure is not None
        terminal = self._terminalize_local(
            TerminalOutcome.REJECTED,
            message.payload.failure.code,
            message.payload.failure.safe_detail,
        )
        return ReceiveResult(ReceiveDisposition.ACCEPTED, terminal=terminal)

    def _receive_progress(self, message: CommandProgressMessage) -> ReceiveResult:
        active = self._active
        if active is None or not self._correlates(active, message):
            return ReceiveResult(ReceiveDisposition.IGNORED)
        if active.ack_accepted is not True:
            return self._reject_receive(FailureCode.INVALID_STATE)
        if message.payload.command_name != active.command_name:
            return self._protocol_fault()
        if active.progress_elapsed_ms is not None:
            if message.payload.elapsed_ms < active.progress_elapsed_ms:
                return ReceiveResult(ReceiveDisposition.IGNORED)
            if message.payload.elapsed_ms == active.progress_elapsed_ms:
                identical = (
                    active.progress_phase == message.payload.phase
                    and active.progress_safe_detail == message.payload.safe_detail
                )
                return ReceiveResult(
                    ReceiveDisposition.IGNORED if identical else ReceiveDisposition.REJECTED,
                    None if identical else FailureCode.INVALID_STATE,
                )
        active.progress_phase = message.payload.phase
        active.progress_elapsed_ms = message.payload.elapsed_ms
        active.progress_safe_detail = message.payload.safe_detail
        return ReceiveResult(ReceiveDisposition.ACCEPTED)

    def _receive_terminal(self, message: TerminalResultMessage) -> ReceiveResult:
        self._prune_terminal_cache(self._now())
        completed = self._terminal_cache.get(message.command_id)
        if completed is not None:
            if completed.locally_terminalized:
                return ReceiveResult(ReceiveDisposition.IGNORED, terminal=completed.terminal)
            if (
                completed.connection_id == message.connection_id
                and completed.result_fingerprint == self._model_fingerprint(message.payload)
            ):
                return ReceiveResult(ReceiveDisposition.IGNORED, terminal=completed.terminal)
            return self._protocol_fault()
        active = self._active
        if active is None or not self._correlates(active, message):
            return ReceiveResult(ReceiveDisposition.IGNORED)
        if active.ack_accepted is not True:
            return self._reject_receive(FailureCode.INVALID_STATE)
        if message.payload.command_name != active.command_name:
            return self._protocol_fault()
        now = self._now()
        summary = TerminalSummary(
            command_id=active.command_id,
            trace_id=active.trace_id,
            command_name=active.command_name,
            outcome=message.payload.outcome,
            completed_at=now,
            safe_detail=message.payload.safe_detail,
            failure_code=message.payload.failure.code if message.payload.failure is not None else None,
        )
        self._store_terminal(
            active,
            summary,
            result_fingerprint=self._model_fingerprint(message.payload),
            locally_terminalized=False,
        )
        self._apply_emergency_recovery(summary)
        return ReceiveResult(ReceiveDisposition.ACCEPTED, terminal=summary)

    def _submission_failure(self, payload: CommandPayloadBase, now: datetime) -> FailureCode | None:
        name = CommandName(payload.name)
        if not self.enabled:
            return FailureCode.FEATURE_DISABLED
        if (
            self._handshake_complete
            and self._last_heartbeat_at is not None
            and now - self._last_heartbeat_at >= timedelta(seconds=self._liveness_timeout_seconds)
        ):
            self._invalidate_session(clear_transport=True)
        if not self._session_available():
            return FailureCode.SIDECAR_UNAVAILABLE
        if payload.deadline_at <= now:
            return FailureCode.DEADLINE_EXCEEDED
        if payload.deadline_at - now > timedelta(seconds=MAX_COMMAND_DEADLINE_SECONDS):
            return FailureCode.INVALID_COMMAND
        if name in _MOVEMENT_COMMANDS and self._emergency_stop_active:
            return FailureCode.EMERGENCY_STOP_ACTIVE
        if self._active is not None:
            return FailureCode.COMMAND_ALREADY_ACTIVE
        if name == CommandName.JOIN:
            if self._emergency_stop_active and not self._emergency_leave_completed:
                return FailureCode.EMERGENCY_STOP_ACTIVE
            if self._minecraft_connection_state != MinecraftConnectionState.DISCONNECTED:
                return FailureCode.INVALID_STATE
            return None
        if name == CommandName.REPORT_STATUS:
            return None
        if not self._state_fresh:
            return FailureCode.INVALID_STATE
        if name == CommandName.LEAVE:
            if self._emergency_stop_active:
                return None
            if self._minecraft_connection_state == MinecraftConnectionState.DISCONNECTED:
                return FailureCode.MINECRAFT_NOT_CONNECTED
            return None
        if self._minecraft_connection_state != MinecraftConnectionState.CONNECTED:
            return FailureCode.MINECRAFT_NOT_CONNECTED
        if self._companion_state not in _ALLOWED_STATES.get(name, set()):
            return FailureCode.INVALID_STATE
        return None

    def _duplicate_submission(
        self,
        command_id: str,
        fingerprint: tuple[Any, ...],
    ) -> SubmissionResult | None:
        if self._active is not None and self._active.command_id == command_id:
            if self._active.fingerprint != fingerprint:
                return SubmissionResult(
                    SubmissionDisposition.REJECTED,
                    command_id=command_id,
                    failure_code=FailureCode.INVALID_COMMAND,
                )
            return SubmissionResult(
                SubmissionDisposition.ACTIVE_DUPLICATE,
                command_id=command_id,
                trace_id=self._active.trace_id,
                active=self._active_status(self._active),
            )
        completed = self._terminal_cache.get(command_id)
        if completed is None:
            return None
        if completed.fingerprint != fingerprint:
            return SubmissionResult(
                SubmissionDisposition.REJECTED,
                command_id=command_id,
                failure_code=FailureCode.INVALID_COMMAND,
            )
        return SubmissionResult(
            SubmissionDisposition.CACHED_DUPLICATE,
            command_id=command_id,
            trace_id=completed.terminal.trace_id,
            terminal=completed.terminal,
        )

    async def _send(self, message: ProtocolMessage, *, invalidate_on_failure: bool = True) -> bool:
        transport = self._transport
        if transport is None:
            return False
        try:
            await transport.send(message)
        except Exception:
            self._last_failure_code = FailureCode.SIDECAR_DISCONNECTED
            if invalidate_on_failure and transport is self._transport:
                self._invalidate_session(clear_transport=True)
            return False
        return True

    def _invalidate_session(self, *, clear_transport: bool) -> None:
        if self._active is not None:
            self._terminalize_local(
                TerminalOutcome.FAILED,
                FailureCode.SIDECAR_DISCONNECTED,
                "Minecraft sidecar control session disconnected.",
            )
        self._connection_id = None
        self._handshake_complete = False
        self._sidecar_healthy = False
        self._last_heartbeat_at = None
        self._last_heartbeat_sequence = None
        self._state_fresh = False
        self._owner_present = False
        if clear_transport:
            self._transport = None

    def _terminalize_local(
        self,
        outcome: TerminalOutcome,
        failure_code: FailureCode,
        safe_detail: str | None,
        *,
        completed_at: datetime | None = None,
    ) -> TerminalSummary | None:
        active = self._active
        if active is None:
            return None
        summary = TerminalSummary(
            command_id=active.command_id,
            trace_id=active.trace_id,
            command_name=active.command_name,
            outcome=outcome,
            completed_at=completed_at or self._now(),
            safe_detail=safe_detail,
            failure_code=failure_code,
        )
        self._store_terminal(
            active,
            summary,
            result_fingerprint=None,
            locally_terminalized=True,
        )
        return summary

    def _store_terminal(
        self,
        active: _ActiveCommand,
        summary: TerminalSummary,
        *,
        result_fingerprint: tuple[Any, ...] | None,
        locally_terminalized: bool,
    ) -> None:
        self._terminal_cache[active.command_id] = _CompletedCommand(
            fingerprint=active.fingerprint,
            connection_id=active.connection_id,
            ack_fingerprint=active.ack_fingerprint,
            result_fingerprint=result_fingerprint,
            locally_terminalized=locally_terminalized,
            terminal=summary,
        )
        self._terminal_cache.move_to_end(active.command_id)
        while len(self._terminal_cache) > self._terminal_cache_capacity:
            self._terminal_cache.popitem(last=False)
        self._last_terminal = summary
        self._last_failure_code = summary.failure_code
        self._active = None

    def _apply_emergency_recovery(self, summary: TerminalSummary) -> None:
        if not self._emergency_stop_active or summary.outcome != TerminalOutcome.COMPLETED:
            return
        if summary.command_name == CommandName.LEAVE:
            self._emergency_leave_completed = True
        elif summary.command_name == CommandName.JOIN and self._emergency_leave_completed:
            self._emergency_stop_active = False
            self._emergency_command_id = None
            self._emergency_trace_id = None
            self._interrupted_command_id = None
            self._emergency_leave_completed = False

    def _prune_terminal_cache(self, now: datetime) -> None:
        expired = [
            command_id
            for command_id, completed in self._terminal_cache.items()
            if now - completed.terminal.completed_at >= self._terminal_cache_ttl
        ]
        for command_id in expired:
            self._terminal_cache.pop(command_id, None)

    def _session_available(self) -> bool:
        return (
            self._transport is not None
            and self._handshake_complete
            and self._sidecar_healthy
            and self._connection_id is not None
        )

    def _correlates(self, active: _ActiveCommand, message: object) -> bool:
        return (
            getattr(message, "connection_id", None) == active.connection_id
            and getattr(message, "command_id", None) == active.command_id
            and getattr(message, "trace_id", None) == active.trace_id
        )

    def _reject_receive(self, failure_code: FailureCode) -> ReceiveResult:
        self._last_failure_code = failure_code
        return ReceiveResult(ReceiveDisposition.REJECTED, failure_code)

    def _protocol_fault(self) -> ReceiveResult:
        self._last_failure_code = FailureCode.INVALID_STATE
        return ReceiveResult(ReceiveDisposition.REJECTED, FailureCode.INVALID_STATE)

    def _active_status(self, active: _ActiveCommand) -> ActiveCommandStatus:
        return ActiveCommandStatus(
            command_id=active.command_id,
            trace_id=active.trace_id,
            command_name=active.command_name,
            sent_at=active.sent_at,
            deadline_at=active.deadline_at,
            acknowledged=active.ack_accepted is True,
            cancellation_pending=active.cancellation_pending,
            progress_phase=active.progress_phase,
            progress_elapsed_ms=active.progress_elapsed_ms,
            progress_safe_detail=active.progress_safe_detail,
        )

    def _fingerprint(self, payload: CommandPayloadBase) -> tuple[Any, ...]:
        return self._model_fingerprint(payload)

    def _model_fingerprint(self, value: Any) -> tuple[Any, ...]:
        return self._freeze(value.model_dump(mode="json"))

    def _freeze(self, value: Any) -> tuple[Any, ...]:
        if isinstance(value, dict):
            return tuple((key, self._freeze(item)) for key, item in sorted(value.items()))
        if isinstance(value, list):
            return tuple(self._freeze(item) for item in value)
        return (value,)

    def _next_sequence(self) -> int:
        sequence = self._outbound_sequence
        self._outbound_sequence += 1
        return sequence

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("clock must return a timezone-aware UTC datetime")
        return value
