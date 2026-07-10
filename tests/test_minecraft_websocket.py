from __future__ import annotations

import asyncio
import ast
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers

from gamma.integrations.minecraft.coordinator import MinecraftCoordinator
from gamma.integrations.minecraft.protocol import (
    CompanionState,
    FailureCode,
    HelloMessage,
    HelloPayload,
    MinecraftConnectionState,
    MinecraftStatusMessage,
    MinecraftStatusPayload,
    ProtocolMessage,
    ReportStatusCommandPayload,
    WelcomeMessage,
)
from gamma.integrations.minecraft.websocket import (
    DEFAULT_MAXIMUM_INBOUND_BYTES,
    MAXIMUM_INBOUND_BYTES_LIMIT,
    MINECRAFT_CONTROL_PATH,
    MinecraftWebSocketDeliveryError,
    MinecraftWebSocketTransport,
    _safe_close,
    create_minecraft_control_router,
    is_literal_loopback_peer,
)
from gamma.main import app as production_app


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "minecraft_protocol" / "v1"
TOKEN = "dedicated-test-control-token"
NOW = datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)


class RecordingCoordinator(MinecraftCoordinator):
    def __init__(self, **kwargs: Any) -> None:
        connection_ids = itertools.count(1)
        message_ids = itertools.count(1)
        super().__init__(
            enabled=True,
            connection_id_factory=lambda: f"connection-test-{next(connection_ids)}",
            message_id_factory=lambda: f"message-test-{next(message_ids)}",
            **kwargs,
        )
        self.attach_count = 0
        self.detach_count = 0
        self.received: list[ProtocolMessage] = []
        self.statuses_after_receive = []

    def attach_transport(self, transport) -> None:
        self.attach_count += 1
        super().attach_transport(transport)

    def detach_transport(self, transport=None) -> bool:
        self.detach_count += 1
        return super().detach_transport(transport)

    async def receive(self, message: ProtocolMessage):
        result = await super().receive(message)
        self.received.append(message)
        self.statuses_after_receive.append(self.status())
        return result


class FakeSendWebSocket:
    def __init__(self) -> None:
        self.text_frames: list[str] = []
        self.binary_frames: list[bytes] = []
        self.fail: Exception | None = None
        self.active_sends = 0
        self.maximum_active_sends = 0

    async def send_text(self, data: str) -> None:
        self.active_sends += 1
        self.maximum_active_sends = max(self.maximum_active_sends, self.active_sends)
        await anyio.sleep(0)
        try:
            if self.fail is not None:
                raise self.fail
            self.text_frames.append(data)
        finally:
            self.active_sends -= 1


class FakeRouteWebSocket:
    def __init__(
        self,
        *,
        authorization: str | None,
        host: str = "127.0.0.1",
        events: list[dict[str, Any]] | None = None,
        on_send=None,
        send_error: Exception | None = None,
    ) -> None:
        raw_headers = [] if authorization is None else [(b"authorization", authorization.encode())]
        self.headers = Headers(raw=raw_headers)
        self.client = SimpleClient(host)
        self.events = list(events or [])
        self.accepted = False
        self.closes: list[tuple[int, str]] = []
        self.text_frames: list[str] = []
        self.on_send = on_send
        self.send_error = send_error

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        if self.events:
            return self.events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def close(self, *, code: int, reason: str) -> None:
        self.closes.append((code, reason))

    async def send_text(self, data: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.text_frames.append(data)
        if self.on_send is not None:
            self.on_send()


class SimpleClient:
    def __init__(self, host: str) -> None:
        self.host = host


class QueueRouteWebSocket(FakeRouteWebSocket):
    def __init__(self) -> None:
        super().__init__(authorization=f"Bearer {TOKEN}")
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent_event = asyncio.Event()
        self.closed_event = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        return await self.queue.get()

    async def send_text(self, data: str) -> None:
        await super().send_text(data)
        self.sent_event.set()

    async def close(self, *, code: int, reason: str) -> None:
        await super().close(code=code, reason=reason)
        self.closed_event.set()


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def hello_model() -> HelloMessage:
    return HelloMessage(
        protocol="gamma.minecraft",
        version=1,
        type="hello",
        message_id="hello-direct-1",
        sent_at=NOW,
        sequence=0,
        payload=HelloPayload(
            supported_versions=[1],
            sidecar_instance_id="fake-sidecar",
            sidecar_build="0.1.0",
            capabilities=["companion_v1"],
            node_version="22.22.2",
            minecraft_library_version="4.37.1",
            pathfinder_version="2.4.5",
            companion_state=CompanionState.DISCONNECTED,
        ),
    )


def make_app(
    coordinator: MinecraftCoordinator,
    *,
    token: str = TOKEN,
    peer_validator=lambda _host: True,
    maximum_inbound_bytes: int = DEFAULT_MAXIMUM_INBOUND_BYTES,
) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_minecraft_control_router(
            coordinator=coordinator,
            control_token=token,
            peer_validator=peer_validator,
            maximum_inbound_bytes=maximum_inbound_bytes,
        )
    )
    return app


