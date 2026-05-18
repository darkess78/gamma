from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets

from ...config import settings
from ...errors import ConfigurationError
from ...stream.models import StreamActor, StreamInputEvent
from .client import GammaStreamClient
from .sanitize import safe_username_alias


EVENTSUB_WEBSOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_EVENTSUB_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"


@dataclass(frozen=True, slots=True)
class TwitchEventSubConfig:
    client_id: str
    oauth_token: str
    broadcaster_user_id: str
    moderator_user_id: str | None = None
    websocket_url: str = EVENTSUB_WEBSOCKET_URL
    subscriptions_url: str = HELIX_EVENTSUB_URL
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
    max_speech_seconds_per_minute: int = 20

    @classmethod
    def from_settings(cls) -> "TwitchEventSubConfig":
        missing = []
        if not settings.twitch_client_id:
            missing.append("twitch_client_id")
        if not settings.twitch_oauth_token:
            missing.append("twitch_oauth_token")
        if not settings.twitch_broadcaster_user_id:
            missing.append("twitch_broadcaster_user_id")
        if missing:
            raise ConfigurationError(f"Twitch EventSub requires configured {', '.join(missing)}.")
        return cls(
            client_id=settings.twitch_client_id,
            oauth_token=_bearer_token(settings.twitch_oauth_token),
            broadcaster_user_id=settings.twitch_broadcaster_user_id,
            moderator_user_id=settings.twitch_moderator_user_id or settings.twitch_broadcaster_user_id,
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
            max_speech_seconds_per_minute=max(0, int(getattr(settings, "twitch_max_speech_seconds_per_minute", 20))),
        )

    def controls(self) -> dict[str, bool | int]:
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
            "max_speech_seconds_per_minute": self.max_speech_seconds_per_minute,
        }


