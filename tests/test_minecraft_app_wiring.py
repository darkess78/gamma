from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest
from starlette.datastructures import Headers

from gamma import main as shana_main
from gamma.api import routes as shana_routes
from gamma.config import MinecraftSettings, SecretValue, Settings
from gamma.integrations.minecraft.protocol import MinecraftConnectionState
from gamma.integrations.minecraft.websocket import MINECRAFT_CONTROL_PATH


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "production-wiring-test-token"


@pytest.fixture(autouse=True)
def reset_coordinator() -> None:
    shana_routes._minecraft_lifespan_active = False
    shana_routes.minecraft_coordinator.set(None)
    yield
    current = shana_routes.minecraft_coordinator._value
    if current is not None:
        current.detach_transport()
        current.reset()
    shana_routes.minecraft_coordinator.set(None)
    shana_routes._minecraft_lifespan_active = False


def minecraft_settings(*, enabled: bool, token: str | None = None, size: int = 65_536) -> MinecraftSettings:
    return MinecraftSettings(
        enabled=enabled,
        maximum_inbound_bytes=size,
        control_token=SecretValue(token),
    )


def test_configuration_defaults_and_safe_representations() -> None:
    configured = Settings().minecraft
    assert configured.enabled is False
    assert configured.maximum_inbound_bytes == 65_536
    assert configured.control_token.get_secret_value() is None

    secret = "never-display-this-control-token"
    value = minecraft_settings(enabled=True, token=secret)
    assert value.configured is True
    assert secret not in repr(value)
    assert secret not in repr(dataclasses.asdict(value))
    assert secret not in json.dumps(value.safe_dict())
    assert "control_token" not in value.safe_dict()


@pytest.mark.parametrize("value", [0, -1, 1_048_577, "not-an-integer"])
def test_configuration_rejects_invalid_maximum_inbound_bytes(monkeypatch, value: object) -> None:
    monkeypatch.setenv("SHANA_MINECRAFT_MAXIMUM_INBOUND_BYTES", str(value))
    with pytest.raises(ValueError, match="maximum_inbound_bytes"):
        Settings()


def test_configuration_rejects_boolean_maximum_inbound_bytes(monkeypatch) -> None:
    from gamma import config

    monkeypatch.setitem(config.APP_CONFIG, "minecraft", {"maximum_inbound_bytes": True})
    monkeypatch.delenv("SHANA_MINECRAFT_MAXIMUM_INBOUND_BYTES", raising=False)
    with pytest.raises(ValueError, match="maximum_inbound_bytes"):
        Settings()


def test_configuration_accepts_valid_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SHANA_MINECRAFT_ENABLED", "true")
    monkeypatch.setenv("SHANA_MINECRAFT_MAXIMUM_INBOUND_BYTES", "1048576")
    monkeypatch.setenv("SHANA_MINECRAFT_CONTROL_TOKEN", TOKEN)
    configured = Settings().minecraft
    assert configured.enabled is True
    assert configured.maximum_inbound_bytes == 1_048_576
    assert configured.control_token.get_secret_value() == TOKEN


@pytest.mark.parametrize("token", [None, "", "   \t"])
def test_blank_control_token_is_unconfigured(token: str | None) -> None:
    configured = minecraft_settings(enabled=True, token=token)
    assert configured.configured is False
    assert configured.control_token.get_secret_value() is None


def _scheduler() -> Mock:
    scheduler = Mock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()
    return scheduler


class _Peer:
    def __init__(self, host: str) -> None:
        self.host = host


class _RejectSocket:
    def __init__(
        self,
        authorization: str | None,
        events: list[dict] | None = None,
        *,
        host: str = "127.0.0.1",
        on_send=None,
    ) -> None:
        raw = [] if authorization is None else [(b"authorization", authorization.encode())]
        self.headers = Headers(raw=raw)
        self.client = _Peer(host)
        self.accepted = False
        self.close_code: int | None = None
        self.events = list(events or [])
        self.sent: list[str] = []
        self.on_send = on_send

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, *, code: int, reason: str) -> None:
        self.close_code = code

    async def receive(self) -> dict:
        if self.events:
            return self.events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, text: str) -> None:
        self.sent.append(text)
        if self.on_send is not None:
            self.on_send()


async def _run_production_socket(
    authorization: str | None,
    events: list[dict] | None = None,
    *,
    host: str = "127.0.0.1",
    on_send=None,
) -> _RejectSocket:
    route = next(route for route in shana_main.app.routes if route.path == MINECRAFT_CONTROL_PATH)
    socket = _RejectSocket(authorization, events, host=host, on_send=on_send)
    await route.endpoint(socket)
    return socket


def test_production_route_is_shana_owned_and_registered_once() -> None:
    paths = [route.path for route in shana_main.app.routes]
    assert paths.count(MINECRAFT_CONTROL_PATH) == 1
    assert "/api/minecraft" not in paths
    assert not any(path.startswith("/api/minecraft") for path in paths)

    dashboard_root = ROOT / "src" / "gamma" / "dashboard"
    for source in dashboard_root.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "MinecraftCoordinator" not in text
        assert "integrations.minecraft" not in text
    example = (ROOT / "config/app.example.toml").read_text(encoding="utf-8")
    assert "SHANA_MINECRAFT_CONTROL_TOKEN" not in example
    assert "control_token" not in example


