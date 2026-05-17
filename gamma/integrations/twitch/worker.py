from __future__ import annotations

import argparse
import json
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ...config import settings
from ...errors import ConfigurationError
from .client import GammaStreamClient
from .irc import chat_message_from_irc, parse_irc_line
from .normalize import normalize_chat_message
from .trust import ViewerTrustStore


@dataclass(frozen=True, slots=True)
class TwitchWorkerConfig:
    channel: str
    bot_username: str
    oauth_token: str
    owner_user_id: str | None = None
    host: str = "irc.chat.twitch.tv"
    port: int = 6697
    dry_run: bool = True
    voice_enabled: bool = False
    subtitles_enabled: bool = True
    ambient_chat_enabled: bool = True
    mention_replies_enabled: bool = True
    spam_quips_enabled: bool = True
    self_goal_proposals_enabled: bool = True
    llm_safety_review_enabled: bool = True
    min_speech_gap_seconds: int = 5
    spam_quip_cooldown_seconds: int = 60

    @classmethod
    def from_settings(cls) -> "TwitchWorkerConfig":
        missing = []
        if not settings.twitch_channel:
            missing.append("twitch_channel")
        if not settings.twitch_bot_username:
            missing.append("twitch_bot_username")
        if not settings.twitch_oauth_token:
            missing.append("twitch_oauth_token")
        if missing:
            raise ConfigurationError(f"Twitch worker requires configured {', '.join(missing)}.")
        return cls(
            channel=settings.twitch_channel,
            bot_username=settings.twitch_bot_username,
            oauth_token=settings.twitch_oauth_token,
            owner_user_id=settings.twitch_owner_user_id or None,
            host=settings.twitch_irc_host,
            port=settings.twitch_irc_port,
            dry_run=bool(getattr(settings, "twitch_dry_run", True)),
            voice_enabled=bool(getattr(settings, "twitch_voice_enabled", False)),
            subtitles_enabled=bool(getattr(settings, "twitch_subtitles_enabled", True)),
            ambient_chat_enabled=bool(getattr(settings, "twitch_ambient_chat_enabled", True)),
            mention_replies_enabled=bool(getattr(settings, "twitch_mention_replies_enabled", True)),
            spam_quips_enabled=bool(getattr(settings, "twitch_spam_quips_enabled", True)),
            self_goal_proposals_enabled=bool(getattr(settings, "twitch_self_goal_proposals_enabled", True)),
            llm_safety_review_enabled=bool(getattr(settings, "twitch_llm_safety_review_enabled", True)),
            min_speech_gap_seconds=max(0, int(getattr(settings, "twitch_min_speech_gap_seconds", 5))),
            spam_quip_cooldown_seconds=max(0, int(getattr(settings, "twitch_spam_quip_cooldown_seconds", 60))),
        )

    @property
    def normalized_channel(self) -> str:
        return self.channel.lstrip("#").strip().lower()

    def controls(self) -> dict[str, bool]:
        return {
            "dry_run": self.dry_run,
            "voice_enabled": self.voice_enabled,
            "subtitles_enabled": self.subtitles_enabled,
            "ambient_chat_enabled": self.ambient_chat_enabled,
            "mention_replies_enabled": self.mention_replies_enabled,
            "spam_quips_enabled": self.spam_quips_enabled,
            "self_goal_proposals_enabled": self.self_goal_proposals_enabled,
            "llm_safety_review_enabled": self.llm_safety_review_enabled,
            "min_speech_gap_seconds": self.min_speech_gap_seconds,
            "spam_quip_cooldown_seconds": self.spam_quip_cooldown_seconds,
        }


