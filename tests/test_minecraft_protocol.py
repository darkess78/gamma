from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gamma.integrations.minecraft.protocol import (
    PROTOCOL_MESSAGE_ADAPTER,
    CancelCommandMessage,
    CommandAckMessage,
    CommandMessage,
    CommandProgressMessage,
    EmergencyStopMessage,
    FailureCode,
    HeartbeatMessage,
    HelloMessage,
    MinecraftStatusMessage,
    ProtocolErrorMessage,
    ShutdownMessage,
    SidecarStatusMessage,
    StateSnapshotMessage,
    TerminalOutcome,
    TerminalResultMessage,
    WelcomeMessage,
    parse_protocol_message,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "minecraft_protocol" / "v1"

FIXTURE_MODELS = {
    "hello.json": HelloMessage,
    "welcome.json": WelcomeMessage,
    "heartbeat.json": HeartbeatMessage,
    "sidecar-status.json": SidecarStatusMessage,
    "minecraft-status.json": MinecraftStatusMessage,
    "command-follow-owner.json": CommandMessage,
    "command-ack.json": CommandAckMessage,
    "command-progress.json": CommandProgressMessage,
    "state-snapshot.json": StateSnapshotMessage,
    "terminal-result.json": TerminalResultMessage,
    "cancel-command.json": CancelCommandMessage,
    "emergency-stop.json": EmergencyStopMessage,
    "shutdown.json": ShutdownMessage,
    "protocol-error.json": ProtocolErrorMessage,
}


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _validate(data: dict[str, object]):
    return PROTOCOL_MESSAGE_ADAPTER.validate_python(data)


def _assert_invalid(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize(("fixture_name", "model"), FIXTURE_MODELS.items())
def test_valid_fixture_parses_as_corresponding_model(
    fixture_name: str,
    model: type,
) -> None:
    raw = (FIXTURE_ROOT / fixture_name).read_bytes()
    parsed = parse_protocol_message(raw)

    assert isinstance(parsed, model)
    assert parsed.protocol == "gamma.minecraft"
    assert parsed.version == 1


def test_discriminated_parser_selects_message_and_command_payload() -> None:
    parsed = _validate(_fixture("command-follow-owner.json"))

    assert isinstance(parsed, CommandMessage)
    assert parsed.type == "command"
    assert parsed.payload.name == "follow_owner"
    assert type(parsed.payload).__name__ == "FollowOwnerCommandPayload"


@pytest.mark.parametrize(
    ("name", "arguments", "payload_class"),
    [
        ("join", {"connection_profile_id": "private-dev"}, "JoinCommandPayload"),
        ("leave", {"reason": "Owner request."}, "LeaveCommandPayload"),
        (
            "follow_owner",
            {"follow_distance": 3.0, "lease_duration_seconds": 300},
            "FollowOwnerCommandPayload",
        ),
        ("wait_here", {"reason": "Hold this position."}, "WaitHereCommandPayload"),
        ("come_here", {"arrival_distance": 3.0}, "ComeHereCommandPayload"),
        ("look_at_owner", {"duration_seconds": 2.0}, "LookAtOwnerCommandPayload"),
        ("report_status", {"detail_level": "standard"}, "ReportStatusCommandPayload"),
        ("stop", {"reason": "Owner request."}, "StopCommandPayload"),
        (
            "emergency_stop",
            {"reason": "Immediate operator stop."},
            "EmergencyStopCommandPayload",
        ),
    ],
)
def test_every_allowed_command_has_its_own_bounded_payload(
    name: str,
    arguments: dict[str, object],
    payload_class: str,
) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"] = {
        "name": name,
        "deadline_at": "2026-07-10T18:06:00Z",
        "arguments": arguments,
    }

    parsed = _validate(data)

    assert type(parsed.payload).__name__ == payload_class


def test_unknown_protocol_version_is_rejected() -> None:
    data = _fixture("heartbeat.json")
    data["version"] = 2
    _assert_invalid(data)


@pytest.mark.parametrize(("field", "value"), [("version", 1.0), ("sequence", "1")])
def test_envelope_integer_fields_are_not_coerced(field: str, value: object) -> None:
    data = _fixture("heartbeat.json")
    data[field] = value
    _assert_invalid(data)


def test_unknown_message_type_is_rejected() -> None:
    data = _fixture("heartbeat.json")
    data["type"] = "execute_javascript"
    _assert_invalid(data)


def test_unknown_command_name_is_rejected() -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"]["name"] = "mine_diamonds"
    _assert_invalid(data)


def test_invalid_terminal_outcome_is_rejected() -> None:
    data = _fixture("terminal-result.json")
    data["payload"]["outcome"] = "partially_completed"
    _assert_invalid(data)


def test_missing_message_id_is_rejected() -> None:
    data = _fixture("hello.json")
    del data["message_id"]
    _assert_invalid(data)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "welcome.json",
        "heartbeat.json",
        "sidecar-status.json",
        "minecraft-status.json",
        "command-follow-owner.json",
        "command-ack.json",
        "command-progress.json",
        "state-snapshot.json",
        "terminal-result.json",
        "cancel-command.json",
        "emergency-stop.json",
        "shutdown.json",
    ],
)
def test_connection_id_is_required_after_handshake(fixture_name: str) -> None:
    data = _fixture(fixture_name)
    del data["connection_id"]
    _assert_invalid(data)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "command-follow-owner.json",
        "command-ack.json",
        "command-progress.json",
        "terminal-result.json",
        "cancel-command.json",
        "emergency-stop.json",
    ],
)
def test_command_id_is_required_for_command_messages(fixture_name: str) -> None:
    data = _fixture(fixture_name)
    del data["command_id"]
    _assert_invalid(data)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "command-follow-owner.json",
        "command-ack.json",
        "command-progress.json",
        "terminal-result.json",
        "cancel-command.json",
        "emergency-stop.json",
        "shutdown.json",
    ],
)
def test_trace_id_is_required_where_correlated(fixture_name: str) -> None:
    data = _fixture(fixture_name)
    del data["trace_id"]
    _assert_invalid(data)


