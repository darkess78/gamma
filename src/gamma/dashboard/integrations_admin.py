"""Twitch and Discord worker controls, stream readiness and Twitch replay."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from ..config import app_local_config_path, load_app_file_config, settings
from ..errors import ConfigurationError
from ..integrations.discord import DiscordRuntimeConfig, read_discord_worker_state
from ..integrations.twitch.client import GammaStreamClient
from ..integrations.twitch.eventsub import TwitchEventSubConfig, read_twitch_eventsub_state
from ..integrations.twitch.replay import replay_jsonl_text
from ..integrations.twitch.worker import TwitchWorkerConfig, read_twitch_worker_state


class IntegrationsAdminMixin:
    """Twitch and Discord worker controls, stream readiness and Twitch replay."""

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
        process = self._process_manager.module_status(self.DISCORD_TEXT_SERVICE, self.DISCORD_TEXT_MODULE)
        state = read_discord_worker_state()
        from ..integrations.discord.bot_status import summarize
        try:
            summary = summarize(process.get("process", {}), state, time.time())
        except Exception:
            summary = {"connected": False, "voice": "unknown", "label": "Status unverified"}
        return {
            **process,
            "summary": summary,
            "configured": not missing,
            "missing_config": missing,
            "enabled": config.enabled,
            "guild_id": config.guild_id or None,
            "text_channel_id": config.text_channel_id or None,
            "worker": "discord_text",
            "output_enabled": config.output_enabled,
            "voice_enabled": config.voice_enabled,
            "state": read_discord_worker_state(),
        }

    def discord_voice_control(self, command: str) -> dict[str, Any]:
        from ..integrations.discord.control import ControlMailbox
        if not self.discord_text_worker_status().get("process", {}).get("running"):
            raise ValueError("Shana Discord bot is not running")
        return ControlMailbox(settings.data_dir / "runtime" / "discord_text" / "controls").submit(command)

    def discord_voice_command_status(self, command_id: str) -> dict[str, Any]:
        from ..integrations.discord.control import ControlMailbox
        return ControlMailbox(settings.data_dir / "runtime" / "discord_text" / "controls").read(command_id)

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
