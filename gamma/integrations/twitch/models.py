from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TwitchReplayKind = Literal["chat_message", "follow", "raid", "redeem", "donation", "bits", "subscription"]
TrustLevel = Literal["owner", "trusted", "regular", "normal", "new_viewer", "suspicious", "blocked"]


class TwitchChatMessage(BaseModel):
    text: str
    platform_user_id: str | None = None
    display_name: str | None = None
    message_id: str | None = None
    badges: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, Any] = Field(default_factory=dict)


class TwitchReplayEvent(BaseModel):
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
    category: str = "normal"
    safe_prompt_text: str
    should_drop: bool = False
    priority_delta: int = 0
    reasons: list[str] = Field(default_factory=list)
