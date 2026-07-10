from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import anyio
import pytest
from pydantic import ValidationError

from gamma.integrations.minecraft.coordinator import (
    CancellationDisposition,
    EmergencyStopDisposition,
    MinecraftCoordinator,
    ReceiveDisposition,
    SubmissionDisposition,
)
from gamma.integrations.minecraft.protocol import (
    CommandAckMessage,
    CommandAckPayload,
    CommandName,
    CommandProgressMessage,
    CommandProgressPayload,
    CompanionState,
    Failure,
    FailureCode,
    FollowOwnerArguments,
    FollowOwnerCommandPayload,
    HeartbeatMessage,
    HeartbeatPayload,
    HelloMessage,
    HelloPayload,
    JoinCommandPayload,
    LeaveCommandPayload,
    MinecraftConnectionState,
    MinecraftStatusMessage,
    MinecraftStatusPayload,
    ProtocolMessage,
    StateSnapshotMessage,
    StateSnapshotPayload,
    TerminalOutcome,
    TerminalResultMessage,
    TerminalResultPayload,
    WaitHereCommandPayload,
    WelcomeMessage,
)


UTC = timezone.utc
START = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class IdSequence:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[ProtocolMessage] = []
        self.fail_next = False

    async def send(self, message: ProtocolMessage) -> None:
        if self.fail_next:
            self.fail_next = False
            raise OSError("deterministic fake transport loss")
        self.sent.append(message)


def make_coordinator(
    *,
    enabled: bool = True,
    transport: FakeTransport | None = None,
    clock: FakeClock | None = None,
    terminal_cache_capacity: int = 1_000,
    terminal_cache_ttl_seconds: int = 600,
    liveness_timeout_seconds: int = 15,
) -> tuple[MinecraftCoordinator, FakeTransport, FakeClock]:
    fake_transport = transport or FakeTransport()
    fake_clock = clock or FakeClock()
    coordinator = MinecraftCoordinator(
        enabled=enabled,
        transport=fake_transport,
        clock=fake_clock,
        message_id_factory=IdSequence("message"),
        command_id_factory=IdSequence("command"),
        trace_id_factory=IdSequence("trace"),
        connection_id_factory=IdSequence("connection"),
        terminal_cache_capacity=terminal_cache_capacity,
        terminal_cache_ttl_seconds=terminal_cache_ttl_seconds,
        liveness_timeout_seconds=liveness_timeout_seconds,
    )
    return coordinator, fake_transport, fake_clock


def hello(*, instance: str = "sidecar-1") -> HelloMessage:
    return HelloMessage(
        protocol="gamma.minecraft",
        version=1,
        type="hello",
        message_id=f"hello-{instance}",
        sent_at=START,
        sequence=0,
        payload=HelloPayload(
            supported_versions=[1],
            sidecar_instance_id=instance,
            sidecar_build="0.1.0",
            capabilities=["companion_v1"],
            node_version="22.22.2",
            minecraft_library_version="4.37.1",
            pathfinder_version="2.4.5",
            companion_state=CompanionState.DISCONNECTED,
        ),
    )


async def handshake(
    coordinator: MinecraftCoordinator,
    *,
    minecraft_state: MinecraftConnectionState | None = MinecraftConnectionState.CONNECTED,
    companion_state: CompanionState = CompanionState.IDLE,
) -> str:
    result = await coordinator.receive(hello())
    assert result.disposition == ReceiveDisposition.ACCEPTED
    connection_id = coordinator.status().connection_id
    assert connection_id is not None
    if minecraft_state is not None:
        await coordinator.receive(
            MinecraftStatusMessage(
                protocol="gamma.minecraft",
                version=1,
                type="minecraft_status",
                message_id="minecraft-status-1",
                connection_id=connection_id,
                sent_at=START,
                sequence=1,
                payload=MinecraftStatusPayload(
                    connection_state=minecraft_state,
                    companion_state=companion_state,
                    negotiated_version="1.21.11" if minecraft_state == MinecraftConnectionState.CONNECTED else None,
                    dimension="minecraft:overworld" if minecraft_state == MinecraftConnectionState.CONNECTED else None,
                ),
            )
        )
    return connection_id


def follow_payload(clock: FakeClock, *, seconds: int = 60, distance: float = 3.0) -> FollowOwnerCommandPayload:
    return FollowOwnerCommandPayload(
        name="follow_owner",
        deadline_at=clock() + timedelta(seconds=seconds),
        arguments=FollowOwnerArguments(follow_distance=distance, lease_duration_seconds=300),
    )


def ack_message(
    coordinator: MinecraftCoordinator,
    *,
    accepted: bool = True,
    connection_id: str | None = None,
    command_id: str | None = None,
    trace_id: str | None = None,
    failure_code: FailureCode = FailureCode.INVALID_STATE,
) -> CommandAckMessage:
    active = coordinator.status().active_command
    assert active is not None
    return CommandAckMessage(
        protocol="gamma.minecraft",
        version=1,
        type="command_ack",
        message_id="ack-1",
        connection_id=connection_id or coordinator.status().connection_id,
        sent_at=START,
        sequence=2,
        trace_id=trace_id or active.trace_id,
        command_id=command_id or active.command_id,
        payload=CommandAckPayload(
            accepted=accepted,
            command_name=active.command_name,
            failure=None
            if accepted
            else Failure(code=failure_code, safe_detail="Rejected safely.", retriable=False),
        ),
    )


