from __future__ import annotations

import argparse
import socket
import ssl
from dataclasses import dataclass
from typing import Iterable

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
        )

    @property
    def normalized_channel(self) -> str:
        return self.channel.lstrip("#").strip().lower()


class TwitchIrcWorker:
    def __init__(
        self,
        *,
        config: TwitchWorkerConfig,
        client: GammaStreamClient | None = None,
        trust_store: ViewerTrustStore | None = None,
        synthesize_speech: bool = False,
        fast_mode: bool = True,
    ) -> None:
        self.config = config
        self.client = client or GammaStreamClient()
        self.trust_store = trust_store or ViewerTrustStore()
        self.synthesize_speech = synthesize_speech
        self.fast_mode = fast_mode

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
        )
        return self.client.post_event(
            event,
            synthesize_speech=self.synthesize_speech,
            fast_mode=self.fast_mode,
        )

    def run_forever(self) -> None:
        with socket.create_connection((self.config.host, self.config.port), timeout=30) as raw_socket:
            with ssl.create_default_context().wrap_socket(raw_socket, server_hostname=self.config.host) as irc:
                self._authenticate(irc)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Twitch IRC ingestion worker.")
    parser.add_argument("--synthesize-speech", action="store_true")
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
