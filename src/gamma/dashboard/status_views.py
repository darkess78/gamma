"""Dashboard status payloads and the runtime log readers behind them."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from ..config import settings
from ..resources.allocations import recent_sidecar_allocation_entries
from ..resources.runtime_registry import load_resource_routing_registry
from ..safety.floor_log import recent_floor_events
from ..voice.voice_profiles import get_voice_profile, list_voice_profiles


class StatusViewsMixin:
    """Dashboard status payloads and the runtime log readers behind them."""

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
                "floor_events": recent_floor_events(20),
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
            # Home shows Shana's mood and the Discord bot next to the other services.
            "assistant": {
                "emotion": (local_status.get("assistant", {}).get("emotion_memory", {}) or {}).get("state", {}),
            },
            "discord": {"text_worker": self.discord_text_worker_status()},
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

        payload.pop("llm_routing", None)
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
        payload.pop("llm_routing", None)
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
        return {"entries": entries, "summary": summary}

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
