from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TrustLevel = Literal["owner", "trusted", "guest", "public"]


@dataclass
class SpeakerProfile:
    """Speaker identity profile with trust level.
    
    Attributes:
        name: Speaker name.
        trust: Trust level (owner|trusted|guest|public).
        notes: Speaker notes.
        is_owner: True only for the configured owner entry.
        resolved_via: How profile was resolved (assumed|discord|game|voice).
    
    Properties:
        memory_write_allowed: True if trust is owner or trusted.
        memory_read_allowed: True if trust is owner or trusted.
        tools_allowed: True if trust is owner or trusted.
        subject_type: Memory subject_type ('primary_user'|'other_person').
    
    Methods:
        describe: Return profile description string.
    """
    name: str
    trust: TrustLevel
    notes: str = ""
    resolved_via: str = "assumed"
    is_owner: bool = False

    @property
    def memory_write_allowed(self) -> bool:
        return self.trust in ("owner", "trusted")

    @property
    def memory_read_allowed(self) -> bool:
        return self.trust in ("owner", "trusted")

    @property
    def tools_allowed(self) -> bool:
        return self.trust in ("owner", "trusted")

    @property
    def subject_type(self) -> str:
        """Maps to the memory subject_type used in MemoryCandidate."""
        return "primary_user" if self.is_owner else "other_person"

    def describe(self) -> str:
        lines = [f"Name: {self.name}", f"Trust: {self.trust}"]
        if self.resolved_via != "assumed":
            lines.append(f"Identified via: {self.resolved_via}")
        if self.notes:
            lines.append(f"Note: {self.notes}")
        return "\n".join(lines)


# Singleton used when no speaker context is provided (local solo use).
UNKNOWN_PUBLIC = SpeakerProfile(
    name="Unknown",
    trust="public",
    notes="Unrecognized speaker. Be polite but distant. Do not share personal information or persist memory.",
    resolved_via="unresolved",
)