class TwitchIrcWorker:
    def __init__(
        self,
        *,
        config: TwitchWorkerConfig,
        client: GammaStreamClient | None = None,
        trust_store: ViewerTrustStore | None = None,
        synthesize_speech: bool | None = None,
        fast_mode: bool = True,
        state_path: Path | None = None,
    ) -> None:
        self.config = config
        self.client = client or GammaStreamClient()
        self.trust_store = trust_store or ViewerTrustStore()
        self.synthesize_speech = config.voice_enabled if synthesize_speech is None else synthesize_speech
        self.fast_mode = fast_mode
        self.state_path = state_path or twitch_worker_state_path()
        self._message_count = 0

    def handle_line(self, line: str) -> dict | None:
        irc_message = parse_irc_line(line)
        chat_message = chat_message_from_irc(irc_message)
        if chat_message is None:
            return None
        trust_level = self.trust_store.trust_level_for(
            platform="twitch",
            platform_user_id=chat_message.platform_user_id,
        )
        event = normalize_chat_message(
            chat_message,
            owner_user_id=self.config.owner_user_id,
            trust_level=trust_level,
            session_id=f"twitch:{self.config.normalized_channel}",
            twitch_controls=self.config.controls(),
        )
        result = self.client.post_event(
            event,
            synthesize_speech=self.synthesize_speech,
            fast_mode=self.fast_mode,
        )
        self._message_count += 1
        self._write_state(status="connected", connected=True, last_message_kind="chat_message")
        return result

    def run_forever(self, *, max_reconnects: int | None = None) -> None:
        reconnects = 0
        while True:
            try:
                self._write_state(status="connecting", connected=False, reconnects=reconnects)
                self._run_once()
                reconnects += 1
                self._write_state(status="disconnected", connected=False, detail="IRC socket closed.", reconnects=reconnects)
            except KeyboardInterrupt:
                self._write_state(status="stopped", connected=False, detail="Interrupted.", reconnects=reconnects)
                raise
            except Exception as exc:
                reconnects += 1
                self._write_state(status="reconnecting", connected=False, detail=str(exc), reconnects=reconnects)
            if max_reconnects is not None and reconnects >= max_reconnects:
                self._write_state(status="stopped", connected=False, detail="Reconnect limit reached.", reconnects=reconnects)
                return
            time.sleep(min(60.0, 2.0 ** min(reconnects, 5)))

    def _run_once(self) -> None:
        with socket.create_connection((self.config.host, self.config.port), timeout=30) as raw_socket:
            with ssl.create_default_context().wrap_socket(raw_socket, server_hostname=self.config.host) as irc:
                self._authenticate(irc)
                self._write_state(status="connected", connected=True, reconnects=0)
                for line in _iter_socket_lines(irc):
                    if line.startswith("PING "):
                        irc.sendall(line.replace("PING", "PONG", 1).encode("utf-8") + b"\r\n")
                        continue
                    self.handle_line(line)

    def _authenticate(self, irc: ssl.SSLSocket) -> None:
        token = self.config.oauth_token
        if not token.startswith("oauth:"):
            token = f"oauth:{token}"
        commands = [
            "CAP REQ :twitch.tv/tags twitch.tv/commands",
            f"PASS {token}",
            f"NICK {self.config.bot_username}",
            f"JOIN #{self.config.normalized_channel}",
        ]
        irc.sendall(("\r\n".join(commands) + "\r\n").encode("utf-8"))

    def _write_state(self, *, status: str, connected: bool, **extra: Any) -> None:
        payload = {
            "status": status,
            "connected": connected,
            "channel": self.config.normalized_channel,
            "host": self.config.host,
            "port": self.config.port,
            "message_count": self._message_count,
            "updated_at": _utc_now(),
            **extra,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def twitch_worker_state_path() -> Path:
    return settings.data_dir / "runtime" / "twitch_worker" / "state.json"


def read_twitch_worker_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or twitch_worker_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_socket_lines(sock: ssl.SSLSocket) -> Iterable[str]:
    buffer = ""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buffer += chunk.decode("utf-8", errors="replace")
        while "\r\n" in buffer:
            line, buffer = buffer.split("\r\n", 1)
            if line:
                yield line


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Twitch IRC ingestion worker.")
    parser.add_argument("--synthesize-speech", action="store_true", default=None)
    parser.add_argument("--slow-mode", action="store_true")
    args = parser.parse_args()
    worker = TwitchIrcWorker(
        config=TwitchWorkerConfig.from_settings(),
        synthesize_speech=args.synthesize_speech,
        fast_mode=not args.slow_mode,
    )
    worker.run_forever()


if __name__ == "__main__":
    main()