def progress_message(
    coordinator: MinecraftCoordinator,
    *,
    elapsed_ms: int = 100,
    phase: str = "moving",
    safe_detail: str | None = "Following at bounded distance.",
    connection_id: str | None = None,
    command_id: str | None = None,
) -> CommandProgressMessage:
    active = coordinator.status().active_command
    assert active is not None
    return CommandProgressMessage(
        protocol="gamma.minecraft",
        version=1,
        type="command_progress",
        message_id=f"progress-{elapsed_ms}",
        connection_id=connection_id or coordinator.status().connection_id,
        sent_at=START,
        sequence=3,
        trace_id=active.trace_id,
        command_id=command_id or active.command_id,
        payload=CommandProgressPayload(
            command_name=active.command_name,
            phase=phase,
            elapsed_ms=elapsed_ms,
            safe_detail=safe_detail,
        ),
    )


def terminal_message(
    coordinator: MinecraftCoordinator,
    outcome: TerminalOutcome,
    *,
    connection_id: str | None = None,
    command_id: str | None = None,
    safe_detail: str | None = "Bounded terminal detail.",
) -> TerminalResultMessage:
    active = coordinator.status().active_command
    assert active is not None
    needs_failure = outcome in {
        TerminalOutcome.FAILED,
        TerminalOutcome.REJECTED,
        TerminalOutcome.TIMED_OUT,
    }
    return TerminalResultMessage(
        protocol="gamma.minecraft",
        version=1,
        type="terminal_result",
        message_id=f"result-{outcome.value}",
        connection_id=connection_id or coordinator.status().connection_id,
        sent_at=START,
        sequence=4,
        trace_id=active.trace_id,
        command_id=command_id or active.command_id,
        payload=TerminalResultPayload(
            command_name=active.command_name,
            outcome=outcome,
            elapsed_ms=500,
            safe_detail=safe_detail,
            failure=Failure(
                code=FailureCode.PATH_NOT_FOUND,
                safe_detail="No bounded path.",
                retriable=True,
            )
            if needs_failure
            else None,
        ),
    )


async def submit_follow(
    coordinator: MinecraftCoordinator,
    clock: FakeClock,
    *,
    command_id: str = "follow-command",
):
    return await coordinator.submit_command(
        follow_payload(clock),
        command_id=command_id,
        trace_id=f"trace-{command_id}",
    )


def test_safe_defaults_are_disabled_nonmoving_and_bounded() -> None:
    coordinator = MinecraftCoordinator()

    status = coordinator.status()

    assert status.enabled is False
    assert status.transport_attached is False
    assert status.handshake_complete is False
    assert status.sidecar_healthy is False
    assert status.active_command is None
    assert status.companion_state == CompanionState.DISCONNECTED
    assert status.minecraft_connection_state == MinecraftConnectionState.DISCONNECTED
    assert "transport" not in status.__dataclass_fields__
    assert "payload" not in status.__dataclass_fields__
    assert "secret" not in repr(status).lower()
    with pytest.raises(FrozenInstanceError):
        status.enabled = True  # type: ignore[misc]
    assert not hasattr(coordinator, "clear_emergency_stop")


def test_valid_hello_sends_welcome_and_records_bounded_identity() -> None:
    async def run() -> None:
        coordinator, transport, _clock = make_coordinator()

        result = await coordinator.receive(hello(instance="bounded-sidecar"))

        assert result.disposition == ReceiveDisposition.ACCEPTED
        assert len(transport.sent) == 1
        welcome = transport.sent[0]
        assert isinstance(welcome, WelcomeMessage)
        assert welcome.connection_id == "connection-1"
        assert welcome.message_id == "message-1"
        assert welcome.sequence == 0
        assert welcome.payload.heartbeat_interval_seconds == 5
        assert welcome.payload.liveness_timeout_seconds == 15
        status = coordinator.status()
        assert status.handshake_complete is True
        assert status.sidecar_healthy is True
        assert status.sidecar_instance_id == "bounded-sidecar"
        assert status.sidecar_build == "0.1.0"
        assert status.active_command is None
        assert status.companion_state == CompanionState.DISCONNECTED

    anyio.run(run)


def test_failed_welcome_send_does_not_establish_a_session() -> None:
    async def run() -> None:
        transport = FakeTransport()
        transport.fail_next = True
        coordinator, _transport, _clock = make_coordinator(transport=transport)

        result = await coordinator.receive(hello())

        assert result.disposition == ReceiveDisposition.REJECTED
        assert result.failure_code == FailureCode.SIDECAR_UNAVAILABLE
        assert transport.sent == []
        status = coordinator.status()
        assert status.transport_attached is False
        assert status.handshake_complete is False
        assert status.sidecar_healthy is False
        assert status.connection_id is None

    anyio.run(run)


