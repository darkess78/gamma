from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from ...config import settings
from ...errors import ConfigurationError
from ...observability import bind_context, configure_logging, log_event, redact, reset_context
from ..twitch.client import GammaStreamClient
from .adapter import DiscordMessage
from .runtime import DiscordRuntime, DiscordRuntimeConfig

try:  # pragma: no cover - exercised only in configured runtime.
    import discord
except Exception:  # pragma: no cover
    discord = None  # type: ignore[assignment]


class DiscordTextWorker:
    def __init__(
        self,
        config: DiscordRuntimeConfig | None = None,
        *,
        runtime: DiscordRuntime | None = None,
        stream_client: GammaStreamClient | None = None,
        state_path: Path | None = None,
        logger: logging.Logger | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config or DiscordRuntimeConfig.from_app_config()
        self.runtime = runtime or DiscordRuntime(self.config)
        self.stream_client = stream_client or GammaStreamClient()
        self.state_path = state_path or discord_worker_state_path()
        self.logger = logger or configure_logging("discord_text")
        self.client_factory = client_factory
        self._client: Any | None = None
        self._message_count = 0
        self._ignored_count = 0
        self._reconnect_count = 0
        self._has_connected = False

    def validate_config(self) -> None:
        if not self.config.enabled:
            raise ConfigurationError("Discord text worker is disabled.")
        if not self.config.bot_token:
            raise ConfigurationError("Discord bot token is not configured.")
        if not self.config.guild_id:
            raise ConfigurationError("Discord guild ID is not configured.")
        if not self.config.text_channel_id:
            raise ConfigurationError("Discord text channel ID is not configured.")
        if discord is None and self.client_factory is None:
            raise ConfigurationError(
                "Discord text ingestion requires the discord optional dependency: "
                ".venv/bin/python -m pip install -e '.[discord]'"
            )

    async def handle_message(self, message: Any) -> dict[str, Any]:
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        message_id = str(getattr(message, "id", "") or "")
        guild_id = str(getattr(guild, "id", "") or "")
        channel_id = str(getattr(channel, "id", "") or "")
        author_id = str(getattr(author, "id", "") or "")

        ignored_reason = self._ignored_reason(
            author=author,
            author_id=author_id,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        if ignored_reason:
            self._ignored_count += 1
            self._write_state(status="connected", connected=True, last_message_kind="ignored")
            log_event(
                self.logger,
                logging.INFO,
                "discord_text.message.ignored",
                "Ignored a Discord message.",
                message_id=message_id or None,
                reason=ignored_reason,
            )
            return {"ok": True, "ignored": True, "reason": ignored_reason}

        content = str(getattr(message, "content", "") or "")
        if not content.strip():
            self._ignored_count += 1
            return {"ok": True, "ignored": True, "reason": "empty_content"}

        roles = [
            str(getattr(role, "name", "") or "")
            for role in (getattr(author, "roles", None) or [])
            if str(getattr(role, "name", "") or "").strip()
        ]
        discord_message = DiscordMessage(
            text=content,
            user_id=author_id,
            display_name=str(getattr(author, "display_name", "") or getattr(author, "name", "") or "") or None,
            channel_id=channel_id,
            guild_id=guild_id,
            message_id=message_id or None,
            roles=roles,
        )
        session_id = f"discord:{guild_id}:{channel_id}"
        event = self.runtime.normalize_message(discord_message, session_id=session_id)
        event.metadata.setdefault("message_id", message_id)
        context_token = bind_context(
            event_id=event.event_id,
            message_id=message_id or None,
            session_id=event.session_id,
        )
        try:
            result = await asyncio.to_thread(
                self.stream_client.post_event,
                event,
                synthesize_speech=False,
                fast_mode=True,
            )
        except Exception as exc:
            self._write_state(
                status="connected",
                connected=True,
                last_error=str(exc),
                last_message_id=message_id or None,
            )
            log_event(
                self.logger,
                logging.ERROR,
                "discord_text.event.post_failed",
                "Failed to post a Discord message event to Gamma.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
                guild_id=guild_id,
                channel_id=channel_id,
            )
            return {"ok": False, "error": str(exc)}
        finally:
            reset_context(context_token)

        self._message_count += 1
        request_id = str(event.metadata.get("request_id") or "")
        self._write_state(
            status="connected",
            connected=True,
            last_message_id=message_id or None,
            last_request_id=request_id or None,
        )
        log_event(
            self.logger,
            logging.INFO,
            "discord_text.event.posted",
            "Posted a Discord message event to Gamma.",
            request_id=request_id or None,
            event_id=event.event_id,
            message_id=message_id or None,
            session_id=event.session_id,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        return {"ok": True, "ignored": False, "result": result}

    def run_forever(self) -> None:
        try:
            self.validate_config()
        except ConfigurationError as exc:
            self._write_state(status="configuration_error", connected=False, last_error=str(exc))
            log_event(
                self.logger,
                logging.ERROR,
                "discord_text.configuration.failed",
                "Discord text worker configuration is invalid.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
            )
            return

        client = self._build_client()
        self._client = client
        self._install_handlers(client)
        self._write_state(status="starting", connected=False)
        log_event(
            self.logger,
            logging.INFO,
            "discord_text.worker.start",
            "Discord text worker starting.",
            guild_id=self.config.guild_id,
            channel_id=self.config.text_channel_id,
            output_enabled=False,
            voice_enabled=False,
        )
        try:
            client.run(self.config.bot_token, log_handler=None, reconnect=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self._write_state(status="error", connected=False, last_error=str(exc))
            log_event(
                self.logger,
                logging.ERROR,
                "discord_text.worker.failed",
                "Discord text worker failed.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
                reconnect_count=self._reconnect_count,
            )
        finally:
            self._write_state(status="stopped", connected=False)
            log_event(
                self.logger,
                logging.INFO,
                "discord_text.worker.exit",
                "Discord text worker stopped.",
                message_count=self._message_count,
                ignored_count=self._ignored_count,
                reconnect_count=self._reconnect_count,
            )

    def _build_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        intents = discord.Intents.default()
        intents.message_content = True
        return discord.Client(intents=intents)

    def _install_handlers(self, client: Any) -> None:
        @client.event
        async def on_ready() -> None:
            if self._has_connected:
                self._reconnect_count += 1
                event_name = "discord_text.connection.reconnected"
                message = "Discord text worker reconnected."
            else:
                self._has_connected = True
                event_name = "discord_text.connection.connected"
                message = "Discord text worker connected."
            self._write_state(status="connected", connected=True)
            log_event(
                self.logger,
                logging.INFO,
                event_name,
                message,
                guild_id=self.config.guild_id,
                channel_id=self.config.text_channel_id,
                reconnect_count=self._reconnect_count,
            )

        @client.event
        async def on_disconnect() -> None:
            self._write_state(status="reconnecting", connected=False)
            log_event(
                self.logger,
                logging.WARNING,
                "discord_text.connection.disconnected",
                "Discord text worker disconnected; the client will reconnect.",
                reconnect_count=self._reconnect_count,
            )

        @client.event
        async def on_message(message: Any) -> None:
            await self.handle_message(message)

        @client.event
        async def on_error(event_method: str, *_args: Any, **_kwargs: Any) -> None:
            log_event(
                self.logger,
                logging.ERROR,
                "discord_text.client.event_failed",
                "Discord client event handler failed.",
                exc_info=True,
                event_method=event_method,
            )

    def _ignored_reason(
        self,
        *,
        author: Any,
        author_id: str,
        guild_id: str,
        channel_id: str,
    ) -> str | None:
        if bool(getattr(author, "bot", False)):
            return "bot_author"
        client_user_id = str(getattr(getattr(self._client, "user", None), "id", "") or "")
        if client_user_id and author_id == client_user_id:
            return "self_message"
        if guild_id != self.config.guild_id:
            return "guild_not_allowed"
        if channel_id != self.config.text_channel_id:
            return "channel_not_allowed"
        return None

    def _write_state(self, *, status: str, connected: bool, **extra: Any) -> None:
        payload = {
            "status": status,
            "connected": connected,
            "guild_id": self.config.guild_id or None,
            "text_channel_id": self.config.text_channel_id or None,
            "message_count": self._message_count,
            "ignored_count": self._ignored_count,
            "reconnect_count": self._reconnect_count,
            **extra,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def discord_worker_state_path() -> Path:
    return settings.data_dir / "runtime" / "discord_text" / "state.json"


def read_discord_worker_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or discord_worker_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Discord text ingestion worker.")
    parser.parse_args()
    DiscordTextWorker().run_forever()


if __name__ == "__main__":
    main()
