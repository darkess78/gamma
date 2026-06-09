from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..config import load_app_file_config
from .bus import PerformerEventBus
from .models import STREAM_PUBLIC_TARGET, PerformerOutputEvent

try:  # pragma: no cover - exercised through integration/configured runtime.
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


@dataclass(slots=True)
class VTubeStudioAdapterConfig:
    enabled: bool = False
    endpoint: str = "ws://127.0.0.1:8001"
    plugin_name: str = "Gamma Shana"
    plugin_developer: str = "Gamma"
    auth_token: str = ""
    expression_hotkeys: dict[str, str] = field(default_factory=dict)
    motion_hotkeys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_app_config(cls, config: dict[str, Any] | None = None) -> "VTubeStudioAdapterConfig":
        config = config if config is not None else load_app_file_config()
        nested = config.get("vtube_studio", {}) if isinstance(config.get("vtube_studio", {}), dict) else {}
        return cls(
            enabled=_as_bool(config.get("vtube_studio_enabled", nested.get("enabled", False))),
            endpoint=str(config.get("vtube_studio_endpoint", nested.get("endpoint", "ws://127.0.0.1:8001")) or "ws://127.0.0.1:8001"),
            plugin_name=str(config.get("vtube_studio_plugin_name", nested.get("plugin_name", "Gamma Shana")) or "Gamma Shana"),
            plugin_developer=str(config.get("vtube_studio_plugin_developer", nested.get("plugin_developer", "Gamma")) or "Gamma"),
            auth_token=str(config.get("vtube_studio_auth_token", nested.get("auth_token", "")) or ""),
            expression_hotkeys=_string_map(config.get("vtube_studio_expression_hotkeys", nested.get("expression_hotkeys", {}))),
            motion_hotkeys=_string_map(config.get("vtube_studio_motion_hotkeys", nested.get("motion_hotkeys", {}))),
        )


@dataclass(slots=True)
class VTubeStudioAction:
    ok: bool
    action_type: str
    detail: str
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    event_type: str | None = None
    event_id: str | None = None


