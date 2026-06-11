from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileFact(SQLModel, table=True):
    __table_args__ = (Index("ix_profilefact_subject_created", "subject_type", "subject_name", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)
    category: str
    fact_text: str
    confidence: float = 0.8
    subject_type: str = Field(default="primary_user", index=True)
    subject_name: str | None = Field(default=None, index=True)
    relationship_to_user: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)


class EpisodicMemory(SQLModel, table=True):
    __table_args__ = (Index("ix_episodicmemory_session_created", "session_id", "created_at"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    summary: str
    importance: float = 0.5
    tags: str = ""
    subject_type: str = Field(default="primary_user", index=True)
    subject_name: str | None = Field(default=None, index=True)
    relationship_to_user: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)


class KnownPerson(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    relationship_to_user: str | None = None
    trust: str = Field(default="guest", index=True)
    notes: str = ""
    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now, index=True)


class PersonIdentity(SQLModel, table=True):
    __table_args__ = (Index("ix_personidentity_platform_user", "platform", "platform_user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="knownperson.id", index=True)
    platform: str = Field(index=True)
    platform_user_id: str = Field(index=True)
    display_name: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)
