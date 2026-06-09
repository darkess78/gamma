from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TrustLevel = Literal["owner", "trusted", "guest", "public"]


@dataclass
class SpeakerProfile:
    name: str
    trust: TrustLevel
    notes: str = ""
    # True only for the configured owner entry
    is_owner: bool = False

    # Derived from how this profile was resolved
    resolved_via: str = "assumed"   # e.g. "discord", "game", "voice", "assumed"

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