@pytest.mark.parametrize("configured", [minecraft_settings(enabled=False), minecraft_settings(enabled=True)])
def test_disabled_or_missing_token_starts_but_rejects_before_attachment(configured: MinecraftSettings) -> None:
    scheduler = _scheduler()
    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", configured
        ):
            async with shana_main.lifespan(shana_main.app):
                coordinator = shana_routes.get_minecraft_coordinator()
                assert coordinator.enabled is False
                socket = await _run_production_socket(f"Bearer {TOKEN}")
                assert socket.accepted is False
                assert socket.close_code == 1008
                status = coordinator.status()
                assert status.transport_attached is False
                assert status.handshake_complete is False
    anyio.run(run)
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()


def test_enabled_configuration_handshakes_on_authoritative_coordinator_and_detaches() -> None:
    scheduler = _scheduler()
    hello = json.loads((ROOT / "tests/fixtures/minecraft_protocol/v1/hello.json").read_text(encoding="utf-8"))
    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", minecraft_settings(enabled=True, token=TOKEN)
        ):
            async with shana_main.lifespan(shana_main.app):
                authoritative = shana_routes.get_minecraft_coordinator()
                assert authoritative.enabled is True
                handshake_status = []
                socket = await _run_production_socket(
                    f"Bearer {TOKEN}",
                    [{"type": "websocket.receive", "text": json.dumps(hello)}],
                    on_send=lambda: handshake_status.append(authoritative.status()),
                )
                assert shana_routes.get_minecraft_coordinator() is authoritative
                welcome = json.loads(socket.sent[0])
                assert welcome["type"] == "welcome"
                assert handshake_status[0].handshake_complete is True
                status = authoritative.status()
                assert socket.accepted is True
                assert status.minecraft_connection_state == MinecraftConnectionState.DISCONNECTED
                assert authoritative.status().transport_attached is False
            assert shana_routes.minecraft_coordinator._value is None
    anyio.run(run)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}],
)
def test_enabled_configuration_rejects_missing_or_wrong_token(headers: dict[str, str]) -> None:
    scheduler = _scheduler()
    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", minecraft_settings(enabled=True, token=TOKEN)
        ):
            async with shana_main.lifespan(shana_main.app):
                coordinator = shana_routes.get_minecraft_coordinator()
                socket = await _run_production_socket(headers.get("Authorization"))
                assert socket.accepted is False
                assert socket.close_code == 1008
                assert coordinator.status().transport_attached is False
    anyio.run(run)


def test_enabled_configuration_rejects_non_loopback_peer() -> None:
    scheduler = _scheduler()

    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", minecraft_settings(enabled=True, token=TOKEN)
        ):
            async with shana_main.lifespan(shana_main.app):
                socket = await _run_production_socket(
                    f"Bearer {TOKEN}", host="192.0.2.10"
                )
                assert socket.accepted is False
                assert socket.close_code == 1008
                assert shana_routes.get_minecraft_coordinator().status().transport_attached is False

    anyio.run(run)


def test_fresh_lifespan_gets_fresh_coordinator() -> None:
    scheduler = _scheduler()
    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", minecraft_settings(enabled=True, token=TOKEN)
        ):
            async with shana_main.lifespan(shana_main.app):
                first = shana_routes.get_minecraft_coordinator()
            async with shana_main.lifespan(shana_main.app):
                second = shana_routes.get_minecraft_coordinator()
                assert second is not first
                assert second.status().transport_attached is False
                assert second.status().active_command is None
    anyio.run(run)


def test_overlapping_lifespans_cannot_share_or_reset_a_coordinator() -> None:
    scheduler = _scheduler()

    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler), patch.object(
            shana_main.settings, "minecraft", minecraft_settings(enabled=True, token=TOKEN)
        ):
            async with shana_main.lifespan(shana_main.app):
                authoritative = shana_routes.get_minecraft_coordinator()
                with pytest.raises(RuntimeError, match="already active"):
                    async with shana_main.lifespan(shana_main.app):
                        pass
                assert shana_routes.get_minecraft_coordinator() is authoritative
                assert authoritative.enabled is True

    anyio.run(run)


def test_shutdown_detaches_resets_and_discards_authoritative_coordinator() -> None:
    scheduler = _scheduler()
    coordinator = Mock()
    coordinator.enabled = True
    shana_routes.minecraft_coordinator.set(coordinator)

    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler):
            async with shana_main.lifespan(shana_main.app):
                assert shana_routes.get_minecraft_coordinator() is coordinator

    anyio.run(run)
    coordinator.detach_transport.assert_called_once_with()
    coordinator.reset.assert_called_once_with()
    assert shana_routes.minecraft_coordinator._value is None


def test_scheduler_start_failure_still_discards_authoritative_coordinator() -> None:
    scheduler = _scheduler()
    scheduler.start.side_effect = RuntimeError("scheduler start failed")

    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler):
            with pytest.raises(RuntimeError, match="scheduler start failed"):
                async with shana_main.lifespan(shana_main.app):
                    pass

    anyio.run(run)
    scheduler.stop.assert_awaited_once_with()
    assert shana_routes.minecraft_coordinator._value is None
    assert shana_routes._minecraft_lifespan_active is False
