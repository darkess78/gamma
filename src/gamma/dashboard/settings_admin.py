"""Reading and saving assistant runtime settings in the local TOML config."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import app_local_config_path, load_app_file_config, settings


class SettingsAdminMixin:
    """Reading and saving assistant runtime settings in the local TOML config."""

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
            "speech_filter_word_list_enabled": bool(
                config.get("speech_filter_word_list_enabled", settings.speech_filter_word_list_enabled)
            ),
            "speech_filter_extra_enabled": bool(config.get("speech_filter_extra_enabled", settings.speech_filter_extra_enabled)),
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
        # The safety floor (hard block, phrase heuristics, reviewer) is not saved from here:
        # it only changes by editing the config file. The word list and extra safety are.
        bool_keys = [
            "speech_filter_word_list_enabled",
            "speech_filter_extra_enabled",
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

    def _resolve_path(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute():
            path = settings.project_root / path
        return path