@pytest.mark.parametrize(
    "sent_at",
    [
        "2026-07-10T18:00:00",
        "not-a-timestamp",
        "2026-07-10T12:00:00-06:00",
    ],
)
def test_sent_at_requires_well_formed_utc_timestamp(sent_at: str) -> None:
    data = _fixture("heartbeat.json")
    data["sent_at"] = sent_at
    _assert_invalid(data)


@pytest.mark.parametrize(
    "deadline_at",
    [
        "not-a-timestamp",
        "2026-07-10T18:01:00",
        "2026-07-10T12:06:00-06:00",
        "2026-07-10T18:00:59Z",
        "2026-07-10T18:01:00Z",
        "2026-07-10T18:16:01Z",
    ],
)
def test_deadline_must_be_valid_utc_and_later_than_send_time(deadline_at: str) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"]["deadline_at"] = deadline_at
    _assert_invalid(data)


def test_command_deadline_is_required() -> None:
    data = _fixture("command-follow-owner.json")
    del data["payload"]["deadline_at"]
    _assert_invalid(data)


def test_negative_sequence_is_rejected() -> None:
    data = _fixture("heartbeat.json")
    data["sequence"] = -1
    _assert_invalid(data)


@pytest.mark.parametrize(
    "dangerous_argument",
    [
        {"javascript": "bot.chat('op me')"},
        {"method_name": "setControlState"},
        {"server_command": "/op Shana"},
        {"pathfinder_goal": {"kind": "GoalBlock"}},
        {"x": 10, "y": 64, "z": 10},
    ],
)
def test_dangerous_or_coordinate_command_arguments_are_rejected(
    dangerous_argument: dict[str, object],
) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"]["arguments"].update(dangerous_argument)
    _assert_invalid(data)


@pytest.mark.parametrize("field", ["reason", "safe_detail"])
def test_oversized_safe_text_is_rejected(field: str) -> None:
    if field == "reason":
        data = _fixture("cancel-command.json")
        data["payload"][field] = "x" * 161
    else:
        data = _fixture("protocol-error.json")
        data["payload"][field] = "x" * 257
    _assert_invalid(data)


