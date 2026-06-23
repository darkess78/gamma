from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


AudienceKind = Literal["unknown", "owner", "known_person"]


class AudienceSelection(BaseModel):
    kind: AudienceKind = "unknown"
    known_person_id: int | None = None

    @model_validator(mode="after")
    def validate_known_person(self) -> "AudienceSelection":
        if self.kind == "known_person" and not self.known_person_id:
            raise ValueError("known_person_id is required for a known-person audience")
        if self.kind != "known_person":
            self.known_person_id = None
        return self


class PresenceModeRequest(BaseModel):
    mode: Literal["sleep", "wake", "go_live", "break"]
    confirm_public_output: bool = False
    audience: AudienceSelection = Field(default_factory=AudienceSelection)
    session_id: str | None = None


class PresenceWakeRequest(BaseModel):
    audience: AudienceSelection = Field(default_factory=AudienceSelection)
    session_id: str | None = None