def run_router_endpoint(router, websocket: FakeRouteWebSocket) -> None:
    endpoint = router.routes[0].endpoint
    anyio.run(endpoint, websocket)


def text_event(payload: str | dict[str, Any]) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"type": "websocket.receive", "text": text}


def binary_event(payload: bytes) -> dict[str, Any]:
    return {"type": "websocket.receive", "bytes": payload}


def disconnect_event(code: int = 1000) -> dict[str, Any]:
    return {"type": "websocket.disconnect", "code": code}


def test_transport_serializes_canonical_enums_datetimes_and_text_frames() -> None:
    async def run() -> None:
        socket = FakeSendWebSocket()
        transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
        await transport.send(hello_model())

        assert socket.binary_frames == []
        assert len(socket.text_frames) == 1
        payload = json.loads(socket.text_frames[0])
        assert payload["payload"]["companion_state"] == "DISCONNECTED"
        assert payload["sent_at"] == "2026-07-10T18:00:00Z"
        assert payload["type"] == "hello"

    anyio.run(run)


def test_transport_serializes_concurrent_sends() -> None:
    async def run() -> None:
        socket = FakeSendWebSocket()
        transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
        async with anyio.create_task_group() as group:
            for _ in range(8):
                group.start_soon(transport.send, hello_model())
        assert len(socket.text_frames) == 8
        assert socket.maximum_active_sends == 1

    anyio.run(run)


def test_transport_rejects_raw_dictionaries_and_has_no_receive_or_credentials() -> None:
    async def run() -> None:
        transport = MinecraftWebSocketTransport(FakeSendWebSocket())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="canonical"):
            await transport.send(fixture("hello.json"))  # type: ignore[arg-type]
        assert not hasattr(transport, "receive")
        assert "token" not in vars(transport)
        assert "peer" not in vars(transport)

    anyio.run(run)


def test_transport_failure_is_safe_and_propagates() -> None:
    async def run() -> None:
        socket = FakeSendWebSocket()
        socket.fail = OSError("private low-level detail")
        transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
        with pytest.raises(MinecraftWebSocketDeliveryError) as caught:
            await transport.send(hello_model())
        assert "private low-level detail" not in str(caught.value)

    anyio.run(run)


