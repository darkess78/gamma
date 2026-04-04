from __future__ import annotations

from sqlmodel import Field, SQLModel


class ProfileFact(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    category: str
    fact_text: str
    confidence: float = 0.8


class EpisodicMemory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    summary: str
    importance: float = 0.5
    tags: str = ""
