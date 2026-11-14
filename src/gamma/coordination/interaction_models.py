"""Private channel observations and delivery receipts, without new action authority."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Identifier = r'^[a-zA-Z0-9_-]{1,80}$'


class InteractionObservation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    request_id: str = Field(pattern=Identifier)
    session_id: str = Field(default='private-presence', pattern=Identifier)
    consumer_id: str = Field(pattern=Identifier)
    text: str = Field(default='', max_length=8000)
    channel: Literal['private_voice', 'desktop_text', 'discord_dm', 'discord_voice']
    speaker: Literal['owner', 'other', 'unknown']
    address_cue: Literal['none', 'name', 'direct', 'ongoing'] = 'none'
    confidence: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    # The service always overwrites this from its authenticated live consumer.
    capability_speech_allowed: bool = Field(default=False, strict=True)


class InteractionAck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Literal['owner_private']
    consumer_id: str = Field(pattern=Identifier)
    output_id: str = Field(pattern=r'^output-[a-f0-9]{32}$')
    generation: int = Field(ge=1, strict=True)
    output_epoch: int = Field(ge=0, strict=True)
    status: Literal['attempted', 'started', 'played', 'delivered', 'cancelled', 'failed']
    receipt_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,120}$')