@pytest.mark.parametrize("token", ["", " ", None])
def test_router_rejects_empty_control_token(token: str | None) -> None:
    with pytest.raises(ValueError, match="control_token"):
        create_minecraft_control_router(
            coordinator=RecordingCoordinator(),
            control_token=token,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "size",
    [0, -1, True, 1.5, MAXIMUM_INBOUND_BYTES_LIMIT + 1],
)
def test_router_rejects_invalid_message_size(size: object) -> None:
    with pytest.raises(ValueError, match="maximum_inbound_bytes"):
        create_minecraft_control_router(
            coordinator=RecordingCoordinator(),
            control_token=TOKEN,
            maximum_inbound_bytes=size,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("provider_name", ["coordinator", "token", "maximum"])
def test_provider_exceptions_fail_closed_without_acceptance(provider_name: str) -> None:
    coordinator = RecordingCoordinator()

    def fail():
        raise RuntimeError("distinctive-provider-detail")

    providers = {
        "coordinator_provider": fail if provider_name == "coordinator" else lambda: coordinator,
        "control_token_provider": fail if provider_name == "token" else lambda: TOKEN,
        "maximum_inbound_bytes_provider": fail if provider_name == "maximum" else lambda: 65_536,
    }
    router = create_minecraft_control_router(**providers)
    socket = FakeRouteWebSocket(authorization=f"Bearer {TOKEN}")
    run_router_endpoint(router, socket)
    assert socket.accepted is False
    assert socket.closes == [(1008, "connection not authorized")]
    assert coordinator.attach_count == 0


def test_router_factory_starts_nothing_and_production_mounts_once() -> None:
    coordinator = RecordingCoordinator()
    before = coordinator.status()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
    )

    assert [route.path for route in router.routes] == [MINECRAFT_CONTROL_PATH]
    assert MINECRAFT_CONTROL_PATH == "/v1/minecraft/control"
    assert coordinator.status() == before
    assert coordinator.attach_count == 0
    assert [route.path for route in production_app.routes].count(MINECRAFT_CONTROL_PATH) == 1
    temporary_app = make_app(RecordingCoordinator())
    assert any(route.path == MINECRAFT_CONTROL_PATH for route in temporary_app.routes)


@pytest.mark.parametrize(
    "header",
    [None, "Basic abc", "Bearer", "Bearer ", "Bearer wrong", f"Bearer {TOKEN} extra"],
)
def test_authentication_rejection_is_uniform_and_does_not_attach(header: str | None) -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    websocket = FakeRouteWebSocket(authorization=header)

    run_router_endpoint(router, websocket)

    assert websocket.accepted is False
    assert websocket.closes == [(1008, "connection not authorized")]
    reason = websocket.closes[0][1]
    assert reason == "connection not authorized"
    assert TOKEN not in reason
    assert coordinator.attach_count == 0
    assert coordinator.status().transport_attached is False
    assert TOKEN not in repr(coordinator.status())


def test_valid_token_from_unauthorized_peer_is_rejected_without_attachment() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: False,
    )
    websocket = FakeRouteWebSocket(authorization=f"Bearer {TOKEN}")

    run_router_endpoint(router, websocket)

    reason = websocket.closes[0][1]
    assert reason == "connection not authorized"
    assert coordinator.attach_count == 0


def test_multiple_authorization_headers_cannot_bypass_authentication() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    websocket = FakeRouteWebSocket(authorization=None)
    websocket.headers = Headers(
        raw=[
            (b"authorization", f"Bearer {TOKEN}".encode()),
            (b"authorization", b"Bearer wrong"),
        ]
    )

    run_router_endpoint(router, websocket)

    assert websocket.closes == [(1008, "connection not authorized")]
    assert coordinator.attach_count == 0


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", True),
        ("127.255.255.254", True),
        ("::1", True),
        ("0:0:0:0:0:0:0:1", True),
        ("::ffff:127.0.0.1", True),
        ("localhost", False),
        ("testclient", False),
        ("192.168.1.2", False),
        ("169.254.1.1", False),
        ("0.0.0.0", False),
        ("127.0.0.1:8000", False),
        ("not-an-ip", False),
        ("", False),
        (None, False),
    ],
)
def test_default_peer_policy_accepts_only_literal_loopback(host: str | None, allowed: bool) -> None:
    assert is_literal_loopback_peer(host) is allowed


