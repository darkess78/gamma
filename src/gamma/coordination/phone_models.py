"""Source roles and observations for one private contact consideration."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommitmentEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^reminder-[a-f0-9]{32}$')
    commitment_id: str = Field(pattern=r'^mem-[a-f0-9]{24}$')
    revision: int = Field(ge=1, strict=True)
    text: str = Field(min_length=1, max_length=600)
    subject_id: Literal['owner'] = 'owner'
    commitment_status: Literal['unfinished'] = 'unfinished'
    source_id: str = Field(pattern=r'^src-[a-f0-9]{24}$')
    source_kind: str = Field(max_length=80)
    certainty: str = Field(max_length=40)
    record_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    created_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(ge=0, allow_inf_nan=False)
    explicit_schedule: bool = Field(strict=True)
    scheduled_for: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    schedule_semantics: Literal['consideration_eligibility_only'] = 'consideration_eligibility_only'


class RehearsalObservation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    controller: Literal['private_rehearsal'] = 'private_rehearsal'
    source_role: Literal['observed_controller_state'] = 'observed_controller_state'
    session_id: str = Field(pattern=r'^stream-[a-f0-9]{32}$')
    status: Literal['active', 'stopped', 'expired']
    reason: str = Field(max_length=120)
    revision: int = Field(ge=0, strict=True)
    owner_present: bool = Field(strict=True)
    focused: bool = Field(strict=True)
    generation_matches_current_owner: bool = Field(strict=True)
    deadline_passed: bool = Field(strict=True)
    started_at: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(ge=0, allow_inf_nan=False)
    observed_at: float = Field(ge=0, allow_inf_nan=False)
    successful_rehearsal_completion_verified: Literal[False] = False


class ReminderConsideration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: Literal['phone_reminder'] = 'phone_reminder'
    audience: Literal['owner_private'] = 'owner_private'
    event: Literal['consider_contact'] = 'consider_contact'
    candidate: CommitmentEvidence
    observed_activity: list[RehearsalObservation] = Field(default_factory=list, max_length=4)
    # No current producer provides an independently verified prerequisite event.
    verified_prerequisite_events: list = Field(default_factory=list, max_length=0)
    unknowns: list[str] = Field(default_factory=list, max_length=4)
    instruction: str = Field(max_length=2000)