def test_fresh_state_snapshot_updates_only_bounded_reported_status() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        connection = await handshake(coordinator, minecraft_state=None)
        snapshot = StateSnapshotMessage(
            protocol="gamma.minecraft",
            version=1,
            type="state_snapshot",
            message_id="snapshot-1",
            connection_id=connection,
            sent_at=clock(),
            sequence=2,
            payload=StateSnapshotPayload(
                sidecar_connection_state="connected",
                minecraft_connection_state=MinecraftConnectionState.CONNECTED,
                companion_state=CompanionState.IDLE,
                owner_present=True,
                owner_display_name="Owner",
                health=20,
                hunger=20,
            ),
        )

        assert (await coordinator.receive(snapshot)).disposition == ReceiveDisposition.ACCEPTED
        status = coordinator.status()
        assert status.minecraft_connection_state == MinecraftConnectionState.CONNECTED
        assert status.companion_state == CompanionState.IDLE
        assert status.owner_present is True
        assert "owner_display_name" not in status.__dataclass_fields__
        assert "health" not in status.__dataclass_fields__

    anyio.run(run)


def test_unsupported_hello_and_prehandshake_messages_are_rejected() -> None:
    async def run() -> None:
        coordinator, transport, _clock = make_coordinator()
        unvalidated_payload = HelloPayload.model_construct(
            supported_versions=[2],
            sidecar_instance_id="sidecar-2",
            sidecar_build="0.2.0",
            capabilities=["companion_v1"],
            node_version="22.22.2",
            minecraft_library_version="4.37.1",
            pathfinder_version="2.4.5",
            companion_state=CompanionState.DISCONNECTED,
        )
        unsupported = HelloMessage.model_construct(
            protocol="gamma.minecraft",
            version=1,
            type="hello",
            message_id="hello-unsupported",
            sent_at=START,
            sequence=0,
            payload=unvalidated_payload,
        )

        mismatch = await coordinator.receive(unsupported)

        assert mismatch.failure_code == FailureCode.PROTOCOL_MISMATCH
        assert transport.sent[-1].type == "protocol_error"
        assert transport.sent[-1].payload.code == FailureCode.PROTOCOL_MISMATCH
        assert coordinator.status().handshake_complete is False

        heartbeat = HeartbeatMessage(
            protocol="gamma.minecraft",
            version=1,
            type="heartbeat",
            message_id="premature-heartbeat",
            connection_id="not-established",
            sent_at=START,
            sequence=1,
            payload=HeartbeatPayload(companion_state=CompanionState.IDLE),
        )
        premature = await coordinator.receive(heartbeat)
        assert premature.disposition == ReceiveDisposition.REJECTED
        assert premature.failure_code == FailureCode.SIDECAR_UNAVAILABLE

    anyio.run(run)


def test_new_handshake_invalidates_old_work_and_stale_messages() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        old_connection = await handshake(coordinator)
        sent = await submit_follow(coordinator, clock)
        old_active = sent.active
        assert old_active is not None

        second = await coordinator.receive(hello(instance="replacement"))

        assert second.disposition == ReceiveDisposition.ACCEPTED
        status = coordinator.status()
        assert status.connection_id == "connection-2"
        assert status.active_command is None
        assert status.last_terminal is not None
        assert status.last_terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED
        assert [message.type for message in transport.sent].count("command") == 1

        stale = CommandAckMessage(
            protocol="gamma.minecraft",
            version=1,
            type="command_ack",
            message_id="stale-ack",
            connection_id=old_connection,
            sent_at=clock(),
            sequence=9,
            trace_id=old_active.trace_id,
            command_id=old_active.command_id,
            payload=CommandAckPayload(
                accepted=True,
                command_name=old_active.command_name,
            ),
        )
        ignored = await coordinator.receive(stale)
        assert ignored.disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command is None

    anyio.run(run)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("disabled", FailureCode.FEATURE_DISABLED),
        ("unattached", FailureCode.SIDECAR_UNAVAILABLE),
        ("unhandshaken", FailureCode.SIDECAR_UNAVAILABLE),
        ("disconnected", FailureCode.MINECRAFT_NOT_CONNECTED),
    ],
)
def test_feature_and_availability_gates(setup: str, expected: FailureCode) -> None:
    async def run() -> None:
        clock = FakeClock()
        if setup == "disabled":
            coordinator = MinecraftCoordinator(enabled=False, clock=clock)
        elif setup == "unattached":
            coordinator = MinecraftCoordinator(enabled=True, clock=clock)
        else:
            coordinator, _transport, clock = make_coordinator()
            if setup == "disconnected":
                await handshake(
                    coordinator,
                    minecraft_state=MinecraftConnectionState.DISCONNECTED,
                    companion_state=CompanionState.DISCONNECTED,
                )
        result = await coordinator.submit_command(follow_payload(clock))
        assert result.disposition == SubmissionDisposition.REJECTED
        assert result.failure_code == expected

    anyio.run(run)


def test_stale_handshake_rejects_without_explicit_liveness_call() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        clock.advance(seconds=16)

        result = await submit_follow(coordinator, clock)

        assert result.failure_code == FailureCode.SIDECAR_UNAVAILABLE
        assert coordinator.status().transport_attached is False
        assert coordinator.status().handshake_complete is False

    anyio.run(run)


