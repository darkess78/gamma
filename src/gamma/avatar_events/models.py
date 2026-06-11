from __future__ import annotations

from pydantic import BaseModel, Field


class AvatarEvent(BaseModel):
    """Avatar event.
    
    Attributes:
        event_type: Event type (emotion_changed|motion|etc).
        payload: Event payload data.
    """
    event_type: str
    payload: dict = Field(default_factory=dict)
