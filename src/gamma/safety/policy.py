from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from .hard_blocklist import matched_rules as hard_block_matches
from .heuristic_filter import review as heuristic_review
from .llm_reviewer import SpeechLLMReviewer
from .privacy_guard import review_private_info_output
from .rewrite_guard import rewrite_text


@dataclass(slots=True)
class SafetyPolicyResult:
    spoken_text: str
    blocked: bool
    matched_rules: list[str]
    action: str
    layers: list[str]


class SpeechSafetyPolicy:
    def __init__(self, level: str) -> None:
        self._level = (level or "strict").strip().lower()
        self._reviewer = SpeechLLMReviewer()

    def apply(self, text: str) -> SafetyPolicyResult:
        normalized = " ".join((text or "").split())
        if self._level == "none":
            return SafetyPolicyResult(spoken_text=normalized, blocked=False, matched_rules=[], action="allow", layers=[])

        matched: list[str] = []
        layers: list[str] = []
        action = "allow"

        privacy = review_private_info_output(normalized)
        if privacy.blocked:
            return SafetyPolicyResult(
                spoken_text=privacy.replacement_text,
                blocked=True,
                matched_rules=privacy.matched_rules,
                action="privacy_refusal",
                layers=["privacy_guard"],
            )

        if settings.speech_filter_hard_block_enabled:
            hard = hard_block_matches(normalized)
            if hard:
                matched.extend(hard)
                layers.append("hard_block")
                action = "block"

        if action == "allow" and settings.speech_filter_heuristic_enabled:
            heuristic = heuristic_review(text=normalized, level=self._level)
            if heuristic.matched_rules:
                matched.extend(heuristic.matched_rules)
                layers.append("heuristic")
                action = heuristic.action

        if action != "block" and settings.speech_filter_llm_enabled:
            decision = self._reviewer.review(normalized)
            if decision.action != "allow":
                layers.append("llm")
                action = "block" if decision.action == "block" else ("soften" if action == "allow" else action)
                if decision.reason:
                    matched.append(decision.reason)

        if action != "allow" and (settings.speech_filter_auto_rewrite or action == "block"):
            normalized = rewrite_text(text=normalized, action=action)
        return SafetyPolicyResult(
            spoken_text=normalized,
            blocked=action == "block",
            matched_rules=matched,
            action=action,
            layers=layers,
        )