def test_valid_handshake_sends_one_welcome_and_disconnect_detaches() -> None:
    coordinator = RecordingCoordinator()
    live_status = []
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(fixture("hello.json")), disconnect_event()],
        on_send=lambda: live_status.append(coordinator.status()),
    )

    run_router_endpoint(router, socket)

    welcome = json.loads(socket.text_frames[0])
    assert welcome["type"] == "welcome"
    assert welcome["connection_id"] == "connection-test-1"
    assert live_status[0].transport_attached is True
    assert live_status[0].handshake_complete is True
    assert live_status[0].minecraft_connection_state == MinecraftConnectionState.DISCONNECTED
    assert len([message for message in coordinator.received if message.type == "hello"]) == 1

    assert coordinator.status().transport_attached is False
    assert coordinator.status().handshake_complete is False
    assert coordinator.detach_count == 1


def test_production_peer_policy_accepts_authenticated_literal_loopback() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        host="127.0.0.1",
        events=[text_event(fixture("hello.json")), disconnect_event()],
    )

    run_router_endpoint(router, socket)

    assert socket.accepted is True
    assert json.loads(socket.text_frames[0])["type"] == "welcome"


def test_canonical_status_and_snapshot_messages_reach_coordinator() -> None:
    coordinator = RecordingCoordinator()
    connection_id = "connection-test-1"
    messages = [fixture("hello.json")]
    for name in (
        "heartbeat.json",
        "sidecar-status.json",
        "minecraft-status.json",
        "state-snapshot.json",
    ):
        payload = fixture(name)
        payload["connection_id"] = connection_id
        messages.append(payload)
    duplicate = fixture("heartbeat.json")
    duplicate["connection_id"] = connection_id
    duplicate["sequence"] = 99
    messages.append(duplicate)
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[*(text_event(message) for message in messages), disconnect_event()],
        on_send=lambda: None,
    )

    run_router_endpoint(router, socket)
    assert [message.type for message in coordinator.received] == [
        "hello",
        "heartbeat",
        "sidecar_status",
        "minecraft_status",
        "state_snapshot",
        "heartbeat",
    ]
    snapshot_status = coordinator.statuses_after_receive[4]
    assert snapshot_status.minecraft_connection_state == MinecraftConnectionState.CONNECTED
    assert snapshot_status.owner_present is True


@pytest.mark.parametrize(
    ("payload", "binary", "expected_code"),
    [
        ("not json", False, 1008),
        ("[]", False, 1008),
        ("42", False, 1008),
        (json.dumps({"type": "unknown"}), False, 1008),
        (json.dumps({**fixture("hello.json"), "version": 2}), False, 1008),
        (json.dumps({key: value for key, value in fixture("hello.json").items() if key != "message_id"}), False, 1008),
        (json.dumps({**fixture("hello.json"), "unexpected": True}), False, 1008),
        (json.dumps({**fixture("hello.json"), "sent_at": "2026-07-10T18:00:00"}), False, 1008),
        (b"binary-json", True, 1003),
    ],
)
def test_invalid_frames_close_safely_without_coordinator_mutation(
    payload: str | bytes,
    binary: bool,
    expected_code: int,
) -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    event = (
        binary_event(payload if isinstance(payload, bytes) else payload.encode())
        if binary
        else text_event(payload if isinstance(payload, str) else payload.decode())
    )
    socket = FakeRouteWebSocket(authorization=f"Bearer {TOKEN}", events=[event])

    run_router_endpoint(router, socket)

    assert socket.closes[0][0] == expected_code
    assert len(socket.closes[0][1]) <= 64
    assert str(payload) not in socket.closes[0][1]
    assert coordinator.received == []


def test_oversized_utf8_is_rejected_before_parsing() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
        maximum_inbound_bytes=8,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event("snowman-☃")],  # 11 UTF-8 bytes, 9 Python characters.
    )

    run_router_endpoint(router, socket)

    assert socket.closes == [(1009, "message too large")]
    assert coordinator.received == []


