"""Authenticated WebSocket transport for the Minecraft control protocol.

The router accepts either fixed reviewed test values or narrow production
providers that resolve Shana-owned state at connection time.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
from collections.abc import Callable
from typing import get_args

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from gamma.integrations.minecraft.coordinator import (
    MinecraftCoordinator,
    ReceiveDisposition,
)
from gamma.integrations.minecraft.protocol import ProtocolMessage, parse_protocol_message


MINECRAFT_CONTROL_PATH = "/v1/minecraft/control"
DEFAULT_MAXIMUM_INBOUND_BYTES = 65_536
MAXIMUM_INBOUND_BYTES_LIMIT = 1_048_576

PeerValidator = Callable[[str | None], bool]
CoordinatorProvider = Callable[[], MinecraftCoordinator | None]
ControlTokenProvider = Callable[[], str | None]
MaximumInboundBytesProvider = Callable[[], int]

_PROTOCOL_MESSAGE_TYPES = get_args(get_args(ProtocolMessage)[0])


class MinecraftWebSocketDeliveryError(RuntimeError):
    """Report a failed text-frame delivery without exposing peer exceptions."""


class MinecraftWebSocketTransport:
    """Serialize canonical protocol models onto one server-side WebSocket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._send_lock = asyncio.Lock()

    async def send(self, message: ProtocolMessage) -> None:
        """Send one canonical Pydantic message as a serialized JSON text frame."""

        if not isinstance(message, _PROTOCOL_MESSAGE_TYPES) or not isinstance(
            message, BaseModel
        ):
            raise TypeError("message must be a canonical Minecraft protocol model")
        serialized = message.model_dump_json()
        try:
            async with self._send_lock:
                await self._websocket.send_text(serialized)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise MinecraftWebSocketDeliveryError(
                "Minecraft WebSocket delivery failed"
            ) from exc


def is_literal_loopback_peer(host: str | None) -> bool:
    """Accept only IP literals Python identifies as loopback addresses."""

    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_minecraft_control_router(
    *,
    coordinator: MinecraftCoordinator | None = None,
    control_token: str | None = None,
    coordinator_provider: CoordinatorProvider | None = None,
    control_token_provider: ControlTokenProvider | None = None,
    maximum_inbound_bytes_provider: MaximumInboundBytesProvider | None = None,
    peer_validator: PeerValidator = is_literal_loopback_peer,
    maximum_inbound_bytes: int = DEFAULT_MAXIMUM_INBOUND_BYTES,
) -> APIRouter:
    """Build, but do not mount, the Shana-owned Minecraft control router."""

    if (coordinator is None) == (coordinator_provider is None):
        raise ValueError("provide exactly one coordinator or coordinator_provider")
    if (control_token is None) == (control_token_provider is None):
        raise ValueError("provide exactly one control_token or control_token_provider")
    if control_token is not None and not _usable_control_token(control_token):
        raise ValueError("control_token must be non-empty")
    if not callable(peer_validator):
        raise TypeError("peer_validator must be callable")
    if type(maximum_inbound_bytes) is not int or not (
        1 <= maximum_inbound_bytes <= MAXIMUM_INBOUND_BYTES_LIMIT
    ):
        raise ValueError(
            "maximum_inbound_bytes must be an integer between 1 and 1048576"
        )

    router = APIRouter()
    active_transport: MinecraftWebSocketTransport | None = None

    @router.websocket(MINECRAFT_CONTROL_PATH)
    async def minecraft_control(websocket: WebSocket) -> None:
        nonlocal active_transport

        try:
            resolved_coordinator = coordinator_provider() if coordinator_provider else coordinator
            resolved_token = control_token_provider() if control_token_provider else control_token
            resolved_maximum = (
                maximum_inbound_bytes_provider()
                if maximum_inbound_bytes_provider
                else maximum_inbound_bytes
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await _safe_close(websocket, code=1008, reason="connection not authorized")
            return
        if (
            resolved_coordinator is None
            or not resolved_coordinator.enabled
            or not _usable_control_token(resolved_token)
            or type(resolved_maximum) is not int
            or not 1 <= resolved_maximum <= MAXIMUM_INBOUND_BYTES_LIMIT
        ):
            await _safe_close(websocket, code=1008, reason="connection not authorized")
            return

        host = websocket.client.host if websocket.client is not None else None
        authorizations = websocket.headers.getlist("authorization")
        authenticated = len(authorizations) == 1 and _valid_bearer(
            authorizations[0], resolved_token
        )
        try:
            authorized_peer = peer_validator(host)
        except Exception:
            authorized_peer = False
        if not (authenticated and authorized_peer):
            await _safe_close(websocket, code=1008, reason="connection not authorized")
            return

        await websocket.accept()
        transport = MinecraftWebSocketTransport(websocket)
        active_transport = transport
        resolved_coordinator.attach_transport(transport)

        try:
            while True:
                event = await websocket.receive()
                if active_transport is not transport:
                    await _safe_close(websocket, code=1008, reason="connection replaced")
                    break
                event_type = event.get("type")
                if event_type == "websocket.disconnect":
                    break
                if event_type != "websocket.receive":
                    await _safe_close(
                        websocket, code=1008, reason="invalid websocket event"
                    )
                    break
                if event.get("bytes") is not None:
                    await _safe_close(websocket, code=1003, reason="text frames required")
                    break
                text = event.get("text")
                if not isinstance(text, str):
                    await _safe_close(
                        websocket, code=1008, reason="invalid websocket frame"
                    )
                    break
                if len(text.encode("utf-8")) > resolved_maximum:
                    await _safe_close(websocket, code=1009, reason="message too large")
                    break
                try:
                    message = parse_protocol_message(_reject_duplicate_keys(text))
                except (ValidationError, ValueError):
                    await _safe_close(
                        websocket, code=1008, reason="invalid protocol message"
                    )
                    break

                result = await resolved_coordinator.receive(message)
                if result.disposition == ReceiveDisposition.REJECTED:
                    await _safe_close(
                        websocket, code=1008, reason="protocol message rejected"
                    )
                    break
                if (
                    result.disposition == ReceiveDisposition.IGNORED
                    and result.failure_code is not None
                ):
                    await _safe_close(websocket, code=1008, reason="stale protocol session")
                    break
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            await _safe_close(websocket, code=1011, reason="internal control failure")
        finally:
            if active_transport is transport:
                active_transport = None
            resolved_coordinator.detach_transport(transport)

    return router


def _valid_bearer(authorization: str, expected_token: str) -> bool:
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value:
        return False
    if value != value.strip() or any(character.isspace() for character in value):
        return False
    return secrets.compare_digest(value, expected_token)


def _usable_control_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not any(
        character.isspace() for character in value
    )


def _reject_duplicate_keys(text: str) -> str:
    """Validate that every JSON object has unique keys before Pydantic parsing."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    json.loads(text, object_pairs_hook=unique_object)
    return text


async def _safe_close(websocket: WebSocket, *, code: int, reason: str) -> None:
    """Best-effort close with stable, bounded details only."""

    try:
        await websocket.close(code=code, reason=reason)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


__all__ = [
    "DEFAULT_MAXIMUM_INBOUND_BYTES",
    "MAXIMUM_INBOUND_BYTES_LIMIT",
    "MINECRAFT_CONTROL_PATH",
    "MinecraftWebSocketDeliveryError",
    "MinecraftWebSocketTransport",
    "create_minecraft_control_router",
    "is_literal_loopback_peer",
]
