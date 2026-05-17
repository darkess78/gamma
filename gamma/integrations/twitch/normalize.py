from __future__ import annotations

from typing import Any

from ...stream.models import StreamActor, StreamInputEvent
from .models import TrustLevel, TwitchChatMessage, TwitchReplayEvent
from .sanitize import classify_chat_text, safe_username_alias


def normalize_chat_message(
    message: TwitchChatMessage,
    *,
    owner_user_id: str | None = None,
    trust_level: TrustLevel = "new_viewer",
    session_id: str | None = "twitch",
    twitch_controls: dict[str, Any] | None = None,
) -> StreamInputEvent:
    platform_user_id = _clean_optional(message.platform_user_id)
    display_name = _clean_optional(message.display_name)
    is_owner = bool(owner_user_id and platform_user_id and platform_user_id == owner_user_id)
    effective_trust: TrustLevel = "owner" if is_owner else trust_level
    safety = classify_chat_text(message.text, display_name=display_name, trust_level=effective_trust)
    roles = ["owner"] if is_owner else []
    priority = max(0, safety.priority_delta)
    metadata: dict[str, Any] = {
        "raw_text": message.text,
        "message_id": message.message_id,
        "badges": message.badges,
        "tags": message.tags,
        "trust_level": effective_trust,
        "is_owner": is_owner,
        "input_safety": safety.model_dump(),
        "safe_prompt_text": safety.safe_prompt_text,
        "safe_display_name": safe_username_alias(display_name),
    }
    if twitch_controls is not None:
        metadata["twitch_controls"] = dict(twitch_controls)
    return StreamInputEvent(
        kind="chat_message",
        text=safety.safe_prompt_text,
        actor=StreamActor(source="twitch", platform_id=platform_user_id, display_name=display_name, roles=roles),
        session_id=session_id,
        priority=priority,
        metadata=metadata,
    )


def normalize_replay_event(
    event: TwitchReplayEvent,
    *,
    owner_user_id: str | None = None,
    trust_level: TrustLevel = "new_viewer",
    session_id: str | None = "twitch-replay",
) -> StreamInputEvent:
    if event.kind == "chat_message":
        return normalize_chat_message(
            TwitchChatMessage(
                text=event.text or "",
                platform_user_id=event.platform_user_id,
                display_name=event.display_name,
                message_id=event.message_id,
                badges=event.badges,
                tags=event.tags,
            ),
            owner_user_id=owner_user_id,
            trust_level=trust_level,
            session_id=session_id,
            twitch_controls=event.metadata.get("twitch_controls") if isinstance(event.metadata.get("twitch_controls"), dict) else None,
        )
    actor = StreamActor(
        source="twitch",
        platform_id=_clean_optional(event.platform_user_id),
        display_name=_clean_optional(event.display_name),
    )
    metadata = {
        **event.metadata,
        "raw_text": event.text,
        "message_id": event.message_id,
        "safe_display_name": safe_username_alias(event.display_name),
    }
    if event.kind == "follow":
        return StreamInputEvent(
            kind="follow",
            text=f"{metadata['safe_display_name']} followed the channel.",
            actor=actor,
            session_id=session_id,
            priority=20,
            metadata={**metadata, "twitch_event_kind": "follow"},
        )
    if event.kind == "raid":
        return StreamInputEvent(
            kind="raid",
            text=_raid_text(event),
            actor=actor,
            session_id=session_id,
            priority=25,
            metadata={**metadata, "viewer_count": event.viewer_count, "twitch_event_kind": "raid"},
        )
    if event.kind == "redeem":
        return StreamInputEvent(
            kind="redeem",
            text=_redeem_text(event),
            actor=actor,
            session_id=session_id,
            priority=10,
            metadata={**metadata, "title": event.title, "twitch_event_kind": "redeem"},
        )
    if event.kind == "bits":
        return StreamInputEvent(
            kind="bits",
            text=_bits_text(event),
            actor=actor,
            session_id=session_id,
            priority=15,
            metadata={**metadata, "amount": event.amount, "twitch_event_kind": "bits"},
        )
    if event.kind == "subscription":
        return StreamInputEvent(
            kind="subscription",
            text=_subscription_text(event),
            actor=actor,
            session_id=session_id,
            priority=15,
            metadata={**metadata, "title": event.title, "twitch_event_kind": "subscription"},
        )
    return StreamInputEvent(
        kind="donation",
        text=_donation_text(event),
        actor=actor,
        session_id=session_id,
        priority=15,
        metadata={**metadata, "amount": event.amount, "twitch_event_kind": "donation"},
    )


def _raid_text(event: TwitchReplayEvent) -> str:
    alias = safe_username_alias(event.display_name)
    if event.viewer_count:
        return f"{alias} raided with {event.viewer_count} viewers."
    return f"{alias} raided the channel."


def _redeem_text(event: TwitchReplayEvent) -> str:
    title = (event.title or "channel point redeem").strip()
    detail = (event.text or "").strip()
    return f"{title}: {detail}" if detail else title


def _bits_text(event: TwitchReplayEvent) -> str:
    alias = safe_username_alias(event.display_name)
    amount = (event.amount or "").strip()
    detail = (event.text or "").strip()
    if amount and detail:
        return f"{alias} cheered {amount} bits: {detail}"
    if amount:
        return f"{alias} cheered {amount} bits."
    return detail or f"{alias} cheered bits."


def _subscription_text(event: TwitchReplayEvent) -> str:
    alias = safe_username_alias(event.display_name)
    detail = (event.text or event.title or "").strip()
    return f"{alias} subscribed: {detail}" if detail else f"{alias} subscribed."


def _donation_text(event: TwitchReplayEvent) -> str:
    alias = safe_username_alias(event.display_name)
    amount = (event.amount or "").strip()
    detail = (event.text or "").strip()
    if amount and detail:
        return f"{alias} donated {amount}: {detail}"
    if amount:
        return f"{alias} donated {amount}."
    return detail or f"{alias} sent a support event."


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
