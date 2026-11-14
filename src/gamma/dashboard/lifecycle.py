"""Presence modes and starting or stopping Shana and the dashboard."""
from __future__ import annotations

import os
import subprocess
import threading
from typing import Any

from ..config import settings
from ..presence import load_presence_state


class LifecycleMixin:
    """Presence modes and starting or stopping Shana and the dashboard."""

    def presence_summary(self) -> dict[str, Any]:
        return self._shana.safe_get("/v1/presence")

    def presence_status(self) -> dict[str, Any]:
        remote_presence = self._shana.safe_get("/v1/presence")
        state = dict(remote_presence.get("state") or load_presence_state(downgrade_stale_live=False))
        memory = self._shana.safe_get("/v1/memory", params={"limit": 100})
        runtime_status = self.build_runtime_status()
        performer = self.performer_output_status()
        stream_ready = self.stream_ready_status()
        activity = dict(state.get("activity") or {})
        activity["stream_ready_mode"] = stream_ready.get("mode")
        recent_by_target = performer.get("recent_by_target") if isinstance(performer.get("recent_by_target"), dict) else {}
        current_output = recent_by_target.get("stream_public") or recent_by_target.get("dashboard_monitor") or performer.get("recent_event")
        if isinstance(current_output, dict):
            activity["current_turn_id"] = current_output.get("turn_id")
            activity["last_output_target"] = current_output.get("target_policy")
        state["activity"] = activity
        return {
            "ok": True,
            "state": state,
            "known_people": memory.get("known_people", []),
            "continuity": remote_presence.get("continuity"),
            "last_durable_output": remote_presence.get("last_durable_output"),
            "runtime": {
                "shana": runtime_status.get("shana", {}),
                "twitch": {
                    "worker": self.twitch_worker_status(),
                    "eventsub": self.twitch_eventsub_status(),
                    "stream_ready": stream_ready,
                },
                "performer": performer,
            },
        }

    def set_presence_mode(
        self,
        mode: str,
        *,
        confirm_public_output: bool = False,
        updated_by: str = "dashboard",
        audience: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(mode or "").strip().lower().replace("-", "_")
        if normalized == "go_live" and not confirm_public_output:
            raise ValueError("confirm_public_output is required for Go Live")
        payload = {
            "mode": normalized,
            "confirm_public_output": confirm_public_output,
            "audience": audience or {"kind": "unknown"},
            "session_id": session_id,
        }
        path = "/v1/presence/wake" if normalized == "wake" else "/v1/presence/mode"
        if normalized == "wake":
            payload.pop("mode", None)
            payload.pop("confirm_public_output", None)
        result = self._shana.post(path, payload, timeout=180)
        state = dict(result.get("state") or {})
        result["actions"] = self._apply_presence_side_effects(state)
        result.setdefault("detail", self._presence_detail(state))
        return result

    def _apply_presence_side_effects(self, state: dict[str, Any]) -> dict[str, Any]:
        mode = str(state.get("mode") or "sleep")
        actions: dict[str, Any] = {}
        if mode in {"sleep", "break"}:
            actions["stream_stop"] = self._safe_presence_action(lambda: self.stop_stream_speech(reason=f"presence_{mode}"))
            actions["stream_public_clear"] = self._safe_presence_action(lambda: self.clear_performer_target("stream_public", reason=f"presence_{mode}"))
            actions["stream_public_mute"] = self._safe_presence_action(
                lambda: self.set_performer_target_mute("stream_public", muted=True, reason=f"presence_{mode}")
            )
        elif mode == "wake":
            actions["stream_public_clear"] = self._safe_presence_action(lambda: self.clear_performer_target("stream_public", reason="presence_wake"))
            actions["stream_public_mute"] = self._safe_presence_action(
                lambda: self.set_performer_target_mute("stream_public", muted=True, reason="presence_wake")
            )
            actions["dashboard_monitor_unmute"] = self._safe_presence_action(
                lambda: self.set_performer_target_mute("dashboard_monitor", muted=False, reason="presence_wake")
            )
        elif mode == "go_live":
            actions["stream_public_unmute"] = self._safe_presence_action(
                lambda: self.set_performer_target_mute("stream_public", muted=False, reason="presence_go_live")
            )
            actions["dashboard_monitor_unmute"] = self._safe_presence_action(
                lambda: self.set_performer_target_mute("dashboard_monitor", muted=False, reason="presence_go_live")
            )
        return actions

    @staticmethod
    def _safe_presence_action(callback) -> dict[str, Any]:
        try:
            return callback()
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    @staticmethod
    def _presence_detail(state: dict[str, Any]) -> str:
        mode = str(state.get("mode") or "sleep")
        if mode == "sleep":
            return "Presence set to Sleep. Public and proactive output are suppressed."
        if mode == "wake":
            return "Presence set to Wake. Local monitor interaction is enabled; stream output remains muted."
        if mode == "break":
            return "Presence set to Break. Observation can continue while public output is muted."
        return "Presence set to Go Live. Public stream output is enabled for this Shana backend session."

    def start_shana(self) -> dict[str, Any]:
        if self._uses_systemd_user_lifecycle():
            return self._run_systemd_user_shana_action("start")
        return self._process_manager.start("shana")

    def stop_shana(self) -> dict[str, Any]:
        if self._uses_systemd_user_lifecycle():
            return self._run_systemd_user_shana_action("stop")
        return self._process_manager.stop("shana")

    def restart_shana(self) -> dict[str, Any]:
        if self._uses_systemd_user_lifecycle():
            return self._run_systemd_user_shana_action("restart")
        return self._process_manager.restart("shana")

    def stop_dashboard(self) -> dict[str, Any]:
        if self._uses_systemd_user_lifecycle():
            return {
                "ok": False,
                "detail": "Dashboard is configured as an always-on systemd user service and was not stopped.",
                "url": settings.dashboard_base_url,
            }
        self._schedule_stop("dashboard")
        return {"ok": True, "detail": "dashboard-stop-scheduled", "url": settings.dashboard_base_url}

    def stop_all(self) -> dict[str, Any]:
        shana_result = self.stop_shana()
        twitch_result = self.stop_twitch_worker()
        eventsub_result = self.stop_twitch_eventsub_worker()
        discord_result = self.stop_discord_text_worker()
        tts_results = self._stop_all_tts_servers()
        preserve_dashboard = self._uses_systemd_user_lifecycle()
        if not preserve_dashboard:
            self._schedule_stop("dashboard")
        tts_ok = all(bool(result.get("ok")) for result in tts_results.values())
        return {
            "ok": bool(shana_result.get("ok", False)) and bool(twitch_result.get("ok", False)) and bool(eventsub_result.get("ok", False)) and bool(discord_result.get("ok", False)) and tts_ok,
            "detail": "runtime-services-stop-requested" if preserve_dashboard else "all-stop-scheduled",
            "shana": shana_result,
            "twitch_worker": twitch_result,
            "twitch_eventsub": eventsub_result,
            "discord_text": discord_result,
            "tts": tts_results,
            "dashboard_url": settings.dashboard_base_url,
            "dashboard_preserved": preserve_dashboard,
        }

    @staticmethod
    def _uses_systemd_user_lifecycle() -> bool:
        return os.environ.get("GAMMA_LIFECYCLE_MANAGER", "").strip().lower() == "systemd-user"

    def _run_systemd_user_shana_action(self, action: str) -> dict[str, Any]:
        completed = subprocess.run(
            ["systemctl", "--user", action, self.SHANA_SYSTEMD_USER_UNIT],
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "systemctl command failed").strip()
            return {"ok": False, "detail": detail, "service": self.SHANA_SYSTEMD_USER_UNIT}
        return {
            "ok": True,
            "detail": f"shana-systemd-{action}-completed",
            "service": self.SHANA_SYSTEMD_USER_UNIT,
            "url": settings.shana_base_url,
        }

    def _schedule_stop(self, service_name: str, delay_seconds: float = 0.35) -> None:
        if service_name == "dashboard":
            timer = threading.Timer(delay_seconds, self._stop_dashboard_process)
            timer.daemon = True
            timer.start()
            return
        timer = threading.Timer(delay_seconds, lambda: self._process_manager.stop(service_name))
        timer.daemon = True
        timer.start()

    def _stop_dashboard_process(self) -> None:
        try:
            process = self._process_manager.find_process("dashboard")
            current_pid = os.getpid()
            if process and process.pid != current_pid:
                self._process_manager.stop("dashboard")
                return
            self._process_manager.clear_pid_file("dashboard")
        finally:
            os._exit(0)
