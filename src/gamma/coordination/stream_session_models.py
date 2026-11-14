"""Authenticated owner controls for a finite private streaming rehearsal."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Identifier = r'^[a-zA-Z0-9_-]{1,80}$'


class StreamPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    format: Literal['gameplay', 'coding']
    broad_plan: str = Field(min_length=1, max_length=1000)
    duration_seconds: int = Field(default=900, ge=60, le=14400, strict=True)
    maximum_decisions: int = Field(default=120, ge=1, le=480, strict=True)
    minimum_gap_seconds: int = Field(default=8, ge=8, le=60, strict=True)
    maximum_speech_seconds_per_minute: int = Field(default=35, ge=5, le=45, strict=True)


class RehearsalEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    event_id: str = Field(pattern=Identifier)
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    consumer_id: str = Field(pattern=Identifier)
    kind: Literal['chat', 'activity', 'lull', 'follow', 'raid']
    source: Literal['replay', 'private_adapter']
    observed_at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    text: str = Field(default='', max_length=1200)
    actor_alias: str = Field(default='', max_length=80)


class RehearsalAck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    consumer_id: str = Field(pattern=Identifier)
    output_id: str = Field(pattern=r'^stream-output-[a-f0-9]{32}$')
    generation: int = Field(ge=1, strict=True)
    revision: int = Field(ge=0, strict=True)
    status: Literal['started', 'delivered', 'failed']
    receipt_id: str = Field(pattern=Identifier)