def test_valid_submission_uses_injected_ids_time_and_only_one_slot() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)

        first = await coordinator.submit_command(follow_payload(clock))
        second = await coordinator.submit_command(
            WaitHereCommandPayload(name="wait_here", deadline_at=clock() + timedelta(seconds=2))
        )

        assert first.disposition == SubmissionDisposition.SENT
        assert first.command_id == "command-1"
        assert first.trace_id == "trace-1"
        command = transport.sent[-1]
        assert command.type == "command"
        assert command.message_id == "message-2"
        assert command.sent_at == START
        assert command.payload == follow_payload(clock)
        assert second.failure_code == FailureCode.COMMAND_ALREADY_ACTIVE
        assert [message.type for message in transport.sent].count("command") == 1

    anyio.run(run)


def test_deadline_validation_and_raw_dictionary_rejection() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)

        expired = await coordinator.submit_command(follow_payload(clock, seconds=0))
        too_far = await coordinator.submit_command(follow_payload(clock, seconds=901))
        raw = await coordinator.submit_command(  # type: ignore[arg-type]
            {"name": "follow_owner", "deadline_at": clock().isoformat(), "arguments": {}}
        )

        assert expired.failure_code == FailureCode.DEADLINE_EXCEEDED
        assert too_far.failure_code == FailureCode.INVALID_COMMAND
        assert raw.failure_code == FailureCode.INVALID_COMMAND

    anyio.run(run)


def test_send_failure_invalidates_session_and_terminalizes_once() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        transport.fail_next = True

        result = await submit_follow(coordinator, clock)

        assert result.failure_code == FailureCode.SIDECAR_UNAVAILABLE
        assert result.terminal is not None
        assert result.terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED
        assert coordinator.status().active_command is None
        assert coordinator.status().transport_attached is False

    anyio.run(run)


def test_accepted_ack_is_idempotent_and_conflict_is_safe() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        accepted = ack_message(coordinator)

        first = await coordinator.receive(accepted)
        duplicate = await coordinator.receive(accepted)
        conflicting = await coordinator.receive(
            accepted.model_copy(
                update={
                    "payload": CommandAckPayload(
                        accepted=False,
                        command_name=CommandName.FOLLOW_OWNER,
                        failure=Failure(
                            code=FailureCode.INVALID_STATE,
                            safe_detail="Conflict.",
                            retriable=False,
                        ),
                    )
                }
            )
        )

        assert first.disposition == ReceiveDisposition.ACCEPTED
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert conflicting.disposition == ReceiveDisposition.REJECTED
        assert coordinator.status().active_command is not None
        assert coordinator.status().active_command.acknowledged is True

    anyio.run(run)


def test_rejected_ack_terminalizes_and_unknown_or_stale_ack_is_safe() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        connection = await handshake(coordinator)
        await submit_follow(coordinator, clock)
        active = coordinator.status().active_command
        assert active is not None

        stale = ack_message(coordinator, connection_id="old-connection")
        unknown = ack_message(coordinator, command_id="unknown-command")
        assert (await coordinator.receive(stale)).disposition == ReceiveDisposition.IGNORED
        assert (await coordinator.receive(unknown)).disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command == active

        rejected_message = ack_message(
            coordinator,
            accepted=False,
            connection_id=connection,
            failure_code=FailureCode.OWNER_NOT_PRESENT,
        )
        rejected = await coordinator.receive(rejected_message)
        duplicate = await coordinator.receive(rejected_message)
        assert rejected.terminal is not None
        assert rejected.terminal.outcome == TerminalOutcome.REJECTED
        assert rejected.terminal.failure_code == FailureCode.OWNER_NOT_PRESENT
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command is None

    anyio.run(run)


def test_progress_requires_ack_is_bounded_and_never_extends_deadline() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        deadline = coordinator.status().active_command.deadline_at  # type: ignore[union-attr]

        premature = await coordinator.receive(progress_message(coordinator))
        assert premature.failure_code == FailureCode.INVALID_STATE
        await coordinator.receive(ack_message(coordinator))
        progress = progress_message(coordinator, elapsed_ms=250, safe_detail="x" * 256)
        accepted = await coordinator.receive(progress)
        duplicate = await coordinator.receive(progress)
        stale = await coordinator.receive(progress_message(coordinator, elapsed_ms=100))

        active = coordinator.status().active_command
        assert accepted.disposition == ReceiveDisposition.ACCEPTED
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert stale.disposition == ReceiveDisposition.IGNORED
        assert active is not None
        assert active.progress_phase == "moving"
        assert active.progress_safe_detail == "x" * 256
        assert active.deadline_at == deadline
        with pytest.raises(ValidationError):
            progress_message(coordinator, safe_detail="x" * 257)

    anyio.run(run)


def test_progress_for_unknown_or_stale_command_does_not_mutate() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.receive(ack_message(coordinator))
        before = coordinator.status().active_command

        stale = progress_message(coordinator, connection_id="old-connection")
        unknown = progress_message(coordinator, command_id="unknown-command")
        assert (await coordinator.receive(stale)).disposition == ReceiveDisposition.IGNORED
        assert (await coordinator.receive(unknown)).disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command == before

    anyio.run(run)


