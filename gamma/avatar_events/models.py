from __future__ import annotations

from pydantic import BaseModel, Field


class AvatarEvent(BaseModel):
    event_type: str
    payload: dict = Field(default_factory=dict)