class TwitchEventSubWorker:
    def __init__(
        self,
        *,
        config: TwitchEventSubConfig,
        client: GammaStreamClient | None = None,
        state_path: Path | None = None,
        synthesize_speech: bool | None = None,
        fast_mode: bool = True,
    ) -> None:
        self.config = config
        self.client = client or GammaStreamClient()
        self.state_path = state_path or twitch_eventsub_state_path()
        self.synthesize_speech = config.voice_enabled if synthesize_speech is None else synthesize_speech
        self.fast_mode = fast_mode
        self._message_count = 0
        self._notification_count = 0

    async def run_forever(self) -> None:
        reconnects = 0
        while True:
            try:
                self._write_state(status="connecting", connected=False, reconnects=reconnects)
                async with websockets.connect(self.config.websocket_url) as websocket:
                    await self._run_socket(websocket, reconnects=reconnects)
            except KeyboardInterrupt:
                self._write_state(status="stopped", connected=False, detail="Interrupted.", reconnects=reconnects)
                raise
            except Exception as exc:
                reconnects += 1
                self._write_state(status="reconnecting", connected=False, detail=str(exc), reconnects=reconnects)
                await asyncio.sleep(min(60.0, 2.0 ** min(reconnects, 5)))

    async def _run_socket(self, websocket: Any, *, reconnects: int) -> None:
        async for raw_message in websocket:
            self._message_count += 1
            payload = json.loads(raw_message)
            metadata = payload.get("metadata") if isinstance(payload, dict) else {}
            message_type = metadata.get("message_type") if isinstance(metadata, dict) else None
            if message_type == "session_welcome":
                session_id = str(payload.get("payload", {}).get("session", {}).get("id") or "")
                if not session_id:
                    raise RuntimeError("EventSub welcome missing session id")
                subscriptions = self.create_subscriptions(session_id)
                self._write_state(
                    status="connected",
                    connected=True,
                    session_id=session_id,
                    subscriptions=subscriptions,
                    reconnects=reconnects,
                )
                continue
            if message_type == "session_keepalive":
                self._write_state(status="connected", connected=True, last_message_kind="keepalive", reconnects=reconnects)
                continue
            if message_type == "notification":
                event = stream_event_from_eventsub_notification(payload, twitch_controls=self.config.controls())
                if event is not None:
                    self.client.post_event(event, synthesize_speech=self.synthesize_speech, fast_mode=self.fast_mode)
                    self._notification_count += 1
                self._write_state(status="connected", connected=True, last_message_kind="notification", reconnects=reconnects)
                continue
            if message_type == "revocation":
                self._write_state(status="revoked", connected=True, last_message_kind="revocation", payload=payload, reconnects=reconnects)
                continue
            if message_type == "session_reconnect":
                reconnect_url = str(payload.get("payload", {}).get("session", {}).get("reconnect_url") or "")
                self._write_state(status="reconnect_requested", connected=True, reconnect_url=reconnect_url, reconnects=reconnects)
                return

    def create_subscriptions(self, session_id: str) -> list[dict[str, Any]]:
        specs = _subscription_specs(self.config)
        results = []
        for spec in specs:
            body = {
                "type": spec["type"],
                "version": spec["version"],
                "condition": spec["condition"],
                "transport": {"method": "websocket", "session_id": session_id},
            }
            results.append(self._create_subscription(body))
        return results

    def _create_subscription(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.subscriptions_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Client-Id": self.config.client_id,
                "Authorization": f"Bearer {self.config.oauth_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"EventSub subscription failed: http-{exc.code} {detail}") from exc

    def _write_state(self, *, status: str, connected: bool, **extra: Any) -> None:
        payload = {
            "status": status,
            "connected": connected,
            "message_count": self._message_count,
            "notification_count": self._notification_count,
            "updated_at": _utc_now(),
            **extra,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stream_event_from_eventsub_notification(payload: dict[str, Any], *, twitch_controls: dict[str, Any] | None = None) -> StreamInputEvent | None:
    subscription = payload.get("payload", {}).get("subscription", {})
    event = payload.get("payload", {}).get("event", {})
    if not isinstance(subscription, dict) or not isinstance(event, dict):
        return None
    event_type = str(subscription.get("type") or "")
    metadata = {
        "twitch_event_kind": event_type,
        "eventsub_subscription": subscription,
        "raw_event": event,
    }
    if twitch_controls is not None:
        metadata["twitch_controls"] = dict(twitch_controls)
    if event_type == "channel.follow":
        display_name = event.get("user_name")
        return _eventsub_stream_event("follow", display_name, event.get("user_id"), f"{safe_username_alias(display_name)} followed the channel.", 20, metadata)
    if event_type == "channel.raid":
        display_name = event.get("from_broadcaster_user_name")
        viewers = event.get("viewers")
        text = f"{safe_username_alias(display_name)} raided with {viewers} viewers." if viewers else f"{safe_username_alias(display_name)} raided the channel."
        return _eventsub_stream_event("raid", display_name, event.get("from_broadcaster_user_id"), text, 25, {**metadata, "viewer_count": viewers})
    if event_type == "channel.cheer":
        display_name = event.get("user_name")
        bits = event.get("bits")
        message = str(event.get("message") or "").strip()
        text = f"{safe_username_alias(display_name)} cheered {bits} bits" + (f": {message}" if message else ".")
        return _eventsub_stream_event("bits", display_name, event.get("user_id"), text, 15, {**metadata, "amount": str(bits or "")})
    if event_type in {"channel.subscribe", "channel.subscription.message"}:
        display_name = event.get("user_name")
        message = event.get("message")
        text_message = message.get("text") if isinstance(message, dict) else ""
        text = f"{safe_username_alias(display_name)} subscribed" + (f": {text_message}" if text_message else ".")
        return _eventsub_stream_event("subscription", display_name, event.get("user_id"), text, 15, metadata)
    if event_type == "channel.channel_points_custom_reward_redemption.add":
        display_name = event.get("user_name")
        reward = event.get("reward") if isinstance(event.get("reward"), dict) else {}
        title = str(reward.get("title") or "channel point redeem")
        user_input = str(event.get("user_input") or "").strip()
        text = f"{title}: {user_input}" if user_input else title
        return _eventsub_stream_event("redeem", display_name, event.get("user_id"), text, 10, {**metadata, "title": title})
    return None


def twitch_eventsub_state_path() -> Path:
    return settings.data_dir / "runtime" / "twitch_eventsub" / "state.json"


def read_twitch_eventsub_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or twitch_eventsub_state_path()
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _eventsub_stream_event(kind: str, display_name: Any, platform_id: Any, text: str, priority: int, metadata: dict[str, Any]) -> StreamInputEvent:
    return StreamInputEvent(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        actor=StreamActor(source="twitch", platform_id=str(platform_id) if platform_id else None, display_name=str(display_name) if display_name else None),
        session_id="twitch:eventsub",
        priority=priority,
        metadata=metadata,
    )


def _subscription_specs(config: TwitchEventSubConfig) -> list[dict[str, Any]]:
    broadcaster = config.broadcaster_user_id
    specs = [
        {"type": "channel.raid", "version": "1", "condition": {"to_broadcaster_user_id": broadcaster}},
        {"type": "channel.cheer", "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
        {"type": "channel.subscribe", "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
        {"type": "channel.subscription.message", "version": "1", "condition": {"broadcaster_user_id": broadcaster}},
        {
            "type": "channel.channel_points_custom_reward_redemption.add",
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster},
        },
    ]
    if config.moderator_user_id:
        specs.insert(
            0,
            {
                "type": "channel.follow",
                "version": "2",
                "condition": {"broadcaster_user_id": broadcaster, "moderator_user_id": config.moderator_user_id},
            },
        )
    return specs


def _bearer_token(token: str) -> str:
    normalized = token.strip()
    return normalized[len("oauth:"):] if normalized.startswith("oauth:") else normalized


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Gamma Twitch EventSub WebSocket worker.")
    parser.add_argument("--no-speech", action="store_true")
    args = parser.parse_args()
    config = TwitchEventSubConfig.from_settings()
    worker = TwitchEventSubWorker(config=config, synthesize_speech=False if args.no_speech else None)
    asyncio.run(worker.run_forever())


if __name__ == "__main__":
    main()
