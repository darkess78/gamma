from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileFact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    category: str
    fact_text: str
    confidence: float = 0.8
    subject_type: str = Field(default="primary_user", index=True)
    subject_name: str | None = Field(default=None, index=True)
    relationship_to_user: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)


class EpisodicMemory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    summary: str
    importance: float = 0.5
    tags: str = ""
    subject_type: str = Field(default="primary_user", index=True)
    subject_name: str | None = Field(default=None, index=True)
    relationship_to_user: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)