def test_message_size_exact_limit_is_accepted_and_one_byte_over_is_rejected() -> None:
    serialized = json.dumps(fixture("hello.json"), separators=(",", ":"))
    maximum = len(serialized.encode("utf-8"))

    accepted_coordinator = RecordingCoordinator()
    accepted_router = create_minecraft_control_router(
        coordinator=accepted_coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
        maximum_inbound_bytes=maximum,
    )
    accepted_socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(serialized), disconnect_event()],
    )
    run_router_endpoint(accepted_router, accepted_socket)
    assert [message.type for message in accepted_coordinator.received] == ["hello"]

    rejected_coordinator = RecordingCoordinator()
    rejected_router = create_minecraft_control_router(
        coordinator=rejected_coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
        maximum_inbound_bytes=maximum,
    )
    rejected_socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(serialized + " ")],
    )
    run_router_endpoint(rejected_router, rejected_socket)
    assert rejected_socket.closes == [(1009, "message too large")]
    assert rejected_coordinator.received == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"protocol":"gamma.minecraft","version":1,"type":"hello","type":"hello"}',
        '{"protocol":"gamma.minecraft","version":1,"version":1,"type":"hello"}',
        '{"protocol":"gamma.minecraft","version":1,"type":"heartbeat",'
        '"connection_id":"first","connection_id":"second"}',
        '{"protocol":"gamma.minecraft","version":1,"type":"command",'
        '"payload":{"name":"follow_owner","arguments":'
        '{"follow_distance":3,"follow_distance":4}}}',
    ],
)
def test_duplicate_json_keys_at_any_object_depth_are_rejected(payload: str) -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(payload)],
    )

    run_router_endpoint(router, socket)

    assert socket.closes == [(1008, "invalid protocol message")]
    assert coordinator.received == []


def test_invalid_json_after_handshake_sends_no_fabricated_protocol_error() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(fixture("hello.json")), text_event("private malformed payload")],
    )

    run_router_endpoint(router, socket)

    assert [json.loads(frame)["type"] for frame in socket.text_frames] == ["welcome"]
    assert socket.closes == [(1008, "invalid protocol message")]
    assert "private malformed payload" not in socket.closes[0][1]


def test_unexpected_asgi_event_closes_with_stable_policy_code() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[{"type": "http.request", "body": b"private"}],
    )

    run_router_endpoint(router, socket)

    assert socket.closes == [(1008, "invalid websocket event")]
    assert coordinator.received == []


@pytest.mark.parametrize("disconnect_code", [1000, 1006])
def test_normal_and_abrupt_disconnects_detach_exact_transport(disconnect_code: int) -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[disconnect_event(disconnect_code)],
    )

    run_router_endpoint(router, socket)

    assert coordinator.attach_count == 1
    assert coordinator.detach_count == 1
    assert coordinator.status().transport_attached is False


def test_disconnect_terminalizes_active_work_without_clearing_safety_state() -> None:
    async def run() -> None:
        coordinator = RecordingCoordinator(clock=lambda: NOW)
        router = create_minecraft_control_router(
            coordinator=coordinator,
            control_token=TOKEN,
            peer_validator=lambda _host: True,
        )
        endpoint = router.routes[0].endpoint
        socket = QueueRouteWebSocket()
        task = asyncio.create_task(endpoint(socket))
        await socket.queue.put(text_event(fixture("hello.json")))
        await socket.sent_event.wait()

        submitted = await coordinator.submit_command(
            ReportStatusCommandPayload(
                name="report_status",
                deadline_at=NOW + timedelta(seconds=30),
            )
        )
        assert submitted.active is not None
        await coordinator.activate_emergency_stop(reason="Keep local latch.")
        await socket.queue.put(disconnect_event())
        await task

        status = coordinator.status()
        assert status.active_command is None
        assert status.last_terminal is not None
        assert status.last_terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED
        assert status.emergency_stop_active is True

    anyio.run(run)


def test_welcome_delivery_failure_never_leaves_completed_handshake() -> None:
    coordinator = RecordingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(fixture("hello.json"))],
        send_error=OSError("private sidecar failure"),
    )

    run_router_endpoint(router, socket)

    assert socket.closes == [(1008, "protocol message rejected")]
    assert "private sidecar failure" not in socket.closes[0][1]
    status = coordinator.status()
    assert status.handshake_complete is False
    assert status.transport_attached is False
    assert coordinator.detach_count == 1