def test_terminal_result_before_accepted_ack_is_rejected_without_finalizing() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)

        premature = await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))

        assert premature.disposition == ReceiveDisposition.REJECTED
        assert premature.failure_code == FailureCode.INVALID_STATE
        assert coordinator.status().active_command is not None
        assert coordinator.status().last_terminal is None

    anyio.run(run)


@pytest.mark.parametrize("outcome", list(TerminalOutcome))
def test_every_terminal_outcome_finalizes_once_and_is_cached(outcome: TerminalOutcome) -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.receive(ack_message(coordinator))
        result_message = terminal_message(coordinator, outcome)

        first = await coordinator.receive(result_message)
        duplicate = await coordinator.receive(result_message)

        assert first.terminal is not None
        assert first.terminal.outcome == outcome
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert duplicate.terminal == first.terminal
        assert coordinator.status().active_command is None
        cached = await coordinator.submit_command(
            follow_payload(clock),
            command_id="follow-command",
            trace_id="different-trace-is-not-used",
        )
        assert cached.disposition == SubmissionDisposition.CACHED_DUPLICATE
        assert cached.terminal == first.terminal

    anyio.run(run)


def test_conflicting_duplicate_terminal_and_unknown_terminal_are_safe() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.receive(ack_message(coordinator))
        completed = terminal_message(coordinator, TerminalOutcome.COMPLETED)
        first = await coordinator.receive(completed)
        assert first.terminal is not None
        original = coordinator.status().last_terminal

        conflict_payload = TerminalResultPayload(
            command_name=completed.payload.command_name,
            outcome=TerminalOutcome.CANCELLED,
            elapsed_ms=500,
            safe_detail="Conflicting duplicate.",
        )
        conflict = await coordinator.receive(completed.model_copy(update={"payload": conflict_payload}))
        assert conflict.disposition == ReceiveDisposition.REJECTED
        assert coordinator.status().last_terminal == original

        await coordinator.submit_command(
            follow_payload(clock), command_id="current-command", trace_id="trace-current"
        )
        before = coordinator.status().active_command
        unknown = terminal_message(coordinator, TerminalOutcome.COMPLETED, command_id="unknown-command")
        assert (await coordinator.receive(unknown)).disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command == before

    anyio.run(run)


def test_active_duplicate_ids_are_not_resent_and_conflicts_are_rejected() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        original = follow_payload(clock)
        await coordinator.submit_command(original, command_id="stable-id", trace_id="trace-stable")

        identical = await coordinator.submit_command(
            original, command_id="stable-id", trace_id="unused-trace"
        )
        conflicting = await coordinator.submit_command(
            follow_payload(clock, distance=4.0), command_id="stable-id"
        )

        assert identical.disposition == SubmissionDisposition.ACTIVE_DUPLICATE
        assert identical.trace_id == "trace-stable"
        assert conflicting.failure_code == FailureCode.INVALID_COMMAND
        assert [message.type for message in transport.sent].count("command") == 1

    anyio.run(run)


def test_caller_cannot_mutate_active_command_or_outbound_snapshot() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        original = follow_payload(clock)
        await coordinator.submit_command(original, command_id="stable-id", trace_id="trace-stable")

        original.arguments.follow_distance = 6.0
        original.name = "leave"  # type: ignore[assignment]

        active = coordinator.status().active_command
        assert active is not None
        assert active.command_name == CommandName.FOLLOW_OWNER
        outbound = transport.sent[-1]
        assert outbound.type == "command"
        assert outbound.payload.name == "follow_owner"
        assert outbound.payload.arguments.follow_distance == 3.0
        duplicate = await coordinator.submit_command(
            follow_payload(clock), command_id="stable-id", trace_id="unused-trace"
        )
        assert duplicate.disposition == SubmissionDisposition.ACTIVE_DUPLICATE

    anyio.run(run)


def test_completed_duplicate_conflict_and_count_eviction() -> None:
    async def complete(
        coordinator: MinecraftCoordinator,
        clock: FakeClock,
        command_id: str,
        distance: float,
    ) -> None:
        await coordinator.submit_command(
            follow_payload(clock, distance=distance),
            command_id=command_id,
            trace_id=f"trace-{command_id}",
        )
        await coordinator.receive(ack_message(coordinator))
        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))

    async def run() -> None:
        coordinator, transport, clock = make_coordinator(terminal_cache_capacity=2)
        await handshake(coordinator)
        await complete(coordinator, clock, "one", 2.0)
        conflict = await coordinator.submit_command(
            follow_payload(clock, distance=3.0), command_id="one"
        )
        assert conflict.failure_code == FailureCode.INVALID_COMMAND
        await complete(coordinator, clock, "two", 3.0)
        await complete(coordinator, clock, "three", 4.0)
        sent_before = [message.type for message in transport.sent].count("command")

        evicted = await coordinator.submit_command(
            follow_payload(clock, distance=2.0), command_id="one", trace_id="trace-one-new"
        )

        assert evicted.disposition == SubmissionDisposition.SENT
        assert [message.type for message in transport.sent].count("command") == sent_before + 1

    anyio.run(run)


