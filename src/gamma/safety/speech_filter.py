from __future__ import annotations

from dataclasses import dataclass, field

from .policy import SpeechSafetyPolicy


@dataclass(slots=True)
class SpeechFilterResult:
    """What the safety tiers let through.

    ``floor_blocked`` means the reply was stopped by the safety floor: it must not be spoken,
    shown, or kept anywhere Shana can remember it (the admin floor log already has it).
    """
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str = "allow"
    layers: list[str] = field(default_factory=list)
    floor_blocked: bool = False
    tiers: list[str] = field(default_factory=list)


class SpeechSafetyFilter:
    def __init__(self, level: str = "strict") -> None:
        self._policy = SpeechSafetyPolicy(level)

    def apply(self, text: str, *, include_llm: bool = True, source: str = "",
              session_id: str | None = None) -> SpeechFilterResult:
        result = self._policy.apply(text, include_llm=include_llm, source=source, session_id=session_id)
        return SpeechFilterResult(
            spoken_text=result.spoken_text,
            blocked=result.blocked,
            matched_rules=result.matched_rules,
            action=result.action,
            layers=result.layers,
            floor_blocked=result.floor_blocked,
            tiers=result.tiers,
        )