class VTubeStudioClient:
    """Small async VTube Studio websocket client.

    The client is intentionally narrow: it connects, optionally authenticates,
    sends API requests, and records enough state for dashboard/operator status.
    """

    def __init__(self, config: VTubeStudioAdapterConfig | None = None) -> None:
        self.config = config or VTubeStudioAdapterConfig.from_app_config()
        self._socket: Any | None = None
        self._connected = False
        self._authenticated = False
        self._token_requested = False
        self._request_count = 0
        self._last_connected_at: str | None = None
        self._last_request_type: str | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "endpoint": self.config.endpoint,
            "connected": self._connected,
            "authenticated": self._authenticated,
            "token_requested": self._token_requested,
            "request_count": self._request_count,
            "last_connected_at": self._last_connected_at,
            "last_request_type": self._last_request_type,
            "last_error": self._last_error,
            "websocket_available": websockets is not None,
        }

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "error": "VTube Studio adapter disabled"}
        async with self._lock:
            try:
                await self._ensure_connected_locked()
                await self._ensure_authenticated_locked()
                return await self._send_locked(request)
            except Exception as exc:
                self._last_error = str(exc)
                self._connected = False
                self._authenticated = False
                await self._close_locked()
                return {"ok": False, "error": str(exc)}

    async def _ensure_connected_locked(self) -> None:
        if self._socket is not None and self._connected:
            return
        if websockets is None:
            raise RuntimeError("websockets dependency is not available")
        self._socket = await websockets.connect(self.config.endpoint)  # type: ignore[union-attr]
        self._connected = True
        self._last_connected_at = _utc_now()
        self._last_error = None

    async def _ensure_authenticated_locked(self) -> None:
        if self._authenticated:
            return
        if self.config.auth_token:
            response = await self._send_locked(
                {
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": f"auth-{uuid4().hex}",
                    "messageType": "AuthenticationRequest",
                    "data": {
                        "pluginName": self.config.plugin_name,
                        "pluginDeveloper": self.config.plugin_developer,
                        "authenticationToken": self.config.auth_token,
                    },
                }
            )
            data = (response.get("response") or {}).get("data") if isinstance(response.get("response"), dict) else {}
            self._authenticated = bool(response.get("ok")) and bool((data or {}).get("authenticated", True))
            if not self._authenticated:
                reason = (data or {}).get("reason") if isinstance(data, dict) else None
                raise RuntimeError(str(response.get("error") or reason or "VTube Studio authentication failed"))
            return
        response = await self._send_locked(
            {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": f"token-{uuid4().hex}",
                "messageType": "AuthenticationTokenRequest",
                "data": {"pluginName": self.config.plugin_name, "pluginDeveloper": self.config.plugin_developer},
            }
        )
        self._token_requested = True
        token = (((response.get("response") or {}).get("data") or {}).get("authenticationToken") if response.get("ok") else None)
        if token:
            self._last_error = "VTube Studio token received; save it as vtube_studio.auth_token and restart"
            raise RuntimeError(self._last_error)
        raise RuntimeError(str(response.get("error") or "VTube Studio auth token approval required"))

    async def _send_locked(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._socket is None:
            raise RuntimeError("VTube Studio websocket is not connected")
        self._last_request_type = str(request.get("messageType") or "")
        self._request_count += 1
        await self._socket.send(json.dumps(request, ensure_ascii=False))
        raw_response = await self._socket.recv()
        response = json.loads(raw_response)
        message_type = str(response.get("messageType") or "")
        if message_type == "APIError":
            error = response.get("data") if isinstance(response.get("data"), dict) else {}
            detail = error.get("message") or response.get("message") or "VTube Studio API error"
            self._last_error = str(detail)
            return {"ok": False, "error": str(detail), "response": response}
        self._last_error = None
        return {"ok": True, "response": response}

    async def _close_locked(self) -> None:
        socket = self._socket
        self._socket = None
        self._connected = False
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass


class VTubeStudioAdapter:
    """Translates generic performer events into VTube Studio API actions.

    This adapter deliberately stays transport-light for now. It provides stable
    event-to-action mapping and status; a websocket sender can consume the
    returned request payloads later without touching stream/voice code.
    """

    def __init__(self, config: VTubeStudioAdapterConfig | None = None, client: VTubeStudioClient | None = None) -> None:
        self.config = config or VTubeStudioAdapterConfig.from_app_config()
        self.client = client or VTubeStudioClient(self.config)
        self._speaking = False
        self._last_event_type: str | None = None
        self._last_action: dict[str, Any] | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        client_status = self.client.status()
        return {
            "enabled": self.config.enabled,
            "configured": bool(self.config.endpoint),
            "connected": client_status["connected"],
            "authenticated": client_status["authenticated"],
            "endpoint": self.config.endpoint,
            "expression_count": len(self.config.expression_hotkeys),
            "motion_count": len(self.config.motion_hotkeys),
            "speaking": self._speaking,
            "last_event_type": self._last_event_type,
            "last_action": self._last_action,
            "last_error": self._last_error,
            "client": client_status,
        }

    def handle_event(self, event: PerformerOutputEvent) -> VTubeStudioAction:
        self._last_event_type = event.type
        try:
            action = self._action_for_event(event)
        except Exception as exc:
            self._last_error = str(exc)
            action = VTubeStudioAction(ok=False, action_type="error", detail=str(exc), event_type=event.type, event_id=event.event_id)
        self._last_action = {
            "ok": action.ok,
            "action_type": action.action_type,
            "detail": action.detail,
            "request": action.request,
            "response": action.response,
            "event_type": action.event_type,
            "event_id": action.event_id,
        }
        return action

    async def handle_event_async(self, event: PerformerOutputEvent) -> VTubeStudioAction:
        action = self.handle_event(event)
        if action.request is None:
            return action
        response = await self.client.send_request(action.request)
        action.response = response
        action.ok = action.ok and bool(response.get("ok"))
        if not action.ok:
            action.detail = str(response.get("error") or action.detail)
            self._last_error = action.detail
        self._last_action = {
            "ok": action.ok,
            "action_type": action.action_type,
            "detail": action.detail,
            "request": action.request,
            "response": action.response,
            "event_type": action.event_type,
            "event_id": action.event_id,
        }
        return action

    def _action_for_event(self, event: PerformerOutputEvent) -> VTubeStudioAction:
        if not self.config.enabled:
            return VTubeStudioAction(ok=True, action_type="disabled", detail="adapter disabled", event_type=event.type, event_id=event.event_id)
        if event.type == "expression_set":
            expression = str(event.payload.get("expression") or "neutral").strip().lower()
            return self._hotkey_action(event, expression, self.config.expression_hotkeys, "expression")
        if event.type == "motion_trigger":
            motion = str(event.payload.get("motion") or "").strip().lower()
            return self._hotkey_action(event, motion, self.config.motion_hotkeys, "motion")
        if event.type == "speech_started":
            self._speaking = True
            return VTubeStudioAction(ok=True, action_type="speaking_state", detail="speaking started", event_type=event.type, event_id=event.event_id)
        if event.type in {"speech_ended", "output_cleared"}:
            self._speaking = False
            return VTubeStudioAction(ok=True, action_type="speaking_state", detail="speaking ended", event_type=event.type, event_id=event.event_id)
        if event.type == "mouth_level":
            return VTubeStudioAction(ok=True, action_type="mouth_level", detail="mouth level observed; transport not connected", event_type=event.type, event_id=event.event_id)
        return VTubeStudioAction(ok=True, action_type="ignored", detail="event has no VTube Studio mapping", event_type=event.type, event_id=event.event_id)

    def _hotkey_action(
        self,
        event: PerformerOutputEvent,
        key: str,
        mapping: dict[str, str],
        mapping_type: str,
    ) -> VTubeStudioAction:
        hotkey_id = mapping.get(key)
        if not hotkey_id:
            return VTubeStudioAction(
                ok=True,
                action_type="no_mapping",
                detail=f"no {mapping_type} hotkey mapping for {key or 'blank'}",
                event_type=event.type,
                event_id=event.event_id,
            )
        return VTubeStudioAction(
            ok=True,
            action_type="hotkey",
            detail=f"trigger {mapping_type} hotkey",
            request={
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": event.event_id,
                "messageType": "HotkeyTriggerRequest",
                "data": {"hotkeyID": hotkey_id},
            },
            event_type=event.type,
            event_id=event.event_id,
        )


class VTubeStudioRunner:
    def __init__(
        self,
        bus: PerformerEventBus,
        adapter: VTubeStudioAdapter | None = None,
        *,
        target_policy: str = STREAM_PUBLIC_TARGET,
    ) -> None:
        self.bus = bus
        self.adapter = adapter or VTubeStudioAdapter()
        self.target_policy = target_policy
        self._running = False
        self._subscriber_id: str | None = None
        self._handled_count = 0
        self._last_sequence: int | None = None
        self._last_error: str | None = None
        self._stop_event: asyncio.Event | None = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "target_policy": self.target_policy,
            "subscriber_id": self._subscriber_id,
            "handled_count": self._handled_count,
            "last_sequence": self._last_sequence,
            "last_error": self._last_error,
        }

    async def run_until_stopped(self, *, replay_recent: int = 0, after_sequence: int | None = None) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        subscriber_id: str | None = None
        try:
            subscriber_id, queue = await self.bus.subscribe(
                replay_recent=replay_recent,
                after_sequence=after_sequence,
                target_policy=self.target_policy,
                client_name="vtube_studio_runner",
            )
            self._subscriber_id = subscriber_id
            while not self._stop_event.is_set():
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                try:
                    event = PerformerOutputEvent(**payload)
                    await self.adapter.handle_event_async(event)
                    self._handled_count += 1
                    self._last_sequence = event.sequence
                    self._last_error = None
                except Exception as exc:
                    self._last_error = str(exc)
        finally:
            self._running = False
            if subscriber_id is not None:
                self.bus.unsubscribe(subscriber_id)
            self._subscriber_id = None
            await self.adapter.client.close()

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).strip().lower(): str(item).strip() for key, item in value.items() if str(key).strip() and str(item).strip()}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
