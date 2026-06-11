from __future__ import annotations

from dataclasses import dataclass, field

from .policy import SpeechSafetyPolicy


@dataclass(slots=True)
class SpeechFilterResult:
    """Speech filter result.
    
    Attributes:
        spoken_text: Filtered spoken text.
        blocked: Whether text was blocked.
        matched_rules: Matched safety rules.
        action: Action taken (allow|block|rewrite).
        layers: Filter layers used.
    """
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str = "allow"
    layers: list[str] = field(default_factory=list)


class SpeechSafetyFilter:
    """Speech safety filter.
    
    Attributes:
        _policy: Underlying speech safety policy.
    
    Methods:
        __init__: Initialize filter.
        apply: Apply filter to text.
    """

    def __init__(self, level: str = "strict") -> None:
        """Initialize filter.
        
        Args:
            level: Safety filter level.
        """
        self._policy = SpeechSafetyPolicy(level)

    def apply(self, text: str, *, include_llm: bool = True) -> SpeechFilterResult:
        """Apply filter to text.
        
        Args:
            text: Input text.
            include_llm: Include LLM safety review.
        
        Returns:
            SpeechFilterResult: Filter result with spoken_text, blocked status, matched_rules, action, and layers.
        """
        result = self._policy.apply(text, include_llm=include_llm)
        return SpeechFilterResult(
            spoken_text=result.spoken_text,
            blocked=result.blocked,
            matched_rules=result.matched_rules,
            action=result.action,
            layers=result.layers,
        )