def test_cached_duplicate_access_does_not_change_oldest_count_eviction() -> None:
    async def complete(
        coordinator: MinecraftCoordinator,
        clock: FakeClock,
        command_id: str,
        distance: float,
    ) -> None:
        await coordinator.submit_command(
            follow_payload(clock, distance=distance),
            command_id=command_id,
            trace_id=f"trace-{command_id}",
        )
        await coordinator.receive(ack_message(coordinator))
        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))

    async def run() -> None:
        coordinator, transport, clock = make_coordinator(terminal_cache_capacity=2)
        await handshake(coordinator)
        await complete(coordinator, clock, "one", 2.0)
        await complete(coordinator, clock, "two", 3.0)
        assert (
            await coordinator.submit_command(follow_payload(clock, distance=2.0), command_id="one")
        ).disposition == SubmissionDisposition.CACHED_DUPLICATE
        await complete(coordinator, clock, "three", 4.0)

        assert (
            await coordinator.submit_command(follow_payload(clock, distance=3.0), command_id="two")
        ).disposition == SubmissionDisposition.CACHED_DUPLICATE
        sent_before = [message.type for message in transport.sent].count("command")
        evicted = await coordinator.submit_command(
            follow_payload(clock, distance=2.0), command_id="one", trace_id="trace-one-new"
        )
        assert evicted.disposition == SubmissionDisposition.SENT
        assert [message.type for message in transport.sent].count("command") == sent_before + 1

    anyio.run(run)


def test_terminal_cache_expires_at_exact_ttl_without_access_extension() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator(
            terminal_cache_ttl_seconds=60,
            liveness_timeout_seconds=180,
        )
        await handshake(coordinator)
        payload = follow_payload(clock, seconds=120)
        await coordinator.submit_command(payload, command_id="ttl-command", trace_id="trace-ttl")
        await coordinator.receive(ack_message(coordinator))
        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))

        clock.advance(seconds=59)
        cached = await coordinator.submit_command(payload, command_id="ttl-command")
        assert cached.disposition == SubmissionDisposition.CACHED_DUPLICATE
        sent_before = [message.type for message in transport.sent].count("command")
        clock.advance(seconds=1)
        expired = await coordinator.submit_command(payload, command_id="ttl-command")
        assert expired.disposition == SubmissionDisposition.SENT
        assert [message.type for message in transport.sent].count("command") == sent_before + 1

    anyio.run(run)


def test_cancellation_is_idempotent_and_terminal_result_finishes_once() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.receive(ack_message(coordinator))

        unknown = await coordinator.cancel_active_command("unknown")
        first = await coordinator.cancel_active_command("follow-command", reason="Owner requested stop.")
        repeated = await coordinator.cancel_active_command("follow-command", reason="Ignored duplicate.")

        assert unknown.disposition == CancellationDisposition.NO_ACTIVE_COMMAND
        assert first.disposition == CancellationDisposition.SENT
        assert repeated.disposition == CancellationDisposition.ALREADY_PENDING
        assert [message.type for message in transport.sent].count("cancel_command") == 1
        assert coordinator.status().active_command is not None
        assert coordinator.status().active_command.cancellation_pending is True

        result = await coordinator.receive(terminal_message(coordinator, TerminalOutcome.CANCELLED))
        assert result.terminal is not None
        assert result.terminal.outcome == TerminalOutcome.CANCELLED
        assert coordinator.status().active_command is None

    anyio.run(run)


def test_transport_loss_while_cancellation_pending_terminalizes_safely() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.cancel_active_command("follow-command")

        assert coordinator.detach_transport(transport) is True
        assert coordinator.detach_transport(transport) is False
        terminal = coordinator.status().last_terminal
        assert terminal is not None
        assert terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED
        assert coordinator.status().active_command is None

    anyio.run(run)


def test_cancellation_send_failure_never_claims_delivery() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        transport.fail_next = True

        result = await coordinator.cancel_active_command("follow-command")

        assert result.disposition == CancellationDisposition.REJECTED
        assert result.failure_code == FailureCode.SIDECAR_UNAVAILABLE
        assert [message.type for message in transport.sent].count("cancel_command") == 0
        status = coordinator.status()
        assert status.active_command is None
        assert status.last_terminal is not None
        assert status.last_terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED

    anyio.run(run)


def test_emergency_stop_bypasses_active_slot_latches_and_sends_once() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)

        first = await coordinator.activate_emergency_stop(reason="Immediate safe stop.")
        repeated = await coordinator.activate_emergency_stop(reason="Repeated stop.")
        blocked = await coordinator.submit_command(
            WaitHereCommandPayload(name="wait_here", deadline_at=clock() + timedelta(seconds=2))
        )

        assert first.disposition == EmergencyStopDisposition.DELIVERED
        assert first.delivered is True
        assert first.interrupted_command_id == "follow-command"
        assert repeated.disposition == EmergencyStopDisposition.ALREADY_LATCHED
        assert [message.type for message in transport.sent].count("emergency_stop") == 1
        assert blocked.failure_code == FailureCode.EMERGENCY_STOP_ACTIVE
        assert coordinator.status().emergency_stop_active is True

    anyio.run(run)


