from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets

from ...config import settings
from ...errors import ConfigurationError
from ...observability import bind_context, configure_logging, log_event, redact, reset_context
from ...stream.models import StreamActor, StreamInputEvent
from .client import GammaStreamClient
from .sanitize import classify_chat_text, safe_username_alias


EVENTSUB_WEBSOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_EVENTSUB_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
TWITCH_TOKEN_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


class EventSubConfigurationFailure(RuntimeError):
    """Fatal EventSub authentication, identity, or scope failure."""


class EventSubSubscriptionFailure(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


@dataclass(frozen=True, slots=True)
class TwitchEventSubConfig:
    client_id: str
    oauth_token: str
    broadcaster_user_id: str
    moderator_user_id: str | None = None
    websocket_url: str = EVENTSUB_WEBSOCKET_URL
    subscriptions_url: str = HELIX_EVENTSUB_URL
    token_validate_url: str = TWITCH_TOKEN_VALIDATE_URL
    subscription_types: tuple[str, ...] = ("channel.follow",)
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
            subscription_types=tuple(getattr(settings, "twitch_eventsub_subscriptions", ("channel.follow",))),
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
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.client = client or GammaStreamClient()
        self.state_path = state_path or twitch_eventsub_state_path()
        self.synthesize_speech = config.voice_enabled if synthesize_speech is None else synthesize_speech
        self.fast_mode = fast_mode
        self.logger = logger or configure_logging("twitch_eventsub")
        self._message_count = 0
        self._notification_count = 0
        self._subscriptions: list[dict[str, Any]] = []
        self._subscription_ok_count = 0
        self._subscription_error_count = 0
        self._last_token_validation_monotonic = 0.0

    async def run_forever(self) -> None:
        reconnects = 0
        websocket_url = self.config.websocket_url
        log_event(
            self.logger,
            logging.INFO,
            "eventsub.worker.start",
            "Twitch EventSub worker starting.",
            subscriptions=list(self.config.subscription_types),
            dry_run=self.config.dry_run,
            voice_enabled=self.config.voice_enabled,
        )
        try:
            token_evidence = await asyncio.to_thread(self.validate_token)
        except EventSubConfigurationFailure as exc:
            self._write_state(status="configuration_error", connected=False, detail=str(exc), reconnects=reconnects)
            log_event(
                self.logger,
                logging.ERROR,
                "eventsub.token.invalid",
                "Twitch OAuth token validation failed.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
            )
            return
        except Exception as exc:
            self._write_state(status="token_validation_error", connected=False, detail=str(exc), reconnects=reconnects)
            log_event(
                self.logger,
                logging.ERROR,
                "eventsub.token.validation_error",
                "Twitch OAuth token validation request failed.",
                exc_info=True,
                error_class=type(exc).__name__,
                detail=str(exc),
            )
            return
        log_event(
            self.logger,
            logging.INFO,
            "eventsub.token.validated",
            "Twitch OAuth token validated.",
            **token_evidence,
        )
        self._last_token_validation_monotonic = time.monotonic()
        while True:
            try:
                self._write_state(status="connecting", connected=False, reconnects=reconnects)
                log_event(
                    self.logger,
                    logging.INFO,
                    "eventsub.connection.opening",
                    "Opening Twitch EventSub WebSocket.",
                    reconnect_count=reconnects,
                    reconnect=websocket_url != self.config.websocket_url,
                )
                async with websockets.connect(websocket_url) as websocket:
                    reconnect_url = await self._run_socket(websocket, reconnects=reconnects)
                if reconnect_url:
                    websocket_url = reconnect_url
                    continue
                raise RuntimeError("EventSub WebSocket closed without a reconnect request.")
            except KeyboardInterrupt:
                self._write_state(status="stopped", connected=False, detail="Interrupted.", reconnects=reconnects)
                log_event(self.logger, logging.INFO, "eventsub.worker.exit", "Twitch EventSub worker interrupted.")
                raise
            except EventSubConfigurationFailure as exc:
                self._write_state(status="subscription_error", connected=False, detail=str(exc), reconnects=reconnects)
                log_event(
                    self.logger,
                    logging.ERROR,
                    "eventsub.subscription.fatal",
                    "EventSub subscription cannot be created with the current authorization.",
                    exc_info=True,
                    error_class=type(exc).__name__,
                    detail=str(exc),
                    reconnect_count=reconnects,
                )
                log_event(self.logger, logging.INFO, "eventsub.worker.exit", "Twitch EventSub worker stopped.")
                return
            except Exception as exc:
                reconnects += 1
                websocket_url = self.config.websocket_url
                backoff_seconds = min(60.0, 2.0 ** min(reconnects, 5))
                self._write_state(
                    status="reconnecting",
                    connected=False,
                    detail=str(exc),
                    reconnects=reconnects,
                    backoff_seconds=backoff_seconds,
                )
                log_event(
                    self.logger,
                    logging.ERROR,
                    "eventsub.connection.error",
                    "Twitch EventSub connection failed.",
                    exc_info=True,
                    error_class=type(exc).__name__,
                    detail=str(exc),
                    reconnect_count=reconnects,
                    backoff_seconds=backoff_seconds,
                )
                log_event(
                    self.logger,
                    logging.WARNING,
                    "eventsub.connection.backoff",
                    "Waiting before reconnecting to Twitch EventSub.",
                    reconnect_count=reconnects,
                    backoff_seconds=backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)

    async def _run_socket(self, websocket: Any, *, reconnects: int) -> str | None:
        async for raw_message in websocket:
            self._message_count += 1
            payload = json.loads(raw_message)
            metadata = payload.get("metadata") if isinstance(payload, dict) else {}
            message_type = metadata.get("message_type") if isinstance(metadata, dict) else None
            message_id = str(metadata.get("message_id") or "") if isinstance(metadata, dict) else ""
            if message_type == "session_welcome":
                session = payload.get("payload", {}).get("session", {})
                session_id = str(session.get("id") or "")
                if not session_id:
                    raise RuntimeError("EventSub welcome missing session id")
                log_event(
                    self.logger,
                    logging.INFO,
                    "eventsub.session.welcome",
                    "Twitch EventSub session welcomed.",
                    session_id=session_id,
                    keepalive_timeout_seconds=session.get("keepalive_timeout_seconds"),
                    reconnect_count=reconnects,
                )
                started = time.perf_counter()
                subscriptions = await asyncio.to_thread(self.create_subscriptions, session_id)
                self._subscriptions = subscriptions
                self._subscription_ok_count = sum(1 for subscription in subscriptions if subscription.get("ok"))
                self._subscription_error_count = sum(1 for subscription in subscriptions if not subscription.get("ok"))
                elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                for subscription in subscriptions:
                    event_name = "eventsub.subscription.created" if subscription.get("ok") else "eventsub.subscription.failed"
                    level = logging.INFO if subscription.get("ok") else logging.ERROR
                    log_event(
                        self.logger,
                        level,
                        event_name,
                        "EventSub subscription request completed.",
                        session_id=session_id,
                        subscription_type=subscription.get("type"),
                        version=subscription.get("version"),
                        ok=bool(subscription.get("ok")),
                        status_code=subscription.get("status_code"),
                        error_class=subscription.get("error_class"),
                        detail=subscription.get("error"),
                        duration_ms=subscription.get("duration_ms"),
                    )
                self._write_state(
                    status="connected",
                    connected=True,
                    session_id=session_id,
                    reconnects=reconnects,
                    subscription_setup_ms=elapsed_ms,
                )
                if not self._subscription_ok_count:
                    detail = _subscription_failure_detail(subscriptions)
                    if any(item.get("fatal") for item in subscriptions):
                        raise EventSubConfigurationFailure(detail)
                    raise RuntimeError(detail)
                log_event(
                    self.logger,
                    logging.INFO,
                    "eventsub.connection.connected",
                    "Twitch EventSub session is connected and subscribed.",
                    session_id=session_id,
                    subscription_ok_count=self._subscription_ok_count,
                    subscription_error_count=self._subscription_error_count,
                    subscription_setup_ms=elapsed_ms,
                )
                continue
            if message_type == "session_keepalive":
                if (
                    self._last_token_validation_monotonic
                    and time.monotonic() - self._last_token_validation_monotonic >= 3600
                ):
                    token_evidence = await asyncio.to_thread(self.validate_token)
                    self._last_token_validation_monotonic = time.monotonic()
                    log_event(
                        self.logger,
                        logging.INFO,
                        "eventsub.token.validated",
                        "Twitch OAuth token revalidated.",
                        **token_evidence,
                    )
                self._write_state(status="connected", connected=True, last_message_kind="keepalive", reconnects=reconnects)
                log_event(
                    self.logger,
                    logging.INFO,
                    "eventsub.session.keepalive",
                    "Twitch EventSub keepalive received.",
                    message_id=message_id or None,
                    reconnect_count=reconnects,
                )
                continue
            if message_type == "notification":
                event = stream_event_from_eventsub_notification(payload, twitch_controls=self.config.controls())
                if event is not None:
                    subscription = payload.get("payload", {}).get("subscription", {})
                    evidence = _safe_notification_evidence(event=event, subscription=subscription if isinstance(subscription, dict) else {})
                    event.metadata.setdefault("message_id", message_id)
                    event.metadata.setdefault("eventsub_subscription_id", str(subscription.get("id") or ""))
                    context_token = bind_context(
                        event_id=event.event_id,
                        message_id=message_id,
                        session_id=event.session_id,
                    )
                    try:
                        self.client.post_event(event, synthesize_speech=self.synthesize_speech, fast_mode=self.fast_mode)
                    except Exception as exc:
                        log_event(
                            self.logger,
                            logging.ERROR,
                            "eventsub.notification.post_failed",
                            "Failed to post EventSub notification to Gamma.",
                            exc_info=True,
                            error_class=type(exc).__name__,
                            detail=str(exc),
                            subscription_type=evidence["last_subscription_type"],
                        )
                        self._write_state(
                            status="connected",
                            connected=True,
                            last_message_kind="notification",
                            last_post_error=str(exc),
                            reconnects=reconnects,
                            **evidence,
                        )
                        reset_context(context_token)
                        continue
                    self._notification_count += 1
                    log_event(
                        self.logger,
                        logging.INFO,
                        "eventsub.notification.posted",
                        "EventSub notification posted to Gamma.",
                        subscription_type=evidence["last_subscription_type"],
                        event_kind=event.kind,
                    )
                    self._write_state(
                        status="connected",
                        connected=True,
                        last_message_kind="notification",
                        last_post_error="",
                        reconnects=reconnects,
                        **evidence,
                    )
                    reset_context(context_token)
                    continue
                self._write_state(status="connected", connected=True, last_message_kind="notification", reconnects=reconnects)
                log_event(
                    self.logger,
                    logging.WARNING,
                    "eventsub.notification.ignored",
                    "Unsupported EventSub notification ignored.",
                    message_id=message_id or None,
                )
                continue
            if message_type == "revocation":
                subscription = payload.get("payload", {}).get("subscription", {})
                self._write_state(
                    status="revoked",
                    connected=True,
                    last_message_kind="revocation",
                    revocation=subscription,
                    reconnects=reconnects,
                )
                log_event(
                    self.logger,
                    logging.ERROR,
                    "eventsub.subscription.revoked",
                    "Twitch revoked an EventSub subscription.",
                    message_id=message_id or None,
                    subscription_type=subscription.get("type"),
                    subscription_status=subscription.get("status"),
                )
                continue
            if message_type == "session_reconnect":
                reconnect_url = str(payload.get("payload", {}).get("session", {}).get("reconnect_url") or "")
                self._write_state(status="reconnect_requested", connected=True, reconnect_url=reconnect_url, reconnects=reconnects)
                log_event(
                    self.logger,
                    logging.WARNING,
                    "eventsub.session.reconnect_requested",
                    "Twitch requested an EventSub session reconnect.",
                    message_id=message_id or None,
                    reconnect_count=reconnects,
                    reconnect_url_present=bool(reconnect_url),
                )
                if not reconnect_url:
                    raise RuntimeError("EventSub reconnect message missing reconnect URL.")
                return reconnect_url
        return None

    def validate_token(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.token_validate_url,
            headers={"Authorization": f"OAuth {self.config.oauth_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise EventSubConfigurationFailure(f"Twitch OAuth token validation failed: http-{exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"Twitch OAuth token validation request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise EventSubConfigurationFailure("Twitch OAuth token validation returned a non-object payload.")
        token_client_id = str(payload.get("client_id") or "")
        token_user_id = str(payload.get("user_id") or "")
        scopes = {str(scope) for scope in payload.get("scopes", []) if scope}
        supported_types = {spec["type"] for spec in _all_subscription_specs(self.config)}
        unknown_types = sorted(set(self.config.subscription_types) - supported_types)
        if unknown_types:
            raise EventSubConfigurationFailure(
                f"Unsupported EventSub subscription type(s): {', '.join(unknown_types)}."
            )
        if token_client_id != self.config.client_id:
            raise EventSubConfigurationFailure("Twitch OAuth token client ID does not match the configured client ID.")
        required_scopes = _required_scopes(self.config.subscription_types)
        missing_scopes = sorted(required_scopes - scopes)
        if missing_scopes:
            raise EventSubConfigurationFailure(
                f"Twitch OAuth token is missing required scope(s): {', '.join(missing_scopes)}."
            )
        if "channel.follow" in self.config.subscription_types and token_user_id != str(self.config.moderator_user_id or ""):
            raise EventSubConfigurationFailure(
                "channel.follow requires moderator_user_id to match the user ID in the OAuth token."
            )
        return {
            "token_user_id": token_user_id,
            "token_login": str(payload.get("login") or ""),
            "scope_count": len(scopes),
            "expires_in_seconds": payload.get("expires_in"),
            "required_scopes": sorted(required_scopes),
        }

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
            started = time.perf_counter()
            try:
                result = self._create_subscription(body)
                results.append(
                    {
                        "ok": True,
                        "type": spec["type"],
                        "version": spec["version"],
                        "status_code": 202,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "response": result,
                    }
                )
            except EventSubSubscriptionFailure as exc:
                results.append(
                    {
                        "ok": False,
                        "type": spec["type"],
                        "version": spec["version"],
                        "status_code": exc.status_code,
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                        "fatal": exc.status_code in {400, 401, 403},
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        "response": exc.response,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "ok": False,
                        "type": spec["type"],
                        "version": spec["version"],
                        "error_class": type(exc).__name__,
                        "error": str(exc),
                        "fatal": False,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                )
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
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise EventSubSubscriptionFailure(
                f"EventSub subscription failed: http-{exc.code} {detail}",
                status_code=exc.code,
                response=detail if isinstance(detail, dict) else {"detail": detail},
            ) from exc

    def _write_state(self, *, status: str, connected: bool, **extra: Any) -> None:
        payload = {
            "status": status,
            "connected": connected,
            "message_count": self._message_count,
            "notification_count": self._notification_count,
            "subscriptions": self._subscriptions,
            "subscription_ok_count": self._subscription_ok_count,
            "subscription_error_count": self._subscription_error_count,
            "updated_at": _utc_now(),
            **extra,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        message, input_safety = _safe_viewer_detail(event.get("message"), display_name=display_name)
        text = f"{safe_username_alias(display_name)} cheered {bits} bits" + (f": {message}" if message else ".")
        return _eventsub_stream_event("bits", display_name, event.get("user_id"), text, 15, {**metadata, "amount": str(bits or ""), **input_safety})
    if event_type in {"channel.subscribe", "channel.subscription.message"}:
        display_name = event.get("user_name")
        message = event.get("message")
        text_message, input_safety = _safe_viewer_detail(message.get("text") if isinstance(message, dict) else "", display_name=display_name)
        text = f"{safe_username_alias(display_name)} subscribed" + (f": {text_message}" if text_message else ".")
        return _eventsub_stream_event("subscription", display_name, event.get("user_id"), text, 15, {**metadata, **input_safety})
    if event_type == "channel.channel_points_custom_reward_redemption.add":
        display_name = event.get("user_name")
        reward = event.get("reward") if isinstance(event.get("reward"), dict) else {}
        title = str(reward.get("title") or "channel point redeem")
        user_input, input_safety = _safe_viewer_detail(event.get("user_input"), display_name=display_name)
        text = f"{title}: {user_input}" if user_input else title
        return _eventsub_stream_event("redeem", display_name, event.get("user_id"), text, 10, {**metadata, "title": title, **input_safety})
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
    safe_display_name = safe_username_alias(str(display_name) if display_name else None)
    return StreamInputEvent(
        kind=kind,  # type: ignore[arg-type]
        text=text,
        actor=StreamActor(source="twitch", platform_id=str(platform_id) if platform_id else None, display_name=str(display_name) if display_name else None),
        session_id="twitch:eventsub",
        priority=priority,
        metadata={**metadata, "safe_display_name": safe_display_name},
    )


def _safe_viewer_detail(raw_text: Any, *, display_name: Any) -> tuple[str, dict[str, Any]]:
    text = str(raw_text or "").strip()
    if not text:
        return "", {}
    safety = classify_chat_text(text, display_name=str(display_name) if display_name else None)
    return safety.safe_prompt_text, {
        "raw_text": text,
        "input_safety": safety.model_dump(),
        "safe_prompt_text": safety.safe_prompt_text,
    }


def _subscription_specs(config: TwitchEventSubConfig) -> list[dict[str, Any]]:
    enabled = set(config.subscription_types)
    return [spec for spec in _all_subscription_specs(config) if spec["type"] in enabled]


def _all_subscription_specs(config: TwitchEventSubConfig) -> list[dict[str, Any]]:
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


def _safe_notification_evidence(*, event: StreamInputEvent, subscription: dict[str, Any]) -> dict[str, str]:
    return {
        "last_subscription_type": str(subscription.get("type") or event.metadata.get("twitch_event_kind") or ""),
        "last_posted_event_kind": event.kind,
        "last_actor_display_name": event.actor.display_name or "",
    }


def _required_scopes(subscription_types: tuple[str, ...]) -> set[str]:
    scopes_by_type = {
        "channel.follow": {"moderator:read:followers"},
        "channel.cheer": {"bits:read"},
        "channel.subscribe": {"channel:read:subscriptions"},
        "channel.subscription.message": {"channel:read:subscriptions"},
        "channel.channel_points_custom_reward_redemption.add": {"channel:read:redemptions"},
    }
    required: set[str] = set()
    for subscription_type in subscription_types:
        required.update(scopes_by_type.get(subscription_type, set()))
    return required


def _subscription_failure_detail(subscriptions: list[dict[str, Any]]) -> str:
    failures = [
        f"{item.get('type')}: {item.get('error') or 'unknown subscription error'}"
        for item in subscriptions
        if not item.get("ok")
    ]
    return "No EventSub subscriptions were created. " + "; ".join(failures)


def _http_error_detail(exc: urllib.error.HTTPError) -> str | dict[str, Any]:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return str(redact(raw))
    return redact(payload) if isinstance(payload, dict) else str(redact(raw))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Gamma Twitch EventSub WebSocket worker.")
    parser.add_argument("--no-speech", action="store_true")
    args = parser.parse_args()
    config = TwitchEventSubConfig.from_settings()
    worker = TwitchEventSubWorker(config=config, synthesize_speech=False if args.no_speech else None)
    asyncio.run(worker.run_forever())


if __name__ == "__main__":
    main()
