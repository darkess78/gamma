from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...config import load_app_file_config
from ...identity.resolver import IdentityResolver
from ...performer.models import DISCORD_CALL_TARGET, PerformerOutputEvent
from ...stream.models import StreamInputEvent
from .adapter import DiscordMessage, DiscordVoiceUtterance, normalize_discord_message, normalize_discord_voice


@dataclass(slots=True)
class DiscordRuntimeConfig:
    enabled: bool = False
    bot_token: str = ""
    guild_id: str = ""
    voice_channel_id: str = ""
    output_enabled: bool = False

    @classmethod
    def from_app_config(cls, config: dict[str, Any] | None = None) -> "DiscordRuntimeConfig":
        config = config if config is not None else load_app_file_config()
        nested = config.get("discord", {}) if isinstance(config.get("discord", {}), dict) else {}
        return cls(
            enabled=_as_bool(config.get("discord_enabled", nested.get("enabled", False))),
            bot_token=str(config.get("discord_bot_token", nested.get("bot_token", "")) or ""),
            guild_id=str(config.get("discord_guild_id", nested.get("guild_id", "")) or ""),
            voice_channel_id=str(config.get("discord_voice_channel_id", nested.get("voice_channel_id", "")) or ""),
            output_enabled=_as_bool(config.get("discord_output_enabled", nested.get("output_enabled", False))),
        )


class DiscordRuntime:
    """Dependency-light Discord adapter runtime.

    This is the boundary where a real Discord bot/client can be attached later.
    For now it owns config/status, Discord identity normalization, and isolated
    `discord_call` output handling.
    """

    def __init__(
        self,
        config: DiscordRuntimeConfig | None = None,
        *,
        identity_resolver: IdentityResolver | None = None,
    ) -> None:
        self.config = config or DiscordRuntimeConfig.from_app_config()
        self.identity_resolver = identity_resolver or IdentityResolver()
        self._running = False
        self._input_count = 0
        self._output_count = 0
        self._last_input: dict[str, Any] | None = None
        self._last_output: dict[str, Any] | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "configured": bool(self.config.bot_token),
            "running": self._running,
            "guild_id": self.config.guild_id or None,
            "voice_channel_id": self.config.voice_channel_id or None,
            "output_enabled": self.config.output_enabled,
            "input_count": self._input_count,
            "output_count": self._output_count,
            "last_input": self._last_input,
            "last_output": self._last_output,
            "last_error": self._last_error,
        }

    def start(self) -> dict[str, Any]:
        if not self.config.enabled:
            self._running = False
            self._last_error = "Discord runtime disabled"
            return {"ok": False, "error": self._last_error, "status": self.status()}
        if not self.config.bot_token:
            self._running = False
            self._last_error = "Discord bot token is not configured"
            return {"ok": False, "error": self._last_error, "status": self.status()}
        self._running = True
        self._last_error = None
        return {"ok": True, "status": self.status()}

    def stop(self) -> dict[str, Any]:
        self._running = False
        return {"ok": True, "status": self.status()}

    def normalize_message(self, message: DiscordMessage) -> StreamInputEvent:
        event = normalize_discord_message(message, identity_resolver=self.identity_resolver)
        self._record_input(event)
        return event

    def normalize_voice(self, utterance: DiscordVoiceUtterance) -> StreamInputEvent:
        event = normalize_discord_voice(utterance, identity_resolver=self.identity_resolver)
        self._record_input(event)
        return event

    def handle_output_event(self, event: PerformerOutputEvent) -> dict[str, Any]:
        if event.target_policy != DISCORD_CALL_TARGET:
            return {"ok": True, "handled": False, "reason": "target policy is not discord_call"}
        if not self.config.output_enabled:
            return {"ok": True, "handled": False, "reason": "Discord output disabled"}
        self._output_count += 1
        self._last_output = {
            "sequence": event.sequence,
            "type": event.type,
            "turn_id": event.turn_id,
            "payload": event.payload,
        }
        self._last_error = None
        return {"ok": True, "handled": True, "target_policy": DISCORD_CALL_TARGET}

    def _record_input(self, event: StreamInputEvent) -> None:
        self._input_count += 1
        self._last_input = {
            "kind": event.kind,
            "text": event.text,
            "actor": event.actor.model_dump() if event.actor else None,
            "session_id": event.session_id,
            "metadata": event.metadata,
        }
        self._last_error = None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
