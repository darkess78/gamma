from __future__ import annotations

from dataclasses import dataclass, field

from .policy import SpeechSafetyPolicy


@dataclass(slots=True)
class SpeechFilterResult:
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str = "allow"
    layers: list[str] = field(default_factory=list)


class SpeechSafetyFilter:
    def __init__(self, level: str = "strict") -> None:
        self._policy = SpeechSafetyPolicy(level)

    def apply(self, text: str) -> SpeechFilterResult:
        result = self._policy.apply(text)
        return SpeechFilterResult(
            spoken_text=result.spoken_text,
            blocked=result.blocked,
            matched_rules=result.matched_rules,
            action=result.action,
            layers=result.layers,
        )