def test_command_delivery_failure_leaves_safe_terminal_coordinator_state() -> None:
    async def run() -> None:
        socket = FakeSendWebSocket()
        transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
        coordinator = RecordingCoordinator(clock=lambda: NOW)
        coordinator.attach_transport(transport)
        await coordinator.receive(hello_model())
        socket.fail = OSError("private command transport failure")

        result = await coordinator.submit_command(
            ReportStatusCommandPayload(
                name="report_status",
                deadline_at=NOW + timedelta(seconds=30),
            )
        )

        assert result.failure_code is not None
        assert result.terminal is not None
        assert coordinator.status().active_command is None
        assert coordinator.status().transport_attached is False
        assert "private command transport failure" not in repr(coordinator.status())

    anyio.run(run)


def test_emergency_stop_send_failure_keeps_local_latch() -> None:
    async def run() -> None:
        socket = FakeSendWebSocket()
        transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
        coordinator = RecordingCoordinator()
        coordinator.attach_transport(transport)
        await coordinator.receive(hello_model())
        socket.fail = OSError("private emergency transport failure")

        result = await coordinator.activate_emergency_stop(reason="Immediate local stop.")

        assert result.delivered is False
        assert coordinator.status().emergency_stop_active is True
        assert coordinator.status().transport_attached is False

    anyio.run(run)


def test_old_session_identifier_is_closed_without_mutating_current_status() -> None:
    coordinator = RecordingCoordinator()
    old = fixture("minecraft-status.json")
    old["connection_id"] = "old-connection"
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(fixture("hello.json")), text_event(old)],
    )

    run_router_endpoint(router, socket)

    assert socket.closes[-1][0] == 1008
    assert coordinator.status().minecraft_connection_state == MinecraftConnectionState.DISCONNECTED


def test_replacement_socket_is_authoritative_and_old_cleanup_cannot_detach_it() -> None:
    async def run() -> None:
        coordinator = RecordingCoordinator()
        router = create_minecraft_control_router(
            coordinator=coordinator,
            control_token=TOKEN,
            peer_validator=lambda _host: True,
        )
        endpoint = router.routes[0].endpoint
        socket_a = QueueRouteWebSocket()
        socket_b = QueueRouteWebSocket()
        task_a = asyncio.create_task(endpoint(socket_a))
        await socket_a.queue.put(text_event(fixture("hello.json")))
        await socket_a.sent_event.wait()
        first_connection = json.loads(socket_a.text_frames[0])["connection_id"]
        submitted = await coordinator.submit_command(
            ReportStatusCommandPayload(
                name="report_status",
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
        )
        assert submitted.active is not None
        assert [json.loads(frame)["type"] for frame in socket_a.text_frames] == [
            "welcome",
            "command",
        ]

        task_b = asyncio.create_task(endpoint(socket_b))
        await anyio.sleep(0)
        assert coordinator.status().handshake_complete is False
        await socket_b.queue.put(text_event(fixture("hello.json")))
        await socket_b.sent_event.wait()
        second_connection = json.loads(socket_b.text_frames[0])["connection_id"]
        assert second_connection != first_connection
        assert coordinator.status().last_terminal is not None
        assert coordinator.status().last_terminal.failure_code == FailureCode.SIDECAR_DISCONNECTED

        stale = fixture("minecraft-status.json")
        stale["connection_id"] = first_connection
        await socket_a.queue.put(text_event(stale))
        await socket_a.closed_event.wait()
        await task_a
        assert socket_a.closes == [(1008, "connection replaced")]
        assert coordinator.status().transport_attached is True
        assert coordinator.status().handshake_complete is True
        assert coordinator.status().minecraft_connection_state == MinecraftConnectionState.DISCONNECTED

        current = fixture("minecraft-status.json")
        current["connection_id"] = second_connection
        await socket_b.queue.put(text_event(current))
        with anyio.fail_after(2):
            while coordinator.status().minecraft_connection_state != MinecraftConnectionState.CONNECTED:
                await anyio.sleep(0)
        assert coordinator.status().transport_attached is True
        assert [json.loads(frame)["type"] for frame in socket_b.text_frames] == ["welcome"]
        assert [json.loads(frame)["type"] for frame in socket_a.text_frames].count("command") == 1

        await socket_b.queue.put(disconnect_event())
        await task_b
        assert coordinator.status().transport_attached is False

    anyio.run(run)


def test_emergency_latch_survives_control_socket_disconnect() -> None:
    async def latch() -> RecordingCoordinator:
        coordinator = RecordingCoordinator()
        await coordinator.activate_emergency_stop(reason="Local safety latch.")
        return coordinator

    coordinator = anyio.run(latch)
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[disconnect_event()],
    )

    run_router_endpoint(router, socket)

    assert coordinator.status().emergency_stop_active is True


