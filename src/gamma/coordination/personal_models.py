"""Internal private decisions; the public turn schema does not accept these writes."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ..schemas.response import BoundedStateUpdates, ConversationTurnDraft, EmotionTag


class PersonalChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['remember', 'correct', 'forget', 'resolve', 'hypothesis']
    kind: Literal['fact', 'preference', 'decision', 'commitment', 'observation', 'relationship', 'hypothesis'] = 'fact'
    subject_id: str = Field(default='owner', pattern=r'^(owner|self|person-[a-f0-9]{24}|unresolved-[a-f0-9]{24})$')
    topic: str = Field(default='', max_length=100)
    text: str = Field(default='', max_length=600)
    target_id: str | None = Field(default=None, pattern=r'^(mem|src)-[a-f0-9]{24}$')


class PersonalStateUpdates(BoundedStateUpdates):
    # Absolute delivery choices; None preserves the continuing settings.
    voice_pace: float | None = Field(default=None, ge=0.90, le=1.25, strict=True, allow_inf_nan=False)
    voice_loudness_db: float | None = Field(default=None, ge=-5.0, le=5.0, strict=True, allow_inf_nan=False)
    activity_action: Literal['wait', 'practice', 'request_practice', 'start_work', 'pause', 'resume', 'stop', 'switch', 'report_status'] | None = None
    activity_run_id: str | None = Field(default=None, pattern=r'^(practice-[a-zA-Z0-9_-]{1,80}|work-[a-f0-9]{32})$')
    work_task_id: str | None = Field(default=None, pattern=r'^work-[a-f0-9]{32}$')
    personal_changes: list[PersonalChange] = Field(default_factory=list, max_length=3)
    growth_proposals: list['GrowthProposal'] = Field(default_factory=list, max_length=2)


class GrowthProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    statement: str = Field(min_length=1,max_length=600)
    kind: Literal['judgment','interest','character','skill']
    scope_target: Literal['dialogue','work'] = 'dialogue'
    scope_marker: str = Field(min_length=1,max_length=100)
    alternatives: list[str] = Field(default_factory=list,max_length=3)
    expected_outcome: str = Field(default='',max_length=500)


class PrivateTurnDraft(ConversationTurnDraft):
    # None inherits canonical Core mood; neutral deliberately selects neutral delivery.
    emotion: EmotionTag | None = None
    state_updates: PersonalStateUpdates = Field(default_factory=PersonalStateUpdates)