@pytest.mark.parametrize("distance", [1.99, 6.01])
def test_invalid_follow_distance_is_rejected(distance: float) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"]["arguments"]["follow_distance"] = distance
    _assert_invalid(data)


@pytest.mark.parametrize("duration", [4, 901])
def test_invalid_follow_lease_duration_is_rejected(duration: int) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"]["arguments"]["lease_duration_seconds"] = duration
    _assert_invalid(data)


@pytest.mark.parametrize("distance", [1.99, 4.01])
def test_invalid_arrival_distance_is_rejected(distance: float) -> None:
    data = _fixture("command-follow-owner.json")
    data["payload"] = {
        "name": "come_here",
        "deadline_at": "2026-07-10T18:06:00Z",
        "arguments": {"arrival_distance": distance},
    }
    _assert_invalid(data)


@pytest.mark.parametrize(("field", "value"), [("health", -0.1), ("health", 20.1), ("hunger", -1), ("hunger", 21)])
def test_invalid_snapshot_health_or_hunger_is_rejected(field: str, value: float) -> None:
    data = _fixture("state-snapshot.json")
    data["payload"][field] = value
    _assert_invalid(data)


def test_state_snapshot_requires_paired_active_command_fields() -> None:
    data = _fixture("state-snapshot.json")
    del data["payload"]["active_command_name"]
    _assert_invalid(data)


@pytest.mark.parametrize(
    "extra_field",
    ["javascript", "method_name", "coordinates", "raw_chat", "stack_trace", "secret", "item_name"],
)
def test_emergency_stop_accepts_only_a_bounded_reason(extra_field: str) -> None:
    data = _fixture("emergency-stop.json")
    data["payload"][extra_field] = "unsafe"
    _assert_invalid(data)


def test_emergency_stop_rejects_oversized_reason() -> None:
    data = _fixture("emergency-stop.json")
    data["payload"]["reason"] = "x" * 161
    _assert_invalid(data)


@pytest.mark.parametrize(
    "unsafe_field",
    ["exception", "stack_trace", "secret", "raw_server_text", "raw_chat", "book", "sign", "item_name"],
)
def test_protocol_error_rejects_unbounded_or_unsafe_detail_fields(unsafe_field: str) -> None:
    data = _fixture("protocol-error.json")
    data["payload"][unsafe_field] = "must not cross the protocol boundary"
    _assert_invalid(data)


@pytest.mark.parametrize("location", ["envelope", "payload", "arguments"])
def test_unknown_fields_are_rejected_at_every_command_level(location: str) -> None:
    data = _fixture("command-follow-owner.json")
    if location == "envelope":
        data["unexpected"] = True
    elif location == "payload":
        data["payload"]["unexpected"] = True
    else:
        data["payload"]["arguments"]["unexpected"] = True
    _assert_invalid(data)


@pytest.mark.parametrize("outcome", list(TerminalOutcome))
def test_every_allowed_terminal_outcome_is_representable(outcome: TerminalOutcome) -> None:
    data = _fixture("terminal-result.json")
    data["payload"]["outcome"] = outcome.value
    if outcome in {TerminalOutcome.FAILED, TerminalOutcome.REJECTED, TerminalOutcome.TIMED_OUT}:
        data["payload"]["failure"] = {
            "code": "PATH_NOT_FOUND",
            "safe_detail": "No safe bounded path was found.",
            "retriable": True,
        }
    else:
        data["payload"]["failure"] = None

    parsed = _validate(data)

    assert parsed.payload.outcome == outcome


@pytest.mark.parametrize("code", list(FailureCode))
def test_every_stable_failure_code_is_representable(code: FailureCode) -> None:
    data = _fixture("terminal-result.json")
    data["payload"]["outcome"] = "failed"
    data["payload"]["failure"] = {
        "code": code.value,
        "safe_detail": "Bounded operator-safe detail.",
        "retriable": False,
    }

    parsed = _validate(data)

    assert parsed.payload.failure.code == code