def test_unavailable_emergency_stop_latches_locally_and_handshake_does_not_clear() -> None:
    async def run() -> None:
        coordinator = MinecraftCoordinator(
            enabled=True,
            clock=FakeClock(),
            message_id_factory=IdSequence("message"),
            command_id_factory=IdSequence("command"),
            trace_id_factory=IdSequence("trace"),
            connection_id_factory=IdSequence("connection"),
        )

        stopped = await coordinator.activate_emergency_stop()
        assert stopped.disposition == EmergencyStopDisposition.LATCHED_LOCALLY
        assert stopped.delivered is False
        assert coordinator.status().emergency_stop_active is True

        transport = FakeTransport()
        coordinator.attach_transport(transport)
        await coordinator.receive(hello())
        assert coordinator.status().emergency_stop_active is True

    anyio.run(run)


def test_emergency_stop_send_failure_preserves_local_latch() -> None:
    async def run() -> None:
        coordinator, transport, _clock = make_coordinator()
        await handshake(coordinator)
        transport.fail_next = True

        result = await coordinator.activate_emergency_stop(reason="Immediate safe stop.")

        assert result.disposition == EmergencyStopDisposition.LATCHED_LOCALLY
        assert result.delivered is False
        assert [message.type for message in transport.sent].count("emergency_stop") == 0
        status = coordinator.status()
        assert status.emergency_stop_active is True
        assert status.transport_attached is False
        assert status.handshake_complete is False

    anyio.run(run)


def test_emergency_latch_clears_only_after_completed_leave_and_fresh_join() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await coordinator.activate_emergency_stop()

        leave = LeaveCommandPayload(name="leave", deadline_at=clock() + timedelta(seconds=15))
        sent_leave = await coordinator.submit_command(leave, command_id="leave-1", trace_id="trace-leave")
        assert sent_leave.disposition == SubmissionDisposition.SENT
        await coordinator.receive(ack_message(coordinator))
        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))
        assert coordinator.status().emergency_stop_active is True

        connection = coordinator.status().connection_id
        assert connection is not None
        await coordinator.receive(
            MinecraftStatusMessage(
                protocol="gamma.minecraft",
                version=1,
                type="minecraft_status",
                message_id="disconnected-after-leave",
                connection_id=connection,
                sent_at=clock(),
                sequence=8,
                payload=MinecraftStatusPayload(
                    connection_state=MinecraftConnectionState.DISCONNECTED,
                    companion_state=CompanionState.STOPPED,
                ),
            )
        )
        join = JoinCommandPayload(name="join", deadline_at=clock() + timedelta(seconds=45))
        sent_join = await coordinator.submit_command(join, command_id="join-1", trace_id="trace-join")
        assert sent_join.disposition == SubmissionDisposition.SENT
        await coordinator.receive(ack_message(coordinator))
        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))
        assert coordinator.status().emergency_stop_active is False

    anyio.run(run)


def test_heartbeat_liveness_and_disconnect_are_deterministic() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        connection = await handshake(coordinator)
        clock.advance(seconds=4)
        heartbeat = HeartbeatMessage(
            protocol="gamma.minecraft",
            version=1,
            type="heartbeat",
            message_id="heartbeat-current",
            connection_id=connection,
            sent_at=clock(),
            sequence=2,
            payload=HeartbeatPayload(companion_state=CompanionState.IDLE),
        )
        assert (await coordinator.receive(heartbeat)).disposition == ReceiveDisposition.ACCEPTED
        assert coordinator.status().last_heartbeat_at == clock()

        stale = heartbeat.model_copy(update={"connection_id": "old-connection"})
        clock.advance(seconds=10)
        before = coordinator.status().last_heartbeat_at
        assert (await coordinator.receive(stale)).disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().last_heartbeat_at == before
        assert coordinator.check_liveness() is True

        await submit_follow(coordinator, clock)
        clock.advance(seconds=6)
        assert coordinator.check_liveness() is False
        status = coordinator.status()
        assert status.handshake_complete is False
        assert status.transport_attached is False
        assert status.active_command is None
        assert status.last_terminal is not None
        assert status.last_terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED

    anyio.run(run)


def test_heartbeat_time_is_monotonic_and_exact_stale_boundary_disconnects() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator(liveness_timeout_seconds=15)
        connection = await handshake(coordinator)
        clock.advance(seconds=10)
        heartbeat = HeartbeatMessage(
            protocol="gamma.minecraft",
            version=1,
            type="heartbeat",
            message_id="heartbeat-monotonic",
            connection_id=connection,
            sent_at=clock(),
            sequence=2,
            payload=HeartbeatPayload(companion_state=CompanionState.IDLE),
        )
        await coordinator.receive(heartbeat)
        last_heartbeat = coordinator.status().last_heartbeat_at

        clock.value = START + timedelta(seconds=5)
        duplicate = await coordinator.receive(
            heartbeat.model_copy(update={"message_id": "heartbeat-backward"})
        )
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().last_heartbeat_at == last_heartbeat

        clock.value = START + timedelta(seconds=20)
        duplicate = await coordinator.receive(
            heartbeat.model_copy(update={"message_id": "heartbeat-duplicate-later"})
        )
        assert duplicate.disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().last_heartbeat_at == last_heartbeat

        clock.value = START + timedelta(seconds=24, milliseconds=999)
        assert coordinator.check_liveness() is True
        clock.value = START + timedelta(seconds=25)
        assert coordinator.check_liveness() is False
        assert coordinator.status().handshake_complete is False

    anyio.run(run)


