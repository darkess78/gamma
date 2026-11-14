"""Stream traces, performer output, rehearsal, Monitor input and image requests, proxied to Shana."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..schemas.response import AssistantResponse, VisionAnalysis


class StreamAdminMixin:
    """Stream traces, performer output, rehearsal, Monitor input and image requests, proxied to Shana."""

    def stream_recent_traces(self, *, limit: int = 50) -> dict[str, Any]:
        return self._shana.get("/v1/stream/traces/recent", params={"limit": max(1, min(limit, 200))})

    def stream_recent_eval(self, *, limit: int = 50) -> dict[str, Any]:
        return self._shana.get("/v1/stream/eval/recent", params={"limit": max(1, min(limit, 200))})

    def stream_recent_outputs(self, *, limit: int = 50) -> dict[str, Any]:
        return self._shana.get("/v1/stream/outputs/recent", params={"limit": max(1, min(limit, 200))})

    def performer_output_status(self) -> dict[str, Any]:
        url = settings.shana_internal_base_url + "/v1/performer/status"
        payload = self._shana.safe_get("/v1/performer/status")
        if not payload.get("ok", True) and "stats" not in payload:
            return {
                "ok": False,
                "url": url,
                "detail": payload.get("detail", "unavailable"),
                "stats": {},
                "recent_event": None,
                "recent_by_target": {},
            }
        return {
            "ok": True,
            "url": url,
            "stats": payload.get("stats", {}) if isinstance(payload.get("stats", {}), dict) else {},
            "recent_event": payload.get("recent_event") if isinstance(payload.get("recent_event"), dict) else None,
            "recent_by_target": payload.get("recent_by_target") if isinstance(payload.get("recent_by_target"), dict) else {},
            "recent_turns": payload.get("recent_turns") if isinstance(payload.get("recent_turns"), list) else [],
            "adapters": payload.get("adapters") if isinstance(payload.get("adapters"), dict) else {},
        }

    def set_performer_target_mute(self, target_policy: str, *, muted: bool, reason: str = "dashboard") -> dict[str, Any]:
        safe_target = urllib.parse.quote(target_policy.strip().lower() or "stream_public")
        action = "mute" if muted else "unmute"
        return self._post_remote_json(f"/v1/performer/targets/{safe_target}/{action}?reason={urllib.parse.quote(reason)}", {})

    def clear_performer_target(self, target_policy: str, *, reason: str = "dashboard") -> dict[str, Any]:
        safe_target = urllib.parse.quote(target_policy.strip().lower() or "stream_public")
        return self._post_remote_json(f"/v1/performer/targets/{safe_target}/clear?reason={urllib.parse.quote(reason)}", {})

    def stream_pending_queue(self) -> dict[str, Any]:
        return self._shana.get("/v1/stream/queue")

    def stream_temp_memory(self, *, bucket: str | None = None, limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 1000))}
        if bucket:
            params["bucket"] = bucket
        return self._shana.get("/v1/stream/temp-memory", params=params)

    def clear_stream_temp_memory(self, *, bucket: str | None = None) -> dict[str, Any]:
        return self._shana.delete("/v1/stream/temp-memory", params={"bucket": bucket} if bucket else None)

    def stream_self_goals(self, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 1000))}
        if status:
            params["status"] = status
        return self._shana.get("/v1/stream/self-goals", params=params)

    def set_stream_self_goal_status(self, goal_id: int, *, status: str) -> dict[str, Any]:
        if status not in {"approve", "reject"}:
            raise ValueError("unsupported self-goal status action")
        return self._post_remote_json(f"/v1/stream/self-goals/{goal_id}/{status}", {})

    def clear_stream_self_goals(self) -> dict[str, Any]:
        return self._post_remote_json("/v1/stream/self-goals/clear", {})

    def stop_stream_speech(self, *, reason: str = "operator_stop") -> dict[str, Any]:
        return self._post_remote_json(f"/v1/stream/stop?reason={urllib.parse.quote(reason)}", {})

    def stream_rehearsal_status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": dict(self._stream_rehearsal_state),
            "events": list(self._stream_rehearsal_events),
            "results": list(self._stream_rehearsal_results),
        }

    def start_stream_rehearsal(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            session_id = f"local-rehearsal-{stamp}"
        target_policy = self._normalize_output_target_policy(payload.get("output_target_policy"))
        self._stream_rehearsal_state = {
            "enabled": True,
            "session_id": session_id,
            "synthesize_speech": bool(payload.get("synthesize_speech", False)),
            "fast_mode": bool(payload.get("fast_mode", True)),
            "output_target_policy": target_policy,
            "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "stopped_at": "",
            "event_count": 0,
            "last_error": "",
        }
        self._stream_rehearsal_events.clear()
        self._stream_rehearsal_results.clear()
        return self.stream_rehearsal_status()

    def stop_stream_rehearsal(self) -> dict[str, Any]:
        self._stream_rehearsal_state["enabled"] = False
        self._stream_rehearsal_state["stopped_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return self.stream_rehearsal_status()

    def inject_stream_rehearsal_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._stream_rehearsal_state.get("enabled"):
            raise ValueError("stream rehearsal is not running")
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        event_type = str(payload.get("event_type") or payload.get("kind") or "chat").strip().lower()
        event_kind = self._rehearsal_event_kind(event_type)
        display_name = str(payload.get("display_name") or "Local operator").strip() or "Local operator"
        priority = max(0, min(100, int(payload.get("priority", 5 if event_kind == "chat_message" else 0))))
        session_id = str(payload.get("session_id") or self._stream_rehearsal_state.get("session_id") or "").strip()
        event = {
            "kind": event_kind,
            "text": text,
            "session_id": session_id,
            "priority": priority,
            "actor": {
                "source": "local_rehearsal",
                "platform_id": "dashboard",
                "display_name": display_name,
                "roles": ["operator"],
            },
            "metadata": {
                "rehearsal": True,
                "rehearsal_event_type": event_type,
                "output_target_policy": self._stream_rehearsal_state.get("output_target_policy") or "dashboard_monitor",
                "source": "dashboard_stream_rehearsal",
            },
        }
        self._stream_rehearsal_events.append(
            {
                "injected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "event": event,
            }
        )
        path = (
            "/v1/stream/events"
            f"?synthesize_speech={'true' if self._stream_rehearsal_state.get('synthesize_speech') else 'false'}"
            f"&fast_mode={'true' if self._stream_rehearsal_state.get('fast_mode', True) else 'false'}"
        )
        try:
            result = self._post_remote_json(path, event, timeout=180)
        except Exception as exc:
            self._stream_rehearsal_state["last_error"] = str(exc)
            raise
        if isinstance(result.get("input_event"), dict):
            self._stream_rehearsal_events[-1]["event"] = result["input_event"]
        self._stream_rehearsal_state["event_count"] = int(self._stream_rehearsal_state.get("event_count") or 0) + 1
        self._stream_rehearsal_state["last_error"] = ""
        self._stream_rehearsal_results.append(
            {
                "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": result,
            }
        )
        return {"ok": True, "event": event, "result": result, "state": dict(self._stream_rehearsal_state)}

    def submit_monitor_stream_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        input_mode = str(payload.get("input_mode") or "owner_mic").strip().lower()
        event_kind = self._monitor_event_kind(input_mode)
        session_id = str(payload.get("session_id") or "monitor-local-stream").strip()
        controls = self.twitch_runtime_settings()
        controls["dry_run"] = False
        event = {
            "kind": event_kind,
            "text": text,
            "session_id": session_id,
            "priority": max(0, min(100, int(payload.get("priority", 20 if event_kind in {"mic_transcript", "owner_command"} else 5)))),
            "actor": {
                "source": "local_monitor",
                "platform_id": "owner",
                "display_name": str(payload.get("display_name") or "Owner").strip() or "Owner",
                "roles": ["owner", "operator"],
            },
            "metadata": {
                "local_monitor": True,
                "input_mode": input_mode,
                "output_target_policy": self._normalize_output_target_policy(payload.get("output_target_policy")),
                "twitch_controls": controls,
                "source": "dashboard_monitor_input",
            },
        }
        synthesize_speech = payload.get("synthesize_speech")
        if synthesize_speech is None:
            synthesize_speech = True
        fast_mode = payload.get("fast_mode")
        if fast_mode is None:
            fast_mode = True
        path = (
            "/v1/stream/events"
            f"?synthesize_speech={'true' if synthesize_speech else 'false'}"
            f"&fast_mode={'true' if fast_mode else 'false'}"
        )
        result = self._post_remote_json(path, event, timeout=180)
        return {"ok": True, "event": result.get("input_event", event), "result": result}

    @staticmethod
    def _normalize_output_target_policy(value: Any) -> str:
        target_policy = str(value or "dashboard_monitor").strip().lower()
        return target_policy if target_policy in {"dashboard_monitor", "stream_public", "discord_call"} else "dashboard_monitor"

    @staticmethod
    def _monitor_event_kind(input_mode: str) -> str:
        mapping = {
            "owner_mic": "mic_transcript",
            "mic_transcript": "mic_transcript",
            "owner_command": "owner_command",
            "chat": "chat_message",
            "chat_message": "chat_message",
            "context": "chat_message",
            "gameplay": "game_state",
            "game_state": "game_state",
        }
        return mapping.get(input_mode, "mic_transcript")

    @staticmethod
    def _rehearsal_event_kind(event_type: str) -> str:
        mapping = {
            "chat": "chat_message",
            "chat_message": "chat_message",
            "gameplay_note": "game_state",
            "game_state": "game_state",
            "context_note": "chat_message",
            "system_note": "system",
            "system": "system",
            "owner_command": "owner_command",
        }
        return mapping.get(event_type, "chat_message")

    def analyze_remote_image(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
    ) -> VisionAnalysis:
        payload = self._post_remote_image(
            path="/v1/vision/analyze",
            image_bytes=image_bytes,
            filename=filename,
            content_type=content_type,
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=None,
            synthesize_speech=None,
        )
        return VisionAnalysis.model_validate(payload)

    def respond_remote_image(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
        session_id: str | None,
        synthesize_speech: bool,
    ) -> AssistantResponse:
        payload = self._post_remote_image(
            path="/v1/conversation/respond-with-image",
            image_bytes=image_bytes,
            filename=filename,
            content_type=content_type,
            user_text=user_text,
            vision_mode=vision_mode,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
        )
        return AssistantResponse.model_validate(payload)

    def _post_remote_image(
        self,
        *,
        path: str,
        image_bytes: bytes,
        filename: str,
        content_type: str,
        user_text: str,
        vision_mode: str | None,
        session_id: str | None,
        synthesize_speech: bool | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"user_text": user_text}
        if vision_mode:
            data["vision_mode"] = vision_mode
        if session_id:
            data["session_id"] = session_id
        if synthesize_speech is not None:
            data["synthesize_speech"] = "true" if synthesize_speech else "false"
        return self._shana.post_multipart(
            path,
            data=data,
            field_name="image_file",
            filename=filename,
            content=image_bytes,
            content_type=content_type,
        )

    def _post_remote_json(self, path: str, payload: dict[str, Any], *, timeout: float = 10) -> dict[str, Any]:
        return self._shana.post(path, payload, timeout=timeout)
