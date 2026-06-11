from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TwitchReplayKind = Literal["chat_message", "follow", "raid", "redeem", "donation", "bits", "subscription"]
TrustLevel = Literal["owner", "trusted", "regular", "normal", "new_viewer", "suspicious", "blocked"]


class TwitchChatMessage(BaseModel):
    """Represents a Twitch chat message.
    
    Attributes:
        text: Message text content.
        platform_user_id: Twitch user ID or None.
        display_name: User display name.
        message_id: Chat message ID.
        badges: Chat badges dict.
        tags: Chat tags dict.
    """
    text: str
    platform_user_id: str | None = None
    display_name: str | None = None
    message_id: str | None = None
    badges: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, Any] = Field(default_factory=dict)


class TwitchReplayEvent(BaseModel):
    """Represents a replayable Twitch event (chat, follow, raid, etc.).
    
    Attributes:
        kind: Event kind (chat_message, follow, raid, redeem, donation, bits, subscription).
        text: Chat message text or None for non-chat events.
        platform_user_id: Twitch user ID or None.
        display_name: Event participant's display name.
        message_id: Chat message ID or None.
        title: Event title (e.g., donation amount/title).
        amount: Monetary amount for donation/bits events.
        viewer_count: Current viewer count (Twitch only).
        badges: Event participant badges dict.
        tags: Twitch chat tags dict.
        metadata: Additional event metadata.
    """
    kind: TwitchReplayKind
    text: str | None = None
    platform_user_id: str | None = None
    display_name: str | None = None
    message_id: str | None = None
    title: str | None = None
    amount: str | None = None
    viewer_count: int | None = None
    badges: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TwitchSafetyClassification(BaseModel):
    """Safety classification for Twitch content.
    
    Attributes:
        category: Safety category (normal, spam, harassment, etc.).
        safe_prompt_text: Safe text substitution for blocked content.
        should_drop: Whether to drop/block this content.
        priority_delta: Priority adjustment multiplier.
        reasons: List of matched safety rule text.
    """
    category: str = "normal"
    safe_prompt_text: str
    should_drop: bool = False
    priority_delta: int = 0
    reasons: list[str] = Field(default_factory=list)