def test_reconnect_never_replays_and_old_result_cannot_affect_new_work() -> None:
    async def run() -> None:
        coordinator, transport, clock = make_coordinator()
        old_connection = await handshake(coordinator)
        await submit_follow(coordinator, clock, command_id="old-command")
        old_active = coordinator.status().active_command
        assert old_active is not None
        coordinator.detach_transport(transport)

        replacement = FakeTransport()
        coordinator.attach_transport(replacement)
        await handshake(coordinator)
        assert [message.type for message in replacement.sent] == ["welcome"]

        stale_result = TerminalResultMessage(
            protocol="gamma.minecraft",
            version=1,
            type="terminal_result",
            message_id="old-result",
            connection_id=old_connection,
            sent_at=clock(),
            sequence=9,
            trace_id=old_active.trace_id,
            command_id=old_active.command_id,
            payload=TerminalResultPayload(
                command_name=old_active.command_name,
                outcome=TerminalOutcome.COMPLETED,
                elapsed_ms=100,
            ),
        )
        assert (await coordinator.receive(stale_result)).disposition == ReceiveDisposition.IGNORED
        assert coordinator.status().active_command is None

    anyio.run(run)


def test_deadline_expiry_is_cached_and_progress_does_not_delay_it() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        payload = follow_payload(clock, seconds=10)
        await coordinator.submit_command(payload, command_id="deadline-command", trace_id="trace-deadline")
        await coordinator.receive(ack_message(coordinator))
        clock.advance(seconds=9)
        await coordinator.receive(progress_message(coordinator, elapsed_ms=9_000))
        assert coordinator.check_deadlines() is None

        clock.advance(seconds=1)
        terminal = coordinator.check_deadlines()

        assert terminal is not None
        assert terminal.outcome == TerminalOutcome.TIMED_OUT
        assert terminal.failure_code == FailureCode.DEADLINE_EXCEEDED
        assert coordinator.status().active_command is None
        duplicate = await coordinator.submit_command(
            payload, command_id="deadline-command", trace_id="unused"
        )
        assert duplicate.disposition == SubmissionDisposition.CACHED_DUPLICATE
        assert duplicate.terminal == terminal

    anyio.run(run)


def test_late_result_after_local_timeout_is_harmless() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await coordinator.submit_command(
            follow_payload(clock, seconds=10),
            command_id="late-timeout",
            trace_id="trace-late-timeout",
        )
        await coordinator.receive(ack_message(coordinator))
        late = terminal_message(coordinator, TerminalOutcome.COMPLETED)
        clock.advance(seconds=10)
        timeout = coordinator.check_deadlines()
        assert timeout is not None

        received = await coordinator.receive(late)

        assert received.disposition == ReceiveDisposition.IGNORED
        assert received.terminal == timeout
        assert coordinator.status().last_terminal == timeout
        assert coordinator.status().last_failure_code == FailureCode.DEADLINE_EXCEEDED

    anyio.run(run)


def test_successful_terminal_clears_unrelated_previous_failure() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        accepted = ack_message(coordinator)
        await coordinator.receive(accepted)
        conflict = accepted.model_copy(
            update={
                "payload": CommandAckPayload(
                    accepted=False,
                    command_name=CommandName.FOLLOW_OWNER,
                    failure=Failure(code=FailureCode.INVALID_STATE, retriable=False),
                )
            }
        )
        assert (await coordinator.receive(conflict)).disposition == ReceiveDisposition.REJECTED
        assert coordinator.status().last_failure_code == FailureCode.INVALID_STATE

        await coordinator.receive(terminal_message(coordinator, TerminalOutcome.COMPLETED))
        assert coordinator.status().last_failure_code is None

    anyio.run(run)


def test_reset_clears_actionable_state_but_preserves_emergency_latch() -> None:
    async def run() -> None:
        coordinator, _transport, clock = make_coordinator()
        await handshake(coordinator)
        await submit_follow(coordinator, clock)
        await coordinator.activate_emergency_stop()

        coordinator.reset()

        status = coordinator.status()
        assert status.enabled is True
        assert status.transport_attached is False
        assert status.handshake_complete is False
        assert status.sidecar_healthy is False
        assert status.connection_id is None
        assert status.active_command is None
        assert status.last_terminal is None
        assert status.emergency_stop_active is True
        assert status.interrupted_command_id == "follow-command"
        assert status.companion_state == CompanionState.DISCONNECTED
        assert status.minecraft_connection_state == MinecraftConnectionState.DISCONNECTED
        assert not hasattr(coordinator, "_task")
        repeated = await coordinator.activate_emergency_stop()
        assert repeated.disposition == EmergencyStopDisposition.ALREADY_LATCHED

    anyio.run(run)
