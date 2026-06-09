from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...identity.resolver import IdentityResolver
from ...schemas.conversation import SpeakerContext
from ...stream.models import StreamActor, StreamInputEvent


@dataclass(slots=True)
class DiscordMessage:
    text: str
    user_id: str
    display_name: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None
    message_id: str | None = None
    roles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscordVoiceUtterance:
    transcript: str
    user_id: str
    display_name: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None
    roles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_discord_message(
    message: DiscordMessage,
    *,
    session_id: str | None = "discord",
    identity_resolver: IdentityResolver | None = None,
) -> StreamInputEvent:
    profile = (identity_resolver or IdentityResolver()).resolve(SpeakerContext(source="discord", platform_id=message.user_id))
    roles = _roles(message.roles, is_owner=profile.is_owner, trust=profile.trust)
    return StreamInputEvent(
        kind="chat_message",
        text=message.text,
        actor=StreamActor(source="discord", platform_id=_clean(message.user_id), display_name=_clean(message.display_name), roles=roles),
        session_id=session_id,
        priority=10 if profile.is_owner else 0,
        metadata={
            **message.metadata,
            "raw_text": message.text,
            "message_id": message.message_id,
            "channel_id": message.channel_id,
            "guild_id": message.guild_id,
            "trust_level": profile.trust,
            "is_owner": profile.is_owner,
            "resolved_via": profile.resolved_via,
            "profile_name": profile.name,
        },
    )


def normalize_discord_voice(
    utterance: DiscordVoiceUtterance,
    *,
    session_id: str | None = "discord-voice",
    identity_resolver: IdentityResolver | None = None,
) -> StreamInputEvent:
    profile = (identity_resolver or IdentityResolver()).resolve(SpeakerContext(source="discord", platform_id=utterance.user_id))
    roles = _roles(utterance.roles, is_owner=profile.is_owner, trust=profile.trust)
    return StreamInputEvent(
        kind="mic_transcript",
        text=utterance.transcript,
        actor=StreamActor(source="discord", platform_id=_clean(utterance.user_id), display_name=_clean(utterance.display_name), roles=roles),
        session_id=session_id,
        priority=15 if profile.is_owner else 5,
        metadata={
            **utterance.metadata,
            "raw_text": utterance.transcript,
            "channel_id": utterance.channel_id,
            "guild_id": utterance.guild_id,
            "trust_level": profile.trust,
            "is_owner": profile.is_owner,
            "resolved_via": profile.resolved_via,
            "profile_name": profile.name,
            "input_modality": "voice",
        },
    )


def _roles(raw_roles: list[str], *, is_owner: bool, trust: str) -> list[str]:
    roles = [str(role).strip().lower() for role in raw_roles if str(role).strip()]
    if is_owner and "owner" not in roles:
        roles.insert(0, "owner")
    if trust in {"trusted", "guest"} and trust not in roles:
        roles.append(trust)
    return roles


def _clean(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None
