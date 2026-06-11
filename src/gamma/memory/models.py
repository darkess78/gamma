from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    """Get UTC now.
    
    Returns:
        datetime: Current UTC time.
    """
    return datetime.now(timezone.utc)


class ProfileFact(SQLModel, table=True):
    """Profile fact.
    
    Attributes:
        id: Fact ID.
        category: Fact category.
        fact_text: Fact text.
        confidence: Confidence score.
        subject_type: Subject type.
        subject_name: Subject name.
        relationship_to_user: Relationship to user.
        created_at: Creation timestamp.
    """
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
    """Episodic memory.
    
    Attributes:
        id: Memory ID.
        session_id: Session ID.
        summary: Memory summary.
        importance: Importance score.
        tags: Memory tags.
        subject_type: Subject type.
        subject_name: Subject name.
        relationship_to_user: Relationship to user.
        created_at: Creation timestamp.
    """
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
    """Known person.
    
    Attributes:
        id: Person ID.
        name: Person name.
        relationship_to_user: Relationship to user.
        trust: Trust level.
        notes: Person notes.
        created_at: Creation timestamp.
        updated_at: Update timestamp.
    """
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    relationship_to_user: str | None = None
    trust: str = Field(default="guest", index=True)
    notes: str = ""
    created_at: datetime = Field(default_factory=_utc_now, index=True)
    updated_at: datetime = Field(default_factory=_utc_now, index=True)


class PersonIdentity(SQLModel, table=True):
    """Person identity.
    
    Attributes:
        id: Identity ID.
        person_id: Person ID.
        platform: Platform name.
        platform_user_id: Platform user ID.
        display_name: Display name.
        created_at: Creation timestamp.
    """
    __table_args__ = (Index("ix_personidentity_platform_user", "platform", "platform_user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="knownperson.id", index=True)
    platform: str = Field(index=True)
    platform_user_id: str = Field(index=True)
    display_name: str | None = None
    created_at: datetime = Field(default_factory=_utc_now, index=True)
