from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import deque
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config import app_local_config_path, load_app_file_config, load_desired_tts_selection, settings
from ..errors import ConfigurationError
from ..integrations.discord import DiscordRuntimeConfig, read_discord_worker_state
from ..integrations.twitch.client import GammaStreamClient
from ..integrations.twitch.eventsub import TwitchEventSubConfig, read_twitch_eventsub_state
from ..integrations.twitch.replay import replay_jsonl_text
from ..integrations.twitch.worker import TwitchWorkerConfig, read_twitch_worker_state
from ..presence import load_presence_state
from ..resources import MachineResourceMonitor
from ..resources.allocations import recent_sidecar_allocation_entries
from ..resources.runtime_registry import load_resource_routing_registry
from ..schemas.response import AssistantResponse, VisionAnalysis
from ..schemas.voice import VoiceRoundtripResponse
from ..supervisor.manager import ProcessManager
from ..system.cuda_env import prepend_cuda_library_path
from ..voice.voice_profiles import get_voice_profile, list_voice_profiles, profile_template, save_voice_profile
from .shana_client import ShanaApiClient, ShanaClientError


class DashboardService:
    TWITCH_WORKER_SERVICE = "twitch_worker"
    TWITCH_WORKER_MODULE = "gamma.integrations.twitch.worker"
    TWITCH_EVENTSUB_SERVICE = "twitch_eventsub"
    TWITCH_EVENTSUB_MODULE = "gamma.integrations.twitch.eventsub"
    DISCORD_TEXT_SERVICE = "discord_text"
    DISCORD_TEXT_MODULE = "gamma.integrations.discord.worker"
    TWITCH_STATE_STALE_SECONDS = 120
    TWITCH_DRY_RUN_SCENARIO = "\n".join(
        [
            json.dumps({"kind": "chat_message", "platform_user_id": "u1", "display_name": "ViewerOne", "text": "Shana what are you doing?"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "u2", "display_name": "TopicFan", "text": "what is happening with this boss fight chat?"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "u3", "display_name": "QuietViewer", "text": "lol"}),
            json.dumps({"kind": "chat_message", "platform_user_id": "spam1", "display_name": "buy_views_9281", "text": "buy viewers at https://badsite.example"}),
            json.dumps({"kind": "follow", "platform_user_id": "u4", "display_name": "NewViewer"}),
            json.dumps({"kind": "raid", "platform_user_id": "u5", "display_name": "Raider", "viewer_count": 42}),
            json.dumps({"kind": "bits", "platform_user_id": "u6", "display_name": "BitsFan", "amount": "100", "text": "nice stream"}),
            json.dumps({"kind": "bits", "platform_user_id": "u7", "display_name": "UnsafeBits", "amount": "50", "text": "buy views at badsite.example"}),
            json.dumps({"kind": "redeem", "platform_user_id": "u8", "display_name": "Redeemer", "title": "Say hi", "text": "Say hi to chat"}),
            json.dumps({"kind": "redeem", "platform_user_id": "u9", "display_name": "MentionRedeemer", "title": "Ask Shana", "text": "Shana, say hi"}),
        ]
    )

    def __init__(self) -> None:
        self._shana = ShanaApiClient()
        self._process_manager = ProcessManager()
        self._resource_monitor = MachineResourceMonitor(
            project_root=settings.project_root,
            enable_gpu=lambda: settings.dashboard_enable_gpu,
            refresh_interval_seconds=lambda: settings.dashboard_metrics_interval_seconds,
        )
        self._latest_provider_action: dict[str, Any] = {"status": "idle", "detail": "No provider action has been run yet."}
        self._latest_twitch_replay_summary: dict[str, Any] = {}
        self._stream_rehearsal_state: dict[str, Any] = {
            "enabled": False,
            "session_id": "",
            "synthesize_speech": False,
            "fast_mode": True,
            "output_target_policy": "dashboard_monitor",
            "started_at": "",
            "stopped_at": "",
            "event_count": 0,
            "last_error": "",
        }
        self._stream_rehearsal_events: deque[dict[str, Any]] = deque(maxlen=25)
        self._stream_rehearsal_results: deque[dict[str, Any]] = deque(maxlen=25)

    def build_status(self) -> dict[str, Any]:
        local_status = self._remote_system_status()
        route_info = self._recent_llm_routes()
        selected_tts_provider = self.selected_tts_provider()
        selected_tts_profile = self.selected_tts_profile()
        running_tts_provider = self._canonical_tts_provider(
            str(local_status["providers"]["tts"].get("provider") or "")
        )
        local_status["providers"]["tts"]["provider"] = running_tts_provider
        running_tts_profile = str(local_status["providers"]["tts"].get("profile_id") or "").strip()
        local_status["providers"]["tts"]["selected_provider"] = selected_tts_provider
        local_status["providers"]["tts"]["selected_profile"] = selected_tts_profile
        local_status["providers"]["tts"]["selected_profile_label"] = (
            get_voice_profile(selected_tts_profile).label if get_voice_profile(selected_tts_profile) else None
        )
        local_status["providers"]["tts"]["restart_required"] = (
            selected_tts_provider != running_tts_provider or (selected_tts_profile or "") != running_tts_profile
        )
        local_status["providers"]["tts"]["available_providers"] = ["qwen-tts", "piper", "openai"]
        available_profiles: list[dict[str, Any]] = []
        for profile in list_voice_profiles():
            provider = self._canonical_tts_provider(profile.provider)
            if provider not in {"qwen-tts", "piper", "openai"}:
                continue
            payload = profile.as_payload()
            payload["provider"] = provider
            available_profiles.append(payload)
        local_status["providers"]["tts"]["available_profiles"] = available_profiles
        local_status["providers"]["tts"]["editor_profile"] = self.tts_profile_editor_state(
            selected_tts_profile,
            selected_tts_provider,
        )
        local_status["providers"]["tts"]["test_control"] = self._tts_test_control_state(
            selected_tts_provider,
            selected_tts_profile,
        )
        local_status["providers"]["llm"]["router_enabled"] = settings.llm_router_enabled
        local_status["providers"]["llm"]["router_profile"] = settings.llm_router_profile
        local_status["providers"]["llm"]["router_default_provider"] = settings.llm_router_default_provider or settings.llm_provider
        local_status["providers"]["llm"]["router_default_model"] = settings.llm_router_default_model or settings.llm_model
        local_status["providers"]["llm"]["router_hosted_escalation"] = settings.llm_router_allow_hosted_escalation
        local_status["providers"]["llm"]["router_hosted_provider"] = settings.llm_router_hosted_provider
        local_status["providers"]["llm"]["router_hosted_model"] = settings.llm_router_hosted_model or settings.llm_model
        local_status["providers"]["llm"]["router_failure_backoff_seconds"] = settings.llm_router_failure_backoff_seconds
        local_status["providers"]["llm"]["route_summary"] = route_info["summary"]
        local_status["providers"]["llm"]["last_route"] = route_info["entries"][-1] if route_info["entries"] else None
        local_status["providers"]["llm"]["provider_backoff"] = local_status["providers"]["llm"].get(
            "provider_backoff", {}
        )
        local_status["providers"]["llm"]["provider_backoff_entries"] = self._format_router_backoff_entries(
            local_status["providers"]["llm"]["provider_backoff"]
        )
        local_status["providers"]["llm"]["router_capabilities"] = self._build_router_capability_status(
            local_status["providers"]["llm"]
        )
        runtime_status = self.build_runtime_status()
        system_status = (
            {"ok": True, "payload": local_status}
            if local_status.get("app")
            else {"ok": False, "detail": local_status.get("detail", "unavailable")}
        )
        memory_snapshot = self._shana.safe_get("/v1/memory", params={"limit": 100})
        return {
            "dashboard": {
                "name": f"{settings.app_name} dashboard",
                "url": settings.dashboard_base_url,
            },
            "app": local_status["app"],
            "providers": local_status["providers"],
            "recent_artifacts": local_status["recent_artifacts"],
            "shana": {
                **runtime_status["shana"],
                "system_status": system_status,
                "logs": {
                    "stdout_path": str(self._process_manager.stdout_log("shana")),
                    "stderr_path": str(self._process_manager.stderr_log("shana")),
                    "stdout_tail": self._tail(self._process_manager.stdout_log("shana")),
                    "stderr_tail": self._tail(self._process_manager.stderr_log("shana")),
                },
            },
            "machine": runtime_status["machine"],
            "memory_db": {
                "stats": memory_snapshot.get("stats", {}),
                "known_people": memory_snapshot.get("known_people", []),
                "recent_items": memory_snapshot.get("recent_items", []),
            },
            "assistant": {
                "emotion_memory": local_status.get("assistant", {}).get("emotion_memory", {}),
                "settings": self.assistant_runtime_settings(),
            },
            "twitch": {
                "worker": self.twitch_worker_status(),
                "eventsub": self.twitch_eventsub_status(),
                "stream_ready": self.stream_ready_status(),
            },
            "presence": self.presence_summary(),
            "discord": {
                "text_worker": self.discord_text_worker_status(),
            },
            "performer": self.performer_output_status(),
            "provider_actions": self._latest_provider_action,
            "timings": self._recent_timings(),
            "llm_routing": route_info,
            "startup_admission": self._recent_startup_admission(),
            "sidecar_allocations": self._recent_sidecar_allocations(),
        }

    def build_monitor_status(self) -> dict[str, Any]:
        local_status = self._remote_system_status()
        selected_tts_provider = self.selected_tts_provider()
        selected_tts_profile = self.selected_tts_profile()
        running_tts_provider = self._canonical_tts_provider(
            str(local_status["providers"]["tts"].get("provider") or "")
        )
        local_status["providers"]["tts"]["provider"] = running_tts_provider
        local_status["providers"]["tts"]["selected_provider"] = selected_tts_provider
        local_status["providers"]["tts"]["selected_profile"] = selected_tts_profile
        local_status["providers"]["tts"]["restart_required"] = (
            selected_tts_provider != running_tts_provider
            or (selected_tts_profile or "") != str(local_status["providers"]["tts"].get("profile_id") or "").strip()
        )
        runtime_status = self.build_runtime_status()
        return {
            "ok": True,
            "dashboard": {
                "name": f"{settings.app_name} monitor",
                "url": settings.dashboard_base_url,
            },
            "providers": local_status["providers"],
            "shana": runtime_status["shana"],
            "machine": runtime_status["machine"],
            "twitch": {
                "worker": self.twitch_worker_status(),
                "eventsub": self.twitch_eventsub_status(),
            },
            "performer": self.performer_output_status(),
        }

    def build_status_summary(self) -> dict[str, Any]:
        local_status = self._remote_system_status()
        runtime_status = self.build_runtime_status()
        return {
            "ok": True,
            "dashboard": {
                "name": f"{settings.app_name} dashboard",
                "url": settings.dashboard_base_url,
            },
            "app": local_status.get("app", {}),
            "providers": local_status.get("providers", {}),
            "shana": runtime_status["shana"],
            "machine": runtime_status["machine"],
            "twitch": {
                "worker": self.twitch_worker_status(),
                "eventsub": self.twitch_eventsub_status(),
                "stream_ready": self.stream_ready_status(),
            },
            "presence": self.presence_summary(),
            "performer": self.performer_output_status(),
        }

    def build_header_status(self) -> dict[str, Any]:
        """Return inexpensive state used by navigation and overview polling."""
        shana_status = self._build_shana_runtime_status()
        twitch_process = self._process_manager.module_status(
            self.TWITCH_WORKER_SERVICE,
            self.TWITCH_WORKER_MODULE,
        ).get("process", {})
        eventsub_process = self._process_manager.module_status(
            self.TWITCH_EVENTSUB_SERVICE,
            self.TWITCH_EVENTSUB_MODULE,
        ).get("process", {})
        discord_process = self._process_manager.module_status(
            self.DISCORD_TEXT_SERVICE,
            self.DISCORD_TEXT_MODULE,
        ).get("process", {})
        return {
            "ok": True,
            "dashboard": {
                "name": f"{settings.app_name} dashboard",
                "url": settings.dashboard_base_url,
            },
            "app": {"name": settings.app_name},
            "providers": {
                "llm": {"provider": settings.llm_provider, "model": settings.llm_model},
                "stt": {"provider": settings.stt_provider, "model": settings.stt_model},
                "tts": {
                    "provider": self.selected_tts_provider(),
                    "selected_profile": self.selected_tts_profile(),
                },
            },
            "shana": shana_status,
            "machine": {},
            "memory_db": {"stats": {}, "known_people": [], "recent_items": []},
            "assistant": {},
            "twitch": {
                "worker": {
                    "process": twitch_process,
                    "configured": not self._missing_twitch_irc_config(),
                },
                "eventsub": {
                    "process": eventsub_process,
                    "configured": not self._missing_twitch_eventsub_config(),
                    "enabled": bool(settings.twitch_eventsub_enabled),
                },
                "stream_ready": {},
            },
            "discord": {"text_worker": {"process": discord_process}},
            "presence": self.presence_summary(),
            "performer": self.performer_output_status(),
        }

    def build_diagnostics_status(self) -> dict[str, Any]:
        """Return Status-page data without duplicating unbounded domain history."""
        payload = self.build_status()

        system_status = dict(payload.get("shana", {}).get("system_status") or {})
        payload["shana"]["system_status"] = {
            "ok": bool(system_status.get("ok")),
            "detail": system_status.get("detail") or ("ok" if system_status.get("ok") else "unavailable"),
        }
        logs = payload["shana"].get("logs") or {}
        for key in ("stdout_tail", "stderr_tail"):
            value = str(logs.get(key) or "")
            logs[key] = value[-2000:]

        payload["memory_db"] = {"stats": {}, "known_people": [], "recent_items": []}
        payload["assistant"] = {}
        payload["timings"] = {"summary": (payload.get("timings") or {}).get("summary", {})}

        tts = payload.get("providers", {}).get("tts", {})
        tts["available_profiles"] = [
            {
                "id": profile.get("id"),
                "label": profile.get("label"),
                "provider": profile.get("provider"),
            }
            for profile in tts.get("available_profiles", [])
            if isinstance(profile, dict)
        ]

        routing = payload.get("llm_routing") or {}
        shadow = dict(routing.get("placement_shadow") or {})
        shadow["entries"] = list(shadow.get("entries") or [])[-4:]
        payload["llm_routing"] = {"placement_shadow": shadow}
        for key in ("startup_admission", "sidecar_allocations"):
            section = dict(payload.get(key) or {})
            section["entries"] = list(section.get("entries") or [])[-4:]
            payload[key] = section

        twitch = payload.get("twitch") or {}
        worker = twitch.get("worker") or {}
        eventsub = twitch.get("eventsub") or {}
        stream_ready = twitch.get("stream_ready") or {}
        payload["twitch"] = {
            "worker": {
                "process": worker.get("process", {}),
                "configured": worker.get("configured"),
                "missing_config": worker.get("missing_config", []),
            },
            "eventsub": {
                "process": eventsub.get("process", {}),
                "configured": eventsub.get("configured"),
                "enabled": eventsub.get("enabled"),
                "missing_config": eventsub.get("missing_config", []),
            },
            "stream_ready": {
                "ok": stream_ready.get("ok"),
                "mode": stream_ready.get("mode"),
                "detail": stream_ready.get("detail"),
                "blocker_count": stream_ready.get("blocker_count", 0),
                "warning_count": stream_ready.get("warning_count", 0),
            },
        }
        return payload

    def build_settings_status(self) -> dict[str, Any]:
        """Return Settings data without unbounded logs, memory, and domain histories."""
        payload = self.build_status()
        system_status = dict(payload.get("shana", {}).get("system_status") or {})
        payload["shana"]["system_status"] = {
            "ok": bool(system_status.get("ok")),
            "detail": system_status.get("detail") or ("ok" if system_status.get("ok") else "unavailable"),
        }
        logs = dict(payload["shana"].get("logs") or {})
        payload["shana"]["logs"] = {
            "stdout_path": logs.get("stdout_path"),
            "stderr_path": logs.get("stderr_path"),
            "stdout_tail": "",
            "stderr_tail": "",
        }
        payload["memory_db"] = {"stats": {}, "known_people": [], "recent_items": []}
        payload["timings"] = {"summary": (payload.get("timings") or {}).get("summary", {})}
        payload["llm_routing"] = {"placement_shadow": {"entries": [], "summary": {}}}
        payload["startup_admission"] = {"entries": [], "summary": {}}
        payload["sidecar_allocations"] = {"entries": [], "summary": {}}
        payload["recent_artifacts"] = list(payload.get("recent_artifacts") or [])[-10:]

        twitch = payload.get("twitch") or {}
        worker = twitch.get("worker") or {}
        eventsub = twitch.get("eventsub") or {}
        payload["twitch"] = {
            "worker": {
                "process": worker.get("process", {}),
                "configured": worker.get("configured"),
            },
            "eventsub": {
                "process": eventsub.get("process", {}),
                "configured": eventsub.get("configured"),
                "enabled": eventsub.get("enabled"),
            },
            "stream_ready": {},
        }
        performer = payload.get("performer") or {}
        payload["performer"] = {
            "ok": performer.get("ok"),
            "detail": performer.get("detail"),
            "stats": performer.get("stats", {}),
            "recent_event": performer.get("recent_event"),
            "recent_by_target": performer.get("recent_by_target", {}),
        }
        return payload

    def build_memory_status(self) -> dict[str, Any]:
        """Return Memory-page data without loading the full diagnostics payload."""
        payload = self.build_header_status()
        snapshot = self._shana.safe_get("/v1/memory", params={"limit": 100})
        payload["memory_db"] = {
            "stats": snapshot.get("stats", {}),
            "known_people": snapshot.get("known_people", []),
            "recent_items": snapshot.get("recent_items", []),
        }
        return payload

    def _tts_test_control_state(self, provider: str, profile_id: str | None) -> dict[str, Any]:
        normalized = (provider or "").strip().lower()
        profile = get_voice_profile(profile_id)
        values = profile.values if profile and isinstance(profile.values, dict) else {}
        if normalized in {"openai", "piper"}:
            return {"enabled": True, "reason": ""}
        if self._is_qwen_provider(normalized):
            speaker = str(values.get("qwen_tts_speaker", "")).strip()
            ref_audio = str(values.get("qwen_tts_reference_audio", "")).strip()
            if speaker:
                return {"enabled": True, "reason": ""}
            if not ref_audio:
                return {
                    "enabled": False,
                    "reason": "Test TTS needs a Qwen profile with either a built-in speaker or a reference audio file.",
                }
            if not self._path_exists(ref_audio):
                return {
                    "enabled": False,
                    "reason": f"Missing Qwen reference audio: {ref_audio}",
                }
            return {"enabled": True, "reason": ""}
        return {"enabled": False, "reason": "Test TTS is unavailable for the current provider."}

    def _path_exists(self, value: str) -> bool:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = settings.project_root / path
        return path.exists()

    def build_runtime_status(self) -> dict[str, Any]:
        return {
            "shana": self._build_shana_runtime_status(),
            "machine": self._machine_status(),
        }

    def _build_shana_runtime_status(self) -> dict[str, Any]:
        """Return Shana process and API state without sampling host resources."""
        shana_process = self._process_manager.find_process("shana")
        api_probe = self._shana.safe_get("/health")
        api_ok = self._remote_probe_ok(api_probe)
        api_health = {
            "ok": api_ok,
            "detail": "ok" if api_ok else api_probe.get("detail", "unreachable"),
        }
        return {
            "url": settings.shana_base_url,
            "process": self._process_manager.process_payload(shana_process),
            "api_health": api_health,
        }

    def _remote_system_status(self) -> dict[str, Any]:
        payload = self._shana.safe_get("/v1/system/status")
        payload.setdefault("app", {})
        providers = payload.setdefault("providers", {})
        providers.setdefault("llm", {})
        providers.setdefault("stt", {})
        providers.setdefault("tts", {})
        payload.setdefault("recent_artifacts", [])
        payload.setdefault("assistant", {})
        return payload

    @staticmethod
    def _remote_probe_ok(payload: dict[str, Any]) -> bool:
        if payload.get("ok") is False:
            return False
        return payload.get("status") == "ok" or "app" in payload or "providers" in payload

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
        return self._process_manager.start("shana")

    def stop_shana(self) -> dict[str, Any]:
        return self._process_manager.stop("shana")

    def restart_shana(self) -> dict[str, Any]:
        return self._process_manager.restart("shana")

    def stop_dashboard(self) -> dict[str, Any]:
        self._schedule_stop("dashboard")
        return {"ok": True, "detail": "dashboard-stop-scheduled", "url": settings.dashboard_base_url}

    def stop_all(self) -> dict[str, Any]:
        shana_result = self._process_manager.stop("shana")
        twitch_result = self.stop_twitch_worker()
        eventsub_result = self.stop_twitch_eventsub_worker()
        discord_result = self.stop_discord_text_worker()
        tts_results = self._stop_all_tts_servers()
        self._schedule_stop("dashboard")
        tts_ok = all(bool(result.get("ok")) for result in tts_results.values())
        return {
            "ok": bool(shana_result.get("ok", False)) and bool(twitch_result.get("ok", False)) and bool(eventsub_result.get("ok", False)) and bool(discord_result.get("ok", False)) and tts_ok,
            "detail": "all-stop-scheduled",
            "shana": shana_result,
            "twitch_worker": twitch_result,
            "twitch_eventsub": eventsub_result,
            "discord_text": discord_result,
            "tts": tts_results,
            "dashboard_url": settings.dashboard_base_url,
        }

    def start_twitch_worker(self) -> dict[str, Any]:
        try:
            config = TwitchWorkerConfig.from_settings()
        except ConfigurationError as exc:
            return {
                "ok": False,
                "detail": str(exc),
                "auth_required": True,
                "process": {"running": False},
                "missing_config": self._missing_twitch_irc_config(),
            }
        result = self._process_manager.start_module(self.TWITCH_WORKER_SERVICE, self.TWITCH_WORKER_MODULE)
        return {
            **result,
            "channel": config.normalized_channel,
            "worker": "twitch_irc",
        }

    def stop_twitch_worker(self) -> dict[str, Any]:
        return self._process_manager.stop_module(self.TWITCH_WORKER_SERVICE, self.TWITCH_WORKER_MODULE)

    def start_twitch_eventsub_worker(self) -> dict[str, Any]:
        try:
            config = TwitchEventSubConfig.from_settings()
        except ConfigurationError as exc:
            return {
                "ok": False,
                "detail": str(exc),
                "auth_required": True,
                "process": {"running": False},
                "missing_config": self._missing_twitch_eventsub_config(),
            }
        result = self._process_manager.start_module(self.TWITCH_EVENTSUB_SERVICE, self.TWITCH_EVENTSUB_MODULE)
        return {
            **result,
            "broadcaster_user_id": config.broadcaster_user_id,
            "worker": "twitch_eventsub",
        }

    def stop_twitch_eventsub_worker(self) -> dict[str, Any]:
        return self._process_manager.stop_module(self.TWITCH_EVENTSUB_SERVICE, self.TWITCH_EVENTSUB_MODULE)

    def start_discord_text_worker(self) -> dict[str, Any]:
        config = DiscordRuntimeConfig.from_app_config()
        missing = self._missing_discord_text_config(config)
        if not config.enabled or missing:
            return {
                "ok": False,
                "detail": "Discord text worker is disabled or missing required configuration.",
                "process": {"running": False},
                "enabled": config.enabled,
                "missing_config": missing,
            }
        result = self._process_manager.start_module(self.DISCORD_TEXT_SERVICE, self.DISCORD_TEXT_MODULE)
        return {
            **result,
            "worker": "discord_text",
            "guild_id": config.guild_id,
            "text_channel_id": config.text_channel_id,
        }

    def stop_discord_text_worker(self) -> dict[str, Any]:
        return self._process_manager.stop_module(self.DISCORD_TEXT_SERVICE, self.DISCORD_TEXT_MODULE)

    def discord_text_worker_status(self) -> dict[str, Any]:
        config = DiscordRuntimeConfig.from_app_config()
        missing = self._missing_discord_text_config(config)
        return {
            **self._process_manager.module_status(self.DISCORD_TEXT_SERVICE, self.DISCORD_TEXT_MODULE),
            "configured": not missing,
            "missing_config": missing,
            "enabled": config.enabled,
            "guild_id": config.guild_id or None,
            "text_channel_id": config.text_channel_id or None,
            "worker": "discord_text",
            "output_enabled": False,
            "voice_enabled": False,
            "state": read_discord_worker_state(),
        }

    def twitch_eventsub_status(self) -> dict[str, Any]:
        status = self._process_manager.module_status(self.TWITCH_EVENTSUB_SERVICE, self.TWITCH_EVENTSUB_MODULE)
        missing = self._missing_twitch_eventsub_config()
        configured = not missing
        return {
            **status,
            "configured": configured,
            "missing_config": missing,
            "enabled": bool(settings.twitch_eventsub_enabled),
            "broadcaster_user_id": settings.twitch_broadcaster_user_id,
            "worker": "twitch_eventsub",
            "state": read_twitch_eventsub_state(),
        }

    def twitch_worker_status(self) -> dict[str, Any]:
        status = self._process_manager.module_status(self.TWITCH_WORKER_SERVICE, self.TWITCH_WORKER_MODULE)
        missing = self._missing_twitch_irc_config()
        configured = not missing
        return {
            **status,
            "configured": configured,
            "missing_config": missing,
            "channel": settings.twitch_channel.lstrip("#").strip().lower() if settings.twitch_channel else "",
            "worker": "twitch_irc",
            "ignored_bots": list(settings.twitch_ignored_bots),
            "controls": self.twitch_runtime_settings(),
            "state": read_twitch_worker_state(),
        }

    def _missing_twitch_irc_config(self) -> list[str]:
        missing = []
        if not settings.twitch_channel:
            missing.append("twitch_channel")
        if not settings.twitch_bot_username:
            missing.append("twitch_bot_username")
        if not settings.twitch_oauth_token:
            missing.append("twitch_oauth_token")
        return missing

    def _missing_twitch_eventsub_config(self) -> list[str]:
        missing = []
        if not settings.twitch_client_id:
            missing.append("twitch_client_id")
        if not settings.twitch_oauth_token:
            missing.append("twitch_oauth_token")
        if not settings.twitch_broadcaster_user_id:
            missing.append("twitch_broadcaster_user_id")
        return missing

    @staticmethod
    def _missing_discord_text_config(config: DiscordRuntimeConfig) -> list[str]:
        missing = []
        if not config.bot_token:
            missing.append("discord_bot_token")
        if not config.guild_id:
            missing.append("discord_guild_id")
        if not config.text_channel_id:
            missing.append("discord_text_channel_id")
        return missing

    def stream_ready_status(self) -> dict[str, Any]:
        filtered_audio_path = self._resolve_path(settings.stream_filtered_audio_path)
        controls = self.twitch_runtime_settings()
        irc_missing = self._missing_twitch_irc_config()
        eventsub_missing = self._missing_twitch_eventsub_config()
        irc_process = self._process_manager.module_status(self.TWITCH_WORKER_SERVICE, self.TWITCH_WORKER_MODULE).get("process", {})
        eventsub_process = self._process_manager.module_status(self.TWITCH_EVENTSUB_SERVICE, self.TWITCH_EVENTSUB_MODULE).get("process", {})
        irc_state = read_twitch_worker_state()
        eventsub_state = read_twitch_eventsub_state()
        irc_runtime = self._worker_runtime_evidence(irc_process, irc_state, message_key="message_count")
        eventsub_runtime = self._worker_runtime_evidence(eventsub_process, eventsub_state, message_key="notification_count")
        api_probe = self._shana.safe_get("/v1/system/status")
        api_ok = self._remote_probe_ok(api_probe)
        checks = [
            self._stream_ready_check(
                "api",
                "Shana API",
                "ok" if api_ok else "block",
                "API is reachable." if api_ok else f"API is not reachable: {api_probe.get('detail', 'unknown')}",
                evidence={"url": settings.shana_base_url, "detail": api_probe.get("detail", "")},
            ),
            self._stream_ready_check(
                "irc_config",
                "IRC config",
                "ok" if not irc_missing else "block",
                "IRC chat ingestion is configured." if not irc_missing else f"Missing: {', '.join(irc_missing)}",
                evidence={"missing_config": irc_missing},
            ),
            self._stream_ready_check(
                "eventsub_config",
                "EventSub config",
                "ok" if not eventsub_missing else "block",
                "EventSub ingestion is configured." if not eventsub_missing else f"Missing: {', '.join(eventsub_missing)}",
                evidence={"missing_config": eventsub_missing, "enabled": bool(settings.twitch_eventsub_enabled)},
            ),
            self._stream_ready_check(
                "api_auth",
                "API auth",
                "ok" if not settings.api_auth_enabled or settings.api_bearer_token else "block",
                "API auth is disabled." if not settings.api_auth_enabled else (
                    "Worker API token is available." if settings.api_bearer_token else "API auth is enabled but api_bearer_token is missing."
                ),
                evidence={"api_auth_enabled": bool(settings.api_auth_enabled), "has_bearer_token": bool(settings.api_bearer_token)},
            ),
            self._stream_ready_check(
                "filtered_audio",
                "Filtered audio",
                "ok" if filtered_audio_path and filtered_audio_path.exists() else "warn",
                "Filtered fallback audio exists." if filtered_audio_path and filtered_audio_path.exists() else "Filtered fallback audio is missing; fallback becomes text-only.",
                evidence={"path": str(filtered_audio_path) if filtered_audio_path else "", "exists": bool(filtered_audio_path and filtered_audio_path.exists())},
            ),
            self._stream_ready_check(
                "dry_run",
                "Dry run",
                "ok" if controls.get("dry_run") else "warn",
                "Dry run is on." if controls.get("dry_run") else "Dry run is off; live speech/output can happen.",
                evidence={"dry_run": bool(controls.get("dry_run"))},
            ),
            self._stream_ready_check(
                "voice",
                "Voice",
                "warn" if controls.get("voice_enabled") and controls.get("dry_run") else "ok",
                "Voice is enabled while dry run is still on." if controls.get("voice_enabled") and controls.get("dry_run") else (
                    "Voice is enabled." if controls.get("voice_enabled") else "Voice is off."
                ),
                evidence={"voice_enabled": bool(controls.get("voice_enabled")), "subtitles_enabled": bool(controls.get("subtitles_enabled"))},
            ),
            self._stream_ready_check(
                "voice_disabled_for_validation",
                "Validation voice lock",
                "warn" if controls.get("voice_enabled") else "ok",
                "Twitch voice is off for dry-run validation." if not controls.get("voice_enabled") else "Twitch voice is enabled; keep it off during real dry-run validation.",
                evidence={"voice_enabled": bool(controls.get("voice_enabled"))},
            ),
            self._stream_ready_check(
                "subtitles",
                "Subtitles",
                "ok" if controls.get("subtitles_enabled") else "warn",
                "Subtitles are enabled." if controls.get("subtitles_enabled") else "Subtitles are off.",
                evidence={"subtitles_enabled": bool(controls.get("subtitles_enabled"))},
            ),
            self._stream_ready_check(
                "safety_review",
                "Safety review",
                "ok" if not controls.get("llm_safety_review_enabled") or settings.speech_filter_llm_enabled else "warn",
                "LLM safety review is available." if settings.speech_filter_llm_enabled else "LLM safety review is not globally enabled; heuristic safety still runs.",
                evidence={
                    "twitch_llm_safety_review_enabled": bool(controls.get("llm_safety_review_enabled")),
                    "speech_filter_llm_enabled": bool(settings.speech_filter_llm_enabled),
                },
            ),
            self._stream_ready_check(
                "irc_runtime",
                "IRC runtime",
                self._worker_runtime_status(irc_process, irc_state, irc_runtime),
                self._worker_runtime_detail("IRC worker", irc_process, irc_state, irc_runtime),
                stale=irc_runtime["stale"],
                evidence=irc_runtime,
            ),
            self._stream_ready_check(
                "irc_posting",
                "IRC posting",
                "warn" if irc_runtime.get("last_post_error") else "ok",
                f"Last IRC post failed: {irc_runtime.get('last_post_error')}" if irc_runtime.get("last_post_error") else "No IRC post error recorded.",
                evidence={"last_post_error": irc_runtime.get("last_post_error", "")},
            ),
            self._stream_ready_check(
                "eventsub_runtime",
                "EventSub runtime",
                self._eventsub_runtime_check_status(eventsub_process, eventsub_state, eventsub_runtime),
                self._eventsub_runtime_check_detail(eventsub_process, eventsub_state, eventsub_runtime),
                stale=eventsub_runtime["stale"],
                evidence={
                    **eventsub_runtime,
                    "subscription_ok_count": int(eventsub_state.get("subscription_ok_count") or 0),
                    "subscription_error_count": int(eventsub_state.get("subscription_error_count") or 0),
                },
            ),
            self._stream_ready_check(
                "eventsub_posting",
                "EventSub posting",
                "warn" if eventsub_runtime.get("last_post_error") else "ok",
                f"Last EventSub post failed: {eventsub_runtime.get('last_post_error')}" if eventsub_runtime.get("last_post_error") else "No EventSub post error recorded.",
                evidence={"last_post_error": eventsub_runtime.get("last_post_error", "")},
            ),
        ]
        blockers = [check for check in checks if check["status"] == "block"]
        warnings = [check for check in checks if check["status"] == "warn"]
        if blockers:
            mode = "not_ready"
        elif not irc_missing and not eventsub_missing and not irc_process.get("running") and not eventsub_process.get("running"):
            mode = "twitch_connect_ready"
        elif irc_process.get("running") and irc_state.get("connected") and (not settings.twitch_eventsub_enabled or eventsub_state.get("connected")) and controls.get("dry_run"):
            mode = "dry_run_connected"
        elif controls.get("dry_run"):
            mode = "offline_replay_ready"
        elif controls.get("voice_enabled"):
            mode = "voice_ready"
        else:
            mode = "offline_replay_ready"
        return {
            "mode": mode,
            "ok": not blockers,
            "next_step": self._stream_ready_next_step(mode, checks),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "checks": checks,
            "last_replay_summary": self._latest_twitch_replay_summary,
            "safety_gate": {
                "enabled": True,
                "review_timeout_seconds": settings.stream_safety_review_timeout_seconds,
                "review_timeout_action": settings.stream_safety_review_timeout_action,
                "llm_review_enabled": bool(settings.speech_filter_llm_enabled),
            },
            "filtered_audio": {
                "configured_path": settings.stream_filtered_audio_path,
                "resolved_path": str(filtered_audio_path) if filtered_audio_path else "",
                "exists": bool(filtered_audio_path and filtered_audio_path.exists()),
            },
        }

    def _stream_ready_check(
        self,
        check_id: str,
        label: str,
        status: str,
        detail: str,
        *,
        stale: bool = False,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": check_id,
            "label": label,
            "status": status if status in {"ok", "warn", "block"} else "warn",
            "detail": detail,
            "stale": stale,
            "evidence": evidence or {},
        }

    def _eventsub_runtime_check_status(self, process: dict[str, Any], state: dict[str, Any], runtime: dict[str, Any]) -> str:
        if not settings.twitch_eventsub_enabled:
            return "warn"
        if not process.get("running"):
            return "warn"
        if runtime.get("stale"):
            return "warn"
        if state.get("subscription_error_count"):
            return "warn"
        return "ok" if state.get("connected") else "warn"

    def _eventsub_runtime_check_detail(self, process: dict[str, Any], state: dict[str, Any], runtime: dict[str, Any]) -> str:
        if not settings.twitch_eventsub_enabled:
            return "EventSub is disabled in config."
        if not process.get("running"):
            return "EventSub worker is not running yet."
        if runtime.get("stale"):
            return f"EventSub worker state is stale at {runtime.get('age_seconds')} seconds old."
        if state.get("subscription_error_count"):
            return f"EventSub connected with {state.get('subscription_error_count')} subscription error(s)."
        if state.get("connected"):
            return "EventSub worker is connected."
        return "EventSub worker is running but not connected yet."

    def _worker_runtime_status(self, process: dict[str, Any], state: dict[str, Any], runtime: dict[str, Any]) -> str:
        if not process.get("running"):
            return "warn"
        if runtime.get("stale"):
            return "warn"
        return "ok" if state.get("connected") else "warn"

    def _worker_runtime_detail(self, label: str, process: dict[str, Any], state: dict[str, Any], runtime: dict[str, Any]) -> str:
        if not process.get("running"):
            return f"{label} is not connected yet."
        if runtime.get("stale"):
            return f"{label} state is stale at {runtime.get('age_seconds')} seconds old."
        if state.get("connected"):
            return f"{label} is connected."
        return f"{label} is running but not connected yet."

    def _worker_runtime_evidence(self, process: dict[str, Any], state: dict[str, Any], *, message_key: str) -> dict[str, Any]:
        age_seconds = self._state_age_seconds(state.get("updated_at"))
        stale = bool(process.get("running") and age_seconds is not None and age_seconds > self.TWITCH_STATE_STALE_SECONDS)
        return {
            "running": bool(process.get("running")),
            "connected": bool(state.get("connected")),
            "status": state.get("status") or "",
            "updated_at": state.get("updated_at") or "",
            "age_seconds": age_seconds,
            "stale": stale,
            "reconnects": int(state.get("reconnects") or 0),
            "last_message_kind": state.get("last_message_kind") or "",
            "last_posted_event_kind": state.get("last_posted_event_kind") or "",
            "last_actor_display_name": state.get("last_actor_display_name") or "",
            "last_message_id": state.get("last_message_id") or "",
            "last_subscription_type": state.get("last_subscription_type") or "",
            "last_post_error": state.get("last_post_error") or "",
            message_key: int(state.get(message_key) or 0),
        }

    def _state_age_seconds(self, value: Any) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))

    def _stream_ready_next_step(self, mode: str, checks: list[dict[str, Any]]) -> str:
        blockers = [check for check in checks if check["status"] == "block"]
        if blockers:
            return f"Fix blocker: {blockers[0]['label']}."
        if mode == "offline_replay_ready":
            return "Run Dry-Run Replay and inspect Stream Activity."
        if mode == "twitch_connect_ready":
            return "Start the IRC and EventSub workers from the Stream tab."
        if mode == "dry_run_connected":
            return "Watch Stream Activity with real Twitch traffic while dry run remains on."
        if mode == "voice_ready":
            return "Voice is enabled; keep monitoring safety, pacing, and Stop Speech before going live."
        return "Review Stream readiness checks."

    def twitch_runtime_settings(self) -> dict[str, Any]:
        config = load_app_file_config()
        return {
            "dry_run": bool(config.get("twitch_dry_run", settings.twitch_dry_run)),
            "voice_enabled": bool(config.get("twitch_voice_enabled", settings.twitch_voice_enabled)),
            "subtitles_enabled": bool(config.get("twitch_subtitles_enabled", settings.twitch_subtitles_enabled)),
            "ambient_chat_enabled": bool(config.get("twitch_ambient_chat_enabled", settings.twitch_ambient_chat_enabled)),
            "mention_replies_enabled": bool(config.get("twitch_mention_replies_enabled", settings.twitch_mention_replies_enabled)),
            "spam_quips_enabled": bool(config.get("twitch_spam_quips_enabled", settings.twitch_spam_quips_enabled)),
            "self_goal_proposals_enabled": bool(
                config.get("twitch_self_goal_proposals_enabled", settings.twitch_self_goal_proposals_enabled)
            ),
            "llm_safety_review_enabled": bool(
                config.get("twitch_llm_safety_review_enabled", settings.twitch_llm_safety_review_enabled)
            ),
            "min_speech_gap_seconds": int(config.get("twitch_min_speech_gap_seconds", settings.twitch_min_speech_gap_seconds)),
            "spam_quip_cooldown_seconds": int(
                config.get("twitch_spam_quip_cooldown_seconds", settings.twitch_spam_quip_cooldown_seconds)
            ),
            "max_speech_seconds_per_minute": int(
                config.get("twitch_max_speech_seconds_per_minute", settings.twitch_max_speech_seconds_per_minute)
            ),
        }

    def save_twitch_runtime_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        bool_keys = [
            "twitch_dry_run",
            "twitch_voice_enabled",
            "twitch_subtitles_enabled",
            "twitch_ambient_chat_enabled",
            "twitch_mention_replies_enabled",
            "twitch_spam_quips_enabled",
            "twitch_self_goal_proposals_enabled",
            "twitch_llm_safety_review_enabled",
        ]
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = existing
        for key in bool_keys:
            short_key = key.removeprefix("twitch_")
            if key in payload:
                updated = self._upsert_toml_bool(updated, key, bool(payload.get(key)))
            elif short_key in payload:
                updated = self._upsert_toml_bool(updated, key, bool(payload.get(short_key)))
        if "twitch_min_speech_gap_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_min_speech_gap_seconds",
                max(0, int(payload.get("twitch_min_speech_gap_seconds", settings.twitch_min_speech_gap_seconds))),
            )
        elif "min_speech_gap_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_min_speech_gap_seconds",
                max(0, int(payload.get("min_speech_gap_seconds", settings.twitch_min_speech_gap_seconds))),
            )
        if "twitch_spam_quip_cooldown_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_spam_quip_cooldown_seconds",
                max(0, int(payload.get("twitch_spam_quip_cooldown_seconds", settings.twitch_spam_quip_cooldown_seconds))),
            )
        elif "spam_quip_cooldown_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_spam_quip_cooldown_seconds",
                max(0, int(payload.get("spam_quip_cooldown_seconds", settings.twitch_spam_quip_cooldown_seconds))),
            )
        if "twitch_max_speech_seconds_per_minute" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_max_speech_seconds_per_minute",
                max(0, int(payload.get("twitch_max_speech_seconds_per_minute", settings.twitch_max_speech_seconds_per_minute))),
            )
        elif "max_speech_seconds_per_minute" in payload:
            updated = self._upsert_toml_number(
                updated,
                "twitch_max_speech_seconds_per_minute",
                max(0, int(payload.get("max_speech_seconds_per_minute", settings.twitch_max_speech_seconds_per_minute))),
            )
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "twitch_runtime_settings_save",
            "status": "ok",
            "detail": "Twitch runtime settings saved. Restart the Twitch worker to apply ingestion-side changes.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return {
            "ok": True,
            "settings": self.twitch_runtime_settings(),
            "detail": "Twitch runtime settings saved. Restart the Twitch worker to apply ingestion-side changes.",
        }

    def _resolve_path(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute():
            path = settings.project_root / path
        return path

    def twitch_viewer_trust(self, *, platform: str = "twitch", limit: int = 100) -> dict[str, Any]:
        return self._shana.get(
            "/v1/stream/viewer-trust",
            params={"platform": platform, "limit": limit},
        )

    def save_twitch_viewer_trust(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._shana.put("/v1/stream/viewer-trust", payload)
        platform = str(payload.get("platform") or "twitch").strip().lower()
        result["items"] = self.twitch_viewer_trust(platform=platform)["items"]
        return result

    def run_twitch_replay(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("jsonl") or "").strip()
        if not text:
            raise ValueError("jsonl is required")
        results = replay_jsonl_text(
            text,
            client=GammaStreamClient(base_url=settings.shana_internal_base_url),
            owner_user_id=settings.twitch_owner_user_id or None,
            synthesize_speech=bool(payload.get("synthesize_speech", False)),
            fast_mode=bool(payload.get("fast_mode", True)),
            session_id=str(payload.get("session_id") or "twitch-replay"),
        )
        summary = self._summarize_twitch_replay(results, scenario="custom")
        self._latest_twitch_replay_summary = summary
        return {"ok": True, "count": len(results), "results": results, "summary": summary}

    def run_twitch_dry_run_replay(self) -> dict[str, Any]:
        results = replay_jsonl_text(
            self.TWITCH_DRY_RUN_SCENARIO,
            client=GammaStreamClient(base_url=settings.shana_internal_base_url),
            owner_user_id=settings.twitch_owner_user_id or None,
            synthesize_speech=False,
            fast_mode=True,
            session_id="twitch-dry-run-readiness",
        )
        summary = self._summarize_twitch_replay(results, scenario="dry_run_readiness")
        self._latest_twitch_replay_summary = summary
        return {
            "ok": True,
            "scenario": "dry_run_readiness",
            "count": len(results),
            "results": results,
            "summary": summary,
        }

    def _summarize_twitch_replay(self, results: list[Any], *, scenario: str) -> dict[str, Any]:
        decisions_by_kind: dict[str, dict[str, int]] = {}
        safety_categories: dict[str, int] = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            input_event = result.get("input_event") if isinstance(result.get("input_event"), dict) else {}
            decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
            kind = str(input_event.get("kind") or "unknown")
            decision_kind = str(decision.get("decision") or "unknown")
            decisions_by_kind.setdefault(kind, {})
            decisions_by_kind[kind][decision_kind] = decisions_by_kind[kind].get(decision_kind, 0) + 1
            metadata = input_event.get("metadata") if isinstance(input_event.get("metadata"), dict) else {}
            input_safety = metadata.get("input_safety") if isinstance(metadata.get("input_safety"), dict) else {}
            category = str(input_safety.get("category") or "")
            if category:
                safety_categories[category] = safety_categories.get(category, 0) + 1
        return {
            "scenario": scenario,
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event_count": len(results),
            "decisions_by_kind": decisions_by_kind,
            "safety_categories": safety_categories,
        }

    def clear_memory(self) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "all"})
        self._latest_provider_action = {
            "action": "memory_clear",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} stored memory rows.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def clear_recent_memory(self, *, minutes: int = 10) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "recent", "minutes": minutes})
        self._latest_provider_action = {
            "action": "memory_clear_recent",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} recent memory rows from the last {result['minutes']} minutes.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def clear_selected_memory(self, selections: list[dict[str, object]]) -> dict[str, Any]:
        result = self._shana.post("/v1/memory/clear", {"scope": "selected", "selections": selections})
        self._latest_provider_action = {
            "action": "memory_clear_selected",
            "status": "ok",
            "detail": f"Cleared {result['cleared_total']} selected memory rows.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            **result,
        }
        return result

    def update_memory_item(self, payload: dict[str, object]) -> dict[str, Any]:
        kind = str(payload.get("kind") or "").strip()
        item_id = int(payload.get("id") or 0)
        if item_id <= 0:
            raise ValueError("valid memory id is required")
        remote_payload = {key: value for key, value in payload.items() if key not in {"kind", "id"}}
        return self._shana.patch(f"/v1/memory/items/{urllib.parse.quote(kind)}/{item_id}", remote_payload)

    def create_memory_item(self, payload: dict[str, object]) -> dict[str, Any]:
        return self._shana.post("/v1/memory/items", dict(payload))

    def save_known_person(self, payload: dict[str, object]) -> dict[str, Any]:
        return self._shana.put("/v1/memory/people", dict(payload))

    def delete_known_person(self, person_id: int) -> dict[str, Any]:
        return self._shana.delete(f"/v1/memory/people/{person_id}")

    def start_tts(self) -> dict[str, Any]:
        provider = self.selected_tts_provider()
        if not self._is_qwen_provider(provider):
            return {"ok": False, "detail": f"TTS start control is only available for Qwen3-TTS, not {provider}."}
        return self._run_provider_action(
            "tts_start",
            self._tts_script_command("start", provider),
            success_detail="Qwen3-TTS start requested.",
        )

    def stop_tts(self) -> dict[str, Any]:
        provider = self.selected_tts_provider()
        if not self._is_qwen_provider(provider):
            return {"ok": False, "detail": f"TTS stop control is only available for Qwen3-TTS, not {provider}."}
        return self._run_provider_action(
            "tts_stop",
            self._tts_script_command("stop", provider),
            success_detail="Qwen3-TTS stop requested.",
        )

    @staticmethod
    def _is_qwen_provider(provider: str) -> bool:
        return provider.strip().lower() in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}

    @classmethod
    def _canonical_tts_provider(cls, provider: str) -> str:
        normalized = provider.strip().lower()
        return "qwen-tts" if cls._is_qwen_provider(normalized) else normalized

    def selected_tts_provider(self) -> str:
        provider = self._canonical_tts_provider(load_desired_tts_selection().get("tts_provider", ""))
        if provider in {"qwen-tts", "piper", "openai"}:
            return provider
        runtime_provider = self._canonical_tts_provider(settings.tts_provider)
        return runtime_provider if runtime_provider in {"qwen-tts", "piper", "openai"} else provider

    def selected_tts_profile(self) -> str | None:
        value = load_desired_tts_selection().get("tts_profile", "")
        if value:
            return value
        return settings.tts_profile or None

    def set_tts_provider(self, provider: str) -> dict[str, Any]:
        normalized = provider.strip().lower()
        allowed = {"piper", "qwen-tts", "openai"}
        if normalized not in allowed:
            raise ValueError(f"unsupported tts provider: {provider}")
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = self._upsert_toml_string(existing, "tts_provider", normalized)
        updated = self._upsert_toml_string(updated, "tts_profile", "")
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "tts_provider_select",
            "status": "ok",
            "detail": f"TTS provider set to {normalized}. Saved voice profile cleared. Restart Shana to use it for conversation responses.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": normalized,
        }
        return {
            "ok": True,
            "provider": normalized,
            "detail": "TTS provider saved. Saved voice profile cleared. Restart Shana to use it for normal conversations.",
        }

    def set_tts_profile(self, profile_id: str) -> dict[str, Any]:
        if not profile_id:
            app_toml = app_local_config_path()
            existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
            updated = self._upsert_toml_string(existing, "tts_profile", "")
            app_toml.parent.mkdir(parents=True, exist_ok=True)
            app_toml.write_text(updated, encoding="utf-8")
            self._latest_provider_action = {
                "action": "tts_profile_select",
                "status": "ok",
                "detail": "TTS profile cleared. Restart Shana to use base provider settings for conversation responses.",
                "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "provider": self.selected_tts_provider(),
                "profile": "",
            }
            return {
                "ok": True,
                "provider": self.selected_tts_provider(),
                "profile": "",
                "detail": "TTS profile cleared. Restart Shana to use base provider settings for normal conversations.",
            }
        profile = get_voice_profile(profile_id)
        if profile is None:
            raise ValueError(f"unsupported tts profile: {profile_id}")
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = self._upsert_toml_string(existing, "tts_profile", profile.profile_id)
        updated = self._upsert_toml_string(updated, "tts_provider", profile.provider)
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "tts_profile_select",
            "status": "ok",
            "detail": f"TTS profile set to {profile.label}. Restart Shana to use it for conversation responses.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": profile.provider,
            "profile": profile.profile_id,
        }
        return {
            "ok": True,
            "provider": profile.provider,
            "profile": profile.profile_id,
            "detail": "TTS profile saved. Restart Shana to use it for normal conversations.",
        }

    def save_tts_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(payload.get("id", "")).strip()
        profile = save_voice_profile(
            profile_id,
            {
                "label": payload.get("label", ""),
                "provider": payload.get("provider", ""),
                "description": payload.get("description", ""),
                "values": payload.get("values", {}),
            },
        )
        self._latest_provider_action = {
            "action": "tts_profile_save",
            "status": "ok",
            "detail": f"TTS profile saved: {profile.label}.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": profile.provider,
            "profile": profile.profile_id,
        }
        return {
            "ok": True,
            "profile": profile.as_payload(),
            "detail": "TTS profile saved.",
        }

    def tts_profile_editor_state(self, profile_id: str | None, provider: str | None) -> dict[str, Any]:
        profile = get_voice_profile(profile_id)
        if profile is not None:
            return profile.as_payload()
        template = profile_template(provider or self.selected_tts_provider())
        return {
            "id": "",
            "label": template.get("label", ""),
            "provider": template.get("provider", provider or self.selected_tts_provider()),
            "description": template.get("description", ""),
            "values": template.get("values", {}),
        }

    def test_stt(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        return self._run_provider_action(
            "stt_test",
            self._python_module_command("gamma.run_stt_test", str(sample)),
            success_detail="STT smoke test completed.",
        )

    def test_tts(self) -> dict[str, Any]:
        selected_provider = self.selected_tts_provider()
        selected_profile = self.selected_tts_profile()
        env = {"SHANA_TTS_PROVIDER": selected_provider}
        if selected_profile:
            env["SHANA_TTS_PROFILE"] = selected_profile
        if self._is_qwen_provider(selected_provider):
            ready = self._wait_for_qwen_tts_ready(selected_profile, timeout_seconds=90)
            if not ready.get("ok"):
                self._latest_provider_action = {
                    "action": "tts_test",
                    "status": "error",
                    "detail": "tts_test failed",
                    "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "stdout": "",
                    "stderr": ready.get("detail", "Qwen3-TTS is not ready."),
                    "duration_ms": 0.0,
                }
                return self._latest_provider_action
        return self._run_provider_action(
            "tts_test",
            self._python_module_command("gamma.run_tts_test", "Dashboard TTS smoke test."),
            env_overrides=env,
            success_detail="TTS smoke test completed.",
        )

    def synthesize_text(self, text: str) -> dict[str, Any]:
        """Synthesize *text* (multi-chunk if needed) via subprocess; return audio filename."""
        import tempfile
        selected_provider = self.selected_tts_provider()
        selected_profile = self.selected_tts_profile()
        env = {"SHANA_TTS_PROVIDER": selected_provider}
        if selected_profile:
            env["SHANA_TTS_PROFILE"] = selected_profile
        tmppath: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", encoding="utf-8", delete=False
            ) as f:
                f.write(text)
                tmppath = f.name
            completed = self._run_command(
                self._python_module_command("gamma.run_tts_test", "--file", tmppath, "--json"),
                timeout=300,
                env_overrides=env,
            )
            output = (completed.stdout or "").strip()
            payload = json.loads(output)
            audio_path = payload.get("audio_path", "")
            filename = Path(audio_path).name if audio_path else ""
            return {
                "ok": True,
                "filename": filename,
                "provider": payload.get("provider"),
                "timings_ms": payload.get("timings_ms") or {},
            }
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return {"ok": False, "detail": stderr or "synthesis failed"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "detail": "synthesis timed out"}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"ok": False, "detail": "synthesis returned unexpected output"}
        finally:
            if tmppath:
                try:
                    Path(tmppath).unlink()
                except Exception:
                    pass

    def _upsert_toml_string(self, existing: str, key: str, value: str) -> str:
        pattern = rf'(?m)^\s*{re.escape(key)}\s*=\s*"([^"]*)"\s*$'
        if re.search(pattern, existing):
            return re.sub(pattern, f'{key} = "{value}"', existing, count=1)
        updated = existing.rstrip()
        if updated:
            updated += "\n"
        updated += f'{key} = "{value}"\n'
        return updated

    def _upsert_toml_bool(self, existing: str, key: str, value: bool) -> str:
        pattern = rf'(?m)^\s*{re.escape(key)}\s*=\s*(true|false)\s*$'
        replacement = f'{key} = {"true" if value else "false"}'
        if re.search(pattern, existing):
            return re.sub(pattern, replacement, existing, count=1)
        updated = existing.rstrip()
        if updated:
            updated += "\n"
        updated += replacement + "\n"
        return updated

    def _upsert_toml_number(self, existing: str, key: str, value: int | float) -> str:
        pattern = rf'(?m)^\s*{re.escape(key)}\s*=\s*([0-9.]+)\s*$'
        replacement = f"{key} = {value}"
        if re.search(pattern, existing):
            return re.sub(pattern, replacement, existing, count=1)
        updated = existing.rstrip()
        if updated:
            updated += "\n"
        updated += replacement + "\n"
        return updated

    def assistant_runtime_settings(self) -> dict[str, Any]:
        config = load_app_file_config()
        return {
            "speech_filter_level": str(config.get("speech_filter_level", settings.speech_filter_level)),
            "speech_filter_hard_block_enabled": bool(config.get("speech_filter_hard_block_enabled", settings.speech_filter_hard_block_enabled)),
            "speech_filter_heuristic_enabled": bool(config.get("speech_filter_heuristic_enabled", settings.speech_filter_heuristic_enabled)),
            "speech_filter_llm_enabled": bool(config.get("speech_filter_llm_enabled", settings.speech_filter_llm_enabled)),
            "speech_filter_llm_model": str(config.get("speech_filter_llm_model", settings.speech_filter_llm_model)),
            "speech_filter_llm_temperature": float(
                config.get("speech_filter_llm_temperature", settings.speech_filter_llm_temperature)
            ),
            "speech_filter_auto_rewrite": bool(config.get("speech_filter_auto_rewrite", settings.speech_filter_auto_rewrite)),
            "stream_safety_review_timeout_seconds": float(
                config.get("stream_safety_review_timeout_seconds", settings.stream_safety_review_timeout_seconds)
            ),
            "stream_safety_review_timeout_action": str(
                config.get("stream_safety_review_timeout_action", settings.stream_safety_review_timeout_action)
            ),
            "llm_router_profile": str(config.get("llm_router_profile", settings.llm_router_profile)),
            "llm_router_allow_hosted_escalation": bool(
                config.get("llm_router_allow_hosted_escalation", settings.llm_router_allow_hosted_escalation)
            ),
            "llm_router_chat_light_max_input_words": int(
                config.get("llm_router_chat_light_max_input_words", settings.llm_router_chat_light_max_input_words)
            ),
            "llm_router_complex_max_input_words": int(
                config.get("llm_router_complex_max_input_words", settings.llm_router_complex_max_input_words)
            ),
            "llm_router_persona_hosted_fallback_enabled": bool(
                config.get(
                    "llm_router_persona_hosted_fallback_enabled",
                    settings.llm_router_persona_hosted_fallback_enabled,
                )
            ),
            "llm_router_persona_heavy_hosted_fallback_enabled": bool(
                config.get(
                    "llm_router_persona_heavy_hosted_fallback_enabled",
                    settings.llm_router_persona_heavy_hosted_fallback_enabled,
                )
            ),
            "assistant_state_enabled": bool(config.get("assistant_state_enabled", settings.assistant_state_enabled)),
            "assistant_emotion_decay_turns": int(config.get("assistant_emotion_decay_turns", settings.assistant_emotion_decay_turns)),
            "assistant_emotion_episode_threshold": float(config.get("assistant_emotion_episode_threshold", settings.assistant_emotion_episode_threshold)),
            "assistant_emotion_pattern_threshold": int(config.get("assistant_emotion_pattern_threshold", settings.assistant_emotion_pattern_threshold)),
            "proactive_idle_enabled": bool(config.get("proactive_idle_enabled", settings.proactive_idle_enabled)),
            "proactive_idle_min_silence_seconds": int(
                config.get("proactive_idle_min_silence_seconds", settings.proactive_idle_min_silence_seconds)
            ),
            "proactive_idle_target_silence_seconds": int(
                config.get("proactive_idle_target_silence_seconds", settings.proactive_idle_target_silence_seconds)
            ),
            "proactive_idle_cooldown_seconds": int(
                config.get("proactive_idle_cooldown_seconds", settings.proactive_idle_cooldown_seconds)
            ),
            "proactive_idle_max_attempts_per_topic": int(
                config.get("proactive_idle_max_attempts_per_topic", settings.proactive_idle_max_attempts_per_topic)
            ),
            "proactive_idle_tick_seconds": int(config.get("proactive_idle_tick_seconds", settings.proactive_idle_tick_seconds)),
            "proactive_idle_speech_enabled": bool(
                config.get("proactive_idle_speech_enabled", settings.proactive_idle_speech_enabled)
            ),
        }

    def save_assistant_runtime_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        app_toml = app_local_config_path()
        existing = app_toml.read_text(encoding="utf-8") if app_toml.exists() else ""
        updated = existing
        if "speech_filter_level" in payload:
            level = str(payload.get("speech_filter_level", settings.speech_filter_level)).strip().lower()
            if level not in {"strict", "light", "none"}:
                raise ValueError("unsupported speech_filter_level")
            updated = self._upsert_toml_string(updated, "speech_filter_level", level)
        bool_keys = [
            "speech_filter_hard_block_enabled",
            "speech_filter_heuristic_enabled",
            "speech_filter_llm_enabled",
            "speech_filter_auto_rewrite",
            "llm_router_allow_hosted_escalation",
            "llm_router_persona_hosted_fallback_enabled",
            "llm_router_persona_heavy_hosted_fallback_enabled",
            "assistant_state_enabled",
            "proactive_idle_enabled",
            "proactive_idle_speech_enabled",
        ]
        for key in bool_keys:
            if key in payload:
                updated = self._upsert_toml_bool(updated, key, bool(payload.get(key)))
        if "speech_filter_llm_model" in payload:
            updated = self._upsert_toml_string(updated, "speech_filter_llm_model", str(payload.get("speech_filter_llm_model", "")).strip())
        if "speech_filter_llm_temperature" in payload:
            temperature = max(0.0, min(1.0, float(payload.get("speech_filter_llm_temperature", settings.speech_filter_llm_temperature))))
            updated = self._upsert_toml_number(updated, "speech_filter_llm_temperature", temperature)
        if "stream_safety_review_timeout_seconds" in payload:
            timeout_seconds = max(0.05, float(payload.get("stream_safety_review_timeout_seconds", settings.stream_safety_review_timeout_seconds)))
            updated = self._upsert_toml_number(updated, "stream_safety_review_timeout_seconds", timeout_seconds)
        if "stream_safety_review_timeout_action" in payload:
            timeout_action = str(payload.get("stream_safety_review_timeout_action", settings.stream_safety_review_timeout_action)).strip().lower()
            if timeout_action not in {"skip", "defer", "hold"}:
                raise ValueError("unsupported stream_safety_review_timeout_action")
            updated = self._upsert_toml_string(updated, "stream_safety_review_timeout_action", timeout_action)
        if "llm_router_profile" in payload:
            profile = str(payload.get("llm_router_profile", settings.llm_router_profile)).strip().lower()
            if profile not in {"balanced", "local_only", "low_latency_voice", "high_quality", "offline_safe"}:
                raise ValueError("unsupported llm_router_profile")
            updated = self._upsert_toml_string(updated, "llm_router_profile", profile)
        if "llm_router_chat_light_max_input_words" in payload:
            updated = self._upsert_toml_number(
                updated,
                "llm_router_chat_light_max_input_words",
                max(1, int(payload.get("llm_router_chat_light_max_input_words", settings.llm_router_chat_light_max_input_words))),
            )
        if "llm_router_complex_max_input_words" in payload:
            updated = self._upsert_toml_number(
                updated,
                "llm_router_complex_max_input_words",
                max(1, int(payload.get("llm_router_complex_max_input_words", settings.llm_router_complex_max_input_words))),
            )
        if "assistant_emotion_decay_turns" in payload:
            updated = self._upsert_toml_number(updated, "assistant_emotion_decay_turns", max(0, int(payload.get("assistant_emotion_decay_turns", 0))))
        if "assistant_emotion_episode_threshold" in payload:
            threshold = max(0.0, min(1.0, float(payload.get("assistant_emotion_episode_threshold", 0.65))))
            updated = self._upsert_toml_number(updated, "assistant_emotion_episode_threshold", threshold)
        if "assistant_emotion_pattern_threshold" in payload:
            updated = self._upsert_toml_number(updated, "assistant_emotion_pattern_threshold", max(1, int(payload.get("assistant_emotion_pattern_threshold", 1))))
        if "proactive_idle_min_silence_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "proactive_idle_min_silence_seconds",
                max(5, int(payload.get("proactive_idle_min_silence_seconds", settings.proactive_idle_min_silence_seconds))),
            )
        if "proactive_idle_target_silence_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "proactive_idle_target_silence_seconds",
                max(10, int(payload.get("proactive_idle_target_silence_seconds", settings.proactive_idle_target_silence_seconds))),
            )
        if "proactive_idle_cooldown_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "proactive_idle_cooldown_seconds",
                max(30, int(payload.get("proactive_idle_cooldown_seconds", settings.proactive_idle_cooldown_seconds))),
            )
        if "proactive_idle_max_attempts_per_topic" in payload:
            updated = self._upsert_toml_number(
                updated,
                "proactive_idle_max_attempts_per_topic",
                max(1, int(payload.get("proactive_idle_max_attempts_per_topic", settings.proactive_idle_max_attempts_per_topic))),
            )
        if "proactive_idle_tick_seconds" in payload:
            updated = self._upsert_toml_number(
                updated,
                "proactive_idle_tick_seconds",
                max(1, int(payload.get("proactive_idle_tick_seconds", settings.proactive_idle_tick_seconds))),
            )
        app_toml.parent.mkdir(parents=True, exist_ok=True)
        app_toml.write_text(updated, encoding="utf-8")
        self._latest_provider_action = {
            "action": "assistant_runtime_settings_save",
            "status": "ok",
            "detail": "Assistant runtime settings saved. Restart Shana to apply them to the backend process.",
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return {
            "ok": True,
            "settings": self.assistant_runtime_settings(),
            "detail": "Assistant runtime settings saved. Restart Shana to apply them.",
        }

    def test_llm(self) -> dict[str, Any]:
        return self._run_provider_action(
            "llm_test",
            self._python_module_command("gamma.run_llm_test", "Dashboard LLM smoke test."),
            success_detail="LLM smoke test completed.",
        )

    def test_voice_roundtrip(self) -> dict[str, Any]:
        sample = self._sample_audio_path()
        selected_provider = self.selected_tts_provider()
        if self._is_qwen_provider(selected_provider):
            ready = self._wait_for_qwen_tts_ready(self.selected_tts_profile(), timeout_seconds=90)
            if not ready.get("ok"):
                self._latest_provider_action = {
                    "action": "voice_roundtrip_test",
                    "status": "error",
                    "detail": "voice_roundtrip_test failed",
                    "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "stdout": "",
                    "stderr": ready.get("detail", "Qwen3-TTS is not ready."),
                    "duration_ms": 0.0,
                }
                return self._latest_provider_action
        return self._run_provider_action(
            "voice_roundtrip_test",
            self._python_module_command("gamma.run_voice_roundtrip", str(sample)),
            timeout=180,
            success_detail="Full voice loop completed.",
        )

    def append_client_log(self, payload: dict[str, Any]) -> None:
        runtime_dir = settings.data_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / "dashboard.client.log"
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        line = json.dumps({"timestamp": timestamp, **payload}, ensure_ascii=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def run_remote_live_voice_turn(
        self,
        *,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str = "simple_chunked",
    ) -> dict[str, Any]:
        return self.start_remote_live_job(
            pcm_bytes=pcm_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            turn_id=None,
        )

    def run_remote_voice_roundtrip(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        session_id: str | None,
        synthesize_speech: bool,
    ) -> VoiceRoundtripResponse:
        data: dict[str, Any] = {
            "synthesize_speech": "true" if synthesize_speech else "false",
        }
        if session_id:
            data["session_id"] = session_id
        payload = self._shana.post_multipart(
            "/v1/voice/roundtrip",
            data=data,
            field_name="audio_file",
            filename=filename,
            content=audio_bytes,
            content_type=content_type,
        )
        return VoiceRoundtripResponse.model_validate(payload)

    def start_remote_live_job(
        self,
        *,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str,
        turn_id: str | None,
    ) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/live/start",
            pcm_bytes=pcm_bytes,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            turn_id=turn_id,
        )

    def transcribe_remote_live_audio(self, *, pcm_bytes: bytes) -> dict[str, Any]:
        return self._post_live_audio(
            path="/v1/voice/transcribe",
            pcm_bytes=pcm_bytes,
            session_id=None,
            synthesize_speech=None,
            response_mode=None,
            turn_id=None,
        )

    def get_remote_live_job(self, turn_id: str) -> dict[str, Any]:
        return self._shana.get(f"/v1/voice/live/{turn_id}")

    def cancel_remote_live_job(self, turn_id: str, *, reason: str = "interrupted") -> dict[str, Any]:
        return self._shana.request_json(
            "POST",
            f"/v1/voice/live/{turn_id}/cancel",
            data={"reason": reason},
            timeout=30,
        )

    def remote_live_history(self, *, limit: int = 20) -> dict[str, Any]:
        return self._shana.get("/v1/voice/live/history", params={"limit": max(1, min(limit, 100))})

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

    def live_idle_settings(self) -> dict[str, Any]:
        return self.assistant_runtime_settings()

    def record_remote_stream_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return self._post_remote_json("/v1/stream/events", event)

    def _post_live_audio(
        self,
        *,
        path: str,
        pcm_bytes: bytes,
        session_id: str | None,
        synthesize_speech: bool | None,
        response_mode: str | None,
        turn_id: str | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if session_id:
            data["session_id"] = session_id
        if synthesize_speech is not None:
            data["synthesize_speech"] = "true" if synthesize_speech else "false"
        if response_mode:
            data["response_mode"] = response_mode
        if turn_id:
            data["turn_id"] = turn_id
        return self._shana.post_multipart(
            path,
            data=data,
            field_name="audio_file",
            filename="live-browser.wav",
            content=self._pcm_to_wav_bytes(pcm_bytes),
            content_type="audio/wav",
        )

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

    def _pcm_to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(pcm_bytes)
        return buffer.getvalue()

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

    def _run_provider_action(
        self,
        action_name: str,
        command: list[str],
        *,
        timeout: int = 60,
        env_overrides: dict[str, str] | None = None,
        success_detail: str,
    ) -> dict[str, Any]:
        payload = {
            "action": action_name,
            "command": command,
            "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        started_at = time.perf_counter()
        try:
            completed = self._run_command(command, timeout=timeout, env_overrides=env_overrides)
            payload.update(
                {
                    "status": "ok",
                    "detail": success_detail,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            )
        except subprocess.CalledProcessError as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} failed",
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                }
            )
        except subprocess.TimeoutExpired as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": f"{action_name} timed out",
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                }
            )
        except Exception as exc:
            payload.update(
                {
                    "status": "error",
                    "detail": str(exc),
                    "stdout": "",
                    "stderr": "",
                }
            )
        self._latest_provider_action = payload
        payload["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload

    def _run_command(
        self,
        command: list[str],
        *,
        timeout: int,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = prepend_cuda_library_path(os.environ.copy())
        if env_overrides:
            env.update(env_overrides)
        run_kwargs: dict[str, Any] = {
            "cwd": settings.project_root,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "check": True,
            "env": env,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(command, **run_kwargs)

    def _wait_for_qwen_tts_ready(self, profile_id: str | None, *, timeout_seconds: int) -> dict[str, Any]:
        profile = get_voice_profile(profile_id)
        values = profile.values if profile and isinstance(profile.values, dict) else {}
        endpoint = str(values.get("qwen_tts_endpoint", "")).strip()
        if not endpoint:
            return {"ok": False, "detail": "No Qwen TTS endpoint is configured for the selected profile."}
        base_url = endpoint.rsplit("/tts", 1)[0]
        deadline = time.time() + max(timeout_seconds, 1)
        last_error = "Qwen3-TTS readiness check did not reach the server."
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base_url + "/health", timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if 200 <= response.status < 300 and payload.get("status") == "ok":
                    return {"ok": True, "detail": "ready"}
                last_error = f"Unexpected Qwen3-TTS health response: HTTP {response.status}"
            except urllib.error.HTTPError as exc:
                last_error = f"Qwen3-TTS health check failed: HTTP {exc.code}"
            except Exception as exc:
                last_error = f"Qwen3-TTS health check failed: {exc}"
            time.sleep(1)
        return {"ok": False, "detail": last_error}

    def _sample_audio_path(self) -> Path:
        sample = settings.project_root / "test_audio" / "jfk.flac"
        if not sample.exists():
            raise FileNotFoundError(f"sample audio not found: {sample}")
        return sample

    def _python_module_command(self, module: str, *args: str) -> list[str]:
        return [self._process_manager.resolve_foreground_python(), "-m", module, *args]

    def _stop_all_tts_servers(self) -> dict[str, Any]:
        return {
            "qwen_tts": self._run_stop_tts_command("qwen-tts", "Qwen3-TTS"),
        }

    def _run_stop_tts_command(self, provider: str, label: str) -> dict[str, Any]:
        command = self._tts_script_command("stop", provider)
        started_at = time.perf_counter()
        payload: dict[str, Any] = {
            "provider": provider,
            "label": label,
            "command": command,
        }
        try:
            completed = self._run_command(command, timeout=60)
            payload.update(
                {
                    "ok": True,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                }
            )
        except subprocess.CalledProcessError as exc:
            payload.update(
                {
                    "ok": False,
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                    "detail": f"{label} stop failed",
                }
            )
        except subprocess.TimeoutExpired as exc:
            payload.update(
                {
                    "ok": False,
                    "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                    "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
                    "detail": f"{label} stop timed out",
                }
            )
        except Exception as exc:
            payload.update(
                {
                    "ok": False,
                    "stdout": "",
                    "stderr": "",
                    "detail": str(exc),
                }
            )
        payload["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return payload

    def _tts_script_command(self, action: str, provider: str | None = None) -> list[str]:
        scripts_dir = settings.project_root / "scripts"
        verb = "start" if action == "start" else "stop"
        if self._is_qwen_provider(provider or ""):
            script = scripts_dir / f"{verb}_qwen_tts_server.py"
            return [self._process_manager.resolve_foreground_python(), str(script)]
        raise ValueError(f"no managed TTS sidecar for provider: {provider}")

    def _post_remote_json(self, path: str, payload: dict[str, Any], *, timeout: float = 10) -> dict[str, Any]:
        return self._shana.post(path, payload, timeout=timeout)

    def _machine_status(self) -> dict[str, Any]:
        return self._resource_monitor.dashboard_payload()

    def _tail(self, path: Path, *, limit: int = 60) -> str:
        if not path.exists():
            return ""
        lines: deque[str] = deque(maxlen=max(1, limit))
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
        return "\n".join(lines)

    def _recent_timings(self, limit: int = 12) -> dict[str, Any]:
        log_path = settings.data_dir / "runtime" / "conversation.timings.jsonl"
        if not log_path.exists():
            return {"entries": [], "summary": {"count": 0}}
        entries_deque: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries_deque.append(payload)
        entries = list(entries_deque)
        totals = [entry.get("timing_ms", {}).get("total_ms") for entry in entries if isinstance(entry.get("timing_ms", {}).get("total_ms"), (int, float))]
        summary = {
            "count": len(entries),
            "avg_total_ms": round(sum(totals) / len(totals), 1) if totals else None,
            "max_total_ms": round(max(totals), 1) if totals else None,
            "min_total_ms": round(min(totals), 1) if totals else None,
        }
        return {"entries": entries, "summary": summary}

    def _recent_llm_routes(self, limit: int = 24) -> dict[str, Any]:
        log_path = settings.data_dir / "runtime" / "llm.routes.jsonl"
        if not log_path.exists():
            return {
                "entries": [],
                "summary": {"count": 0, "status_counts": {}, "provider_counts": {}, "route_family_counts": {}},
                "placement_shadow": self._placement_shadow_routes([]),
            }
        entries_deque: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries_deque.append(payload)
        entries = list(entries_deque)
        status_counts: dict[str, int] = {}
        provider_counts: dict[str, int] = {}
        route_family_counts: dict[str, int] = {}
        durations: list[float] = []
        for entry in entries:
            status = str(entry.get("status", "") or "unknown")
            provider = str(entry.get("provider", "") or "unknown")
            route_family = str(entry.get("route_family", "") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            route_family_counts[route_family] = route_family_counts.get(route_family, 0) + 1
            duration = entry.get("duration_ms")
            if isinstance(duration, (int, float)):
                durations.append(float(duration))
        summary = {
            "count": len(entries),
            "status_counts": status_counts,
            "provider_counts": provider_counts,
            "route_family_counts": route_family_counts,
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        }
        return {
            "entries": entries,
            "summary": summary,
            "placement_shadow": self._placement_shadow_routes(entries),
        }

    def _recent_startup_admission(self, limit: int = 12) -> dict[str, Any]:
        log_path = settings.data_dir / "runtime" / "logs" / "supervisor.jsonl"
        if not log_path.exists():
            return {"entries": [], "summary": {"count": 0, "event_counts": {}, "target_counts": {}}}
        events: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                event = str(payload.get("event") or "")
                if not event.startswith("resource.startup_admission."):
                    continue
                events.append(self._format_startup_admission_entry(payload))
        entries = list(events)
        event_counts: dict[str, int] = {}
        target_counts: dict[str, int] = {}
        for entry in entries:
            event = str(entry.get("event") or "unknown")
            event_counts[event] = event_counts.get(event, 0) + 1
            target_id = entry.get("target_id")
            if target_id:
                target_key = str(target_id)
                target_counts[target_key] = target_counts.get(target_key, 0) + 1
        return {
            "entries": entries,
            "summary": {
                "count": len(entries),
                "selected_count": event_counts.get("resource.startup_admission.selected", 0),
                "rejected_count": event_counts.get("resource.startup_admission.rejected", 0),
                "skipped_count": event_counts.get("resource.startup_admission.skipped", 0),
                "bypassed_count": event_counts.get("resource.startup_admission.bypassed", 0),
                "event_counts": event_counts,
                "target_counts": target_counts,
            },
        }

    def _format_startup_admission_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = payload.get("selected")
        selected_payload = selected if isinstance(selected, dict) else {}
        rejected = payload.get("rejected")
        rejected_payload = rejected if isinstance(rejected, dict) else {}
        return {
            "timestamp": payload.get("timestamp"),
            "level": payload.get("level"),
            "event": payload.get("event"),
            "message": payload.get("message"),
            "provider": payload.get("provider"),
            "kind": payload.get("kind"),
            "modality": payload.get("modality"),
            "model": payload.get("model"),
            "workload_id": payload.get("workload_id"),
            "status": payload.get("status"),
            "requested_device": payload.get("requested_device"),
            "estimated_vram_mb": payload.get("estimated_vram_mb"),
            "estimate_source": payload.get("estimate_source"),
            "estimate_observed_age_seconds": payload.get("estimate_observed_age_seconds"),
            "estimate_ttl_seconds": payload.get("estimate_ttl_seconds"),
            "minimum_headroom_mb": payload.get("minimum_headroom_mb"),
            "snapshot_age_seconds": payload.get("snapshot_age_seconds"),
            "target_id": selected_payload.get("target_id"),
            "endpoint_ref": selected_payload.get("endpoint_ref"),
            "device": selected_payload.get("device"),
            "gpu_index": selected_payload.get("gpu_index"),
            "gpu_uuid": selected_payload.get("gpu_uuid"),
            "free_vram_mb": selected_payload.get("free_vram_mb"),
            "projected_headroom_mb": selected_payload.get("projected_headroom_mb"),
            "reason": selected_payload.get("reason"),
            "score": selected_payload.get("score"),
            "rejected_count": len(rejected_payload),
            "rejected": rejected_payload,
            "validation_errors": payload.get("validation_errors") if isinstance(payload.get("validation_errors"), list) else [],
        }

    def _recent_sidecar_allocations(self, limit: int = 12) -> dict[str, Any]:
        log_path = settings.data_dir / "runtime" / "logs" / "supervisor.jsonl"
        ttl_seconds = load_resource_routing_registry().policy.sidecar_allocation_ttl_seconds
        if not log_path.exists():
            return {
                "entries": [],
                "summary": {
                    "count": 0,
                    "current_count": 0,
                    "fresh_count": 0,
                    "stale_count": 0,
                    "ttl_seconds": ttl_seconds,
                    "provider_counts": {},
                    "observed_vram_mb": 0,
                    "estimated_vram_mb": 0,
                    "allocation_delta_mb": 0,
                },
            }
        entries_list = [
            allocation.as_payload()
            for allocation in recent_sidecar_allocation_entries(
                log_path,
                ttl_seconds=ttl_seconds,
                limit=limit,
            )
        ]
        provider_counts: dict[str, int] = {}
        latest_by_sidecar: dict[str, dict[str, Any]] = {}
        for entry in entries_list:
            provider = str(entry.get("provider") or "unknown")
            kind = str(entry.get("kind") or "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            latest_by_sidecar[f"{provider}:{kind}"] = entry
        fresh_latest = [entry for entry in latest_by_sidecar.values() if not entry.get("stale")]
        observed_total = sum(int(entry.get("observed_vram_mb") or 0) for entry in fresh_latest)
        estimated_total = sum(int(entry.get("estimated_vram_mb") or 0) for entry in fresh_latest)
        stale_count = sum(1 for entry in entries_list if entry.get("stale"))
        return {
            "entries": entries_list,
            "summary": {
                "count": len(entries_list),
                "current_count": len(fresh_latest),
                "fresh_count": len(entries_list) - stale_count,
                "stale_count": stale_count,
                "ttl_seconds": ttl_seconds,
                "provider_counts": provider_counts,
                "observed_vram_mb": observed_total,
                "estimated_vram_mb": estimated_total,
                "allocation_delta_mb": observed_total - estimated_total,
            },
        }

    def _placement_shadow_routes(self, entries: list[dict[str, Any]], *, limit: int = 8) -> dict[str, Any]:
        shadow_entries: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
        for entry in entries:
            shadow = entry.get("placement_shadow")
            if not isinstance(shadow, dict):
                continue
            flattened = self._format_placement_shadow_entry(entry, shadow)
            if flattened is None:
                continue
            shadow_entries.append(flattened)
        entries_list = list(shadow_entries)
        status_counts: dict[str, int] = {}
        target_counts: dict[str, int] = {}
        for shadow_entry in entries_list:
            shadow_status = str(shadow_entry.get("shadow_status") or "unknown")
            status_counts[shadow_status] = status_counts.get(shadow_status, 0) + 1
            target_id = shadow_entry.get("target_id")
            if target_id:
                target_key = str(target_id)
                target_counts[target_key] = target_counts.get(target_key, 0) + 1
        summary = {
            "count": len(entries_list),
            "selected_count": status_counts.get("selected", 0),
            "no_fit_count": status_counts.get("no_fit", 0),
            "snapshot_stale_count": status_counts.get("snapshot_stale", 0),
            "status_counts": status_counts,
            "target_counts": target_counts,
        }
        return {"entries": entries_list, "summary": summary}

    def _format_placement_shadow_entry(self, entry: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any] | None:
        shadow_status = str(shadow.get("status") or "").strip()
        if not shadow_status:
            return None
        selected = shadow.get("selected")
        selected_payload = selected if isinstance(selected, dict) else {}
        rejected = shadow.get("rejected")
        rejected_payload = rejected if isinstance(rejected, dict) else {}
        comparison = entry.get("placement_shadow_comparison")
        comparison_payload = comparison if isinstance(comparison, dict) else {}
        return {
            "timestamp": entry.get("timestamp"),
            "purpose": entry.get("purpose"),
            "route_family": entry.get("route_family"),
            "provider": entry.get("provider"),
            "model": entry.get("model"),
            "status": entry.get("status"),
            "shadow_status": shadow_status,
            "snapshot_age_seconds": shadow.get("snapshot_age_seconds"),
            "reservation_id": shadow.get("reservation_id"),
            "reservation_expires_at": shadow.get("reservation_expires_at"),
            "reservation_ttl_seconds": shadow.get("reservation_ttl_seconds"),
            "comparison": comparison_payload,
            "target_id": selected_payload.get("target_id"),
            "target_provider": selected_payload.get("provider"),
            "target_kind": selected_payload.get("kind"),
            "endpoint_ref": selected_payload.get("endpoint_ref"),
            "device": selected_payload.get("device"),
            "gpu_index": selected_payload.get("gpu_index"),
            "gpu_uuid": selected_payload.get("gpu_uuid"),
            "free_vram_mb": selected_payload.get("free_vram_mb"),
            "projected_headroom_mb": selected_payload.get("projected_headroom_mb"),
            "advisory_reserved_vram_mb": selected_payload.get("advisory_reserved_vram_mb"),
            "warm": selected_payload.get("warm"),
            "reason": selected_payload.get("reason"),
            "score": selected_payload.get("score"),
            "rejected_count": len(rejected_payload),
            "rejected": rejected_payload,
        }

    def _format_router_backoff_entries(self, backoff_state: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for key, seconds in backoff_state.items():
            normalized_key = str(key or "").strip()
            provider = normalized_key
            scope = "text"
            if ":" in normalized_key:
                provider, scope = normalized_key.split(":", 1)
            entries.append(
                {
                    "key": normalized_key,
                    "provider": provider,
                    "scope": scope,
                    "seconds": float(seconds),
                }
            )
        return entries

    def _build_router_capability_status(self, llm_status: dict[str, Any]) -> list[dict[str, Any]]:
        capabilities: list[dict[str, Any]] = []
        provider = str(llm_status.get("provider") or "").strip().lower()
        text_health = llm_status.get("health")
        if provider in {"local", "ollama", "openai", "mock"}:
            capabilities.append(
                {
                    "provider": provider or "unknown",
                    "scope": "text",
                    "health": text_health,
                }
            )
        if provider in {"local", "ollama"}:
            capabilities.append(
                {
                    "provider": provider,
                    "scope": "vision",
                    "health": llm_status.get("vision_capability"),
                }
            )
        hosted_provider = str(llm_status.get("router_hosted_provider") or "").strip().lower()
        if hosted_provider and hosted_provider != provider:
            hosted_health = {"ok": True, "detail": "configured"}
            if hosted_provider == "openai" and not settings.openai_api_key:
                hosted_health = {"ok": False, "detail": "missing-openai-api-key"}
            capabilities.append(
                {
                    "provider": hosted_provider,
                    "scope": "text",
                    "health": hosted_health,
                }
            )
        return capabilities

    def _api_headers(self) -> dict[str, str]:
        if settings.api_auth_enabled and settings.api_bearer_token:
            return {"Authorization": f"Bearer {settings.api_bearer_token}"}
        return {}
