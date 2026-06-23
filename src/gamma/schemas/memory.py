from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MemoryKind = Literal["profile_fact", "episodic"]
MemorySubject = Literal["primary_user", "other_person", "assistant", "general"]


class MemorySelection(BaseModel):
    kind: MemoryKind
    id: int = Field(gt=0)


class MemoryItemCreate(BaseModel):
    kind: MemoryKind
    summary: str = Field(min_length=1)
    subject_type: MemorySubject = "primary_user"
    subject_name: str | None = None
    relationship_to_user: str | None = None
    category: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    session_id: str | None = None


class MemoryItemUpdate(BaseModel):
    summary: str = Field(min_length=1)
    subject_name: str | None = None
    relationship_to_user: str | None = None
    category: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class MemoryClearRequest(BaseModel):
    scope: Literal["all", "recent", "selected"]
    minutes: int = Field(default=10, ge=1)
    selections: list[MemorySelection] = Field(default_factory=list)


class PersonAccountPayload(BaseModel):
    platform: str = Field(min_length=1)
    platform_user_id: str = Field(min_length=1)
    display_name: str | None = None


class KnownPersonPayload(BaseModel):
    id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1)
    trust: str = "guest"
    notes: str | None = None
    relationship_to_user: str | None = None
    accounts: list[PersonAccountPayload] = Field(default_factory=list)


class ViewerTrustPayload(BaseModel):
    platform: str = "twitch"
    platform_user_id: str = Field(min_length=1)
    display_name: str | None = None
    trust_level: str = "normal"
    notes: str | None = None
    pronunciation_alias: str | None = None