def test_unexpected_coordinator_failure_closes_1011_and_cleans_up() -> None:
    class FailingCoordinator(RecordingCoordinator):
        async def receive(self, message: ProtocolMessage):
            raise RuntimeError("private stack detail")

    coordinator = FailingCoordinator()
    router = create_minecraft_control_router(
        coordinator=coordinator,
        control_token=TOKEN,
        peer_validator=lambda _host: True,
    )
    socket = FakeRouteWebSocket(
        authorization=f"Bearer {TOKEN}",
        events=[text_event(fixture("hello.json"))],
    )

    run_router_endpoint(router, socket)

    assert socket.closes == [(1011, "internal control failure")]
    assert "private stack detail" not in socket.closes[0][1]
    assert coordinator.status().transport_attached is False


def test_close_helper_does_not_swallow_process_cancellation() -> None:
    class CancellingSocket:
        async def close(self, **_kwargs) -> None:
            raise asyncio.CancelledError

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await _safe_close(CancellingSocket(), code=1011, reason="safe")  # type: ignore[arg-type]

    anyio.run(run)


def test_route_does_not_swallow_coordinator_cancellation_and_still_detaches() -> None:
    class CancellingCoordinator(RecordingCoordinator):
        async def receive(self, message: ProtocolMessage):
            raise asyncio.CancelledError

    async def run() -> None:
        coordinator = CancellingCoordinator()
        router = create_minecraft_control_router(
            coordinator=coordinator,
            control_token=TOKEN,
            peer_validator=lambda _host: True,
        )
        socket = FakeRouteWebSocket(
            authorization=f"Bearer {TOKEN}",
            events=[text_event(fixture("hello.json"))],
        )
        endpoint = router.routes[0].endpoint

        with pytest.raises(asyncio.CancelledError):
            await endpoint(socket)
        assert coordinator.status().transport_attached is False

    anyio.run(run)


def test_boundary_imports_and_production_registration_remain_isolated() -> None:
    websocket_path = ROOT / "src" / "gamma" / "integrations" / "minecraft" / "websocket.py"
    dashboard_root = ROOT / "src" / "gamma" / "dashboard"
    tree = ast.parse(websocket_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(module.startswith("gamma.dashboard") for module in imported)
    for source in dashboard_root.rglob("*.py"):
        assert "gamma.integrations.minecraft" not in source.read_text(encoding="utf-8")
    assert "create_minecraft_control_router" not in (ROOT / "src" / "gamma" / "main.py").read_text(encoding="utf-8")
    assert [route.path for route in production_app.routes].count(MINECRAFT_CONTROL_PATH) == 1


def test_canonical_welcome_model_remains_json_compatible() -> None:
    socket = FakeSendWebSocket()
    transport = MinecraftWebSocketTransport(socket)  # type: ignore[arg-type]
    welcome = WelcomeMessage.model_validate(fixture("welcome.json"))

    anyio.run(transport.send, welcome)

    assert json.loads(socket.text_frames[0]) == welcome.model_dump(mode="json")
