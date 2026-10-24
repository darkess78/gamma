from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from .hard_blocklist import matched_rules as hard_block_matches
from .heuristic_filter import review as heuristic_review
from .llm_reviewer import LLMReviewDecision, SpeechLLMReviewer
from .privacy_guard import review_private_info_output
from .rewrite_guard import rewrite_text


@dataclass(slots=True)
class SafetyPolicyResult:
    """Safety policy result.
    
    Attributes:
        spoken_text: Filtered spoken text.
        blocked: Whether text was blocked.
        matched_rules: Matched safety rules.
        action: Action taken.
        layers: Filter layers used.
    """
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str
    layers: list[str]


class SpeechSafetyPolicy:
    """Speech safety policy.
    
    Attributes:
        _level: Policy level string.
        _reviewer: LLM reviewer instance.
    
    Methods:
        __init__: Initialize policy.
        apply: Apply policy to text.
    """

    def __init__(self, level: str) -> None:
        """Initialize policy.
        
        Args:
            level: Policy level (none|minimal|comfortable|strict|safe).
        """
        self._level = (level or "strict").strip().lower()
        self._reviewer = SpeechLLMReviewer()

    def apply(self, text: str, *, include_llm: bool = True) -> SafetyPolicyResult:
        """Apply policy to text.
        
        Args:
            text: Input text.
            include_llm: Include LLM safety review.
        
        Returns:
            SafetyPolicyResult: Policy result with spoken_text, blocked, matched_rules, action, layers.
        """
        normalized = " ".join((text or "").split())
        matched: list[str] = []
        layers: list[str] = []
        action = "allow"

        privacy = review_private_info_output(normalized)
        if privacy.blocked:
            normalized = privacy.replacement_text
            matched.extend(privacy.matched_rules)
            layers.append("privacy_guard")
            action = "privacy_refusal"

        if settings.speech_filter_hard_block_enabled:
            hard = hard_block_matches(normalized)
            if hard:
                matched.extend(hard)
                layers.append("hard_block")
                action = "block"

        if action == "allow" and self._level != "none" and settings.speech_filter_heuristic_enabled:
            heuristic = heuristic_review(text=normalized, level=self._level)
            if heuristic.matched_rules:
                matched.extend(heuristic.matched_rules)
                layers.append("heuristic")
                action = heuristic.action

        if action not in {"allow", "privacy_refusal"} and (settings.speech_filter_auto_rewrite or action == "block"):
            normalized = rewrite_text(text=normalized, action=action)

        if include_llm and settings.speech_filter_llm_enabled:
            # Every externally returned string, including a fast-rule refusal,
            # retains the required review gate.
            try:
                decision = self._reviewer.review(normalized)
            except Exception:
                decision = None
            if (not isinstance(decision, LLMReviewDecision) or decision.action not in {"allow", "soften", "block"}
                    or not isinstance(decision.reason, str)
                    or decision.reason in {"llm-reviewer-invalid", "llm-reviewer-unavailable", "llm-disabled"}
                    or type(decision.confidence) not in {int, float} or not 0 <= decision.confidence <= 1):
                return SafetyPolicyResult(spoken_text="", blocked=True, matched_rules=["required_review_unavailable"],
                                          action="review_unavailable", layers=["llm"])
            if decision.action != "allow":
                return SafetyPolicyResult(spoken_text="", blocked=True,
                    matched_rules=[decision.reason] if decision.reason else [],
                    action="review_denied", layers=[*layers, "llm"])

        return SafetyPolicyResult(
            spoken_text=normalized,
            blocked=action in {"block", "privacy_refusal"},
            matched_rules=matched,
            action=action,
            layers=layers,
        )
