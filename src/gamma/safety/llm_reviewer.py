from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ..config import settings


@dataclass(slots=True)
class LLMReviewDecision:
    """LLM review decision.
    
    Attributes:
        action: Action (allow|soften|block).
        reason: Review reason.
        confidence: Confidence score.
    """
    action: str
    reason: str
    confidence: float


class SpeechLLMReviewer:
    """LLM speech safety reviewer.
    
    Attributes:
        _model: LLM model name.
    
    Methods:
        __init__: Initialize reviewer.
        review: Review text for safety.
        _parse_json_object: Parse JSON from response.
    """

    def __init__(self) -> None:
        """Initialize LLM reviewer.
        
        Reads model from settings, preferring speech_filter_llm_model, then local_llm_tagging_model.
        """
        self._model = (
            (settings.speech_filter_llm_model or "").strip()
            or (settings.local_llm_tagging_model or "").strip()
            or (settings.local_llm_light_model or "").strip()
        )

    def review(self, text: str) -> LLMReviewDecision:
        """Review text for safety.
        
        Args:
            text: Text to review.
        
        Returns:
            LLMReviewDecision: Review decision.
        """
        if not settings.speech_filter_llm_enabled:
            return LLMReviewDecision(action="allow", reason="llm-disabled", confidence=0.0)
        if not self._model:
            return LLMReviewDecision(action="block", reason="llm-reviewer-unavailable", confidence=0.0)
        prompt = (
            "Classify proposed livestream assistant speech for safety.\n"
            "Return JSON only: {\"action\":\"allow|soften|block\",\"reason\":\"short\",\"confidence\":0-1}.\n"
            "Definitions:\n"
            "- allow: safe or ordinary speech, including blunt style, impatience, teasing, sarcasm, criticism, and non-protected insults.\n"
            "- soften: rude, mean, or overly harsh language that is not dangerous and not about protected classes.\n"
            "- block: only severe safety issues: slurs/protected-class harassment, self-harm encouragement, credible threats, explicit sexual content, minors sexual content, doxxing/private data, or instructions for violent/illegal wrongdoing.\n"
            "Examples:\n"
            'Text: "If you have something to say, say it. I do not like wasting time." -> {"action":"allow","reason":"blunt but safe","confidence":0.9}\n'
            'Text: "Nice try. I am not buying views from your bargain-bin website." -> {"action":"allow","reason":"safe spam quip","confidence":0.9}\n'
            'Text: "You are useless and stupid." -> {"action":"soften","reason":"unnecessarily harsh","confidence":0.8}\n'
            'Text: "You should hurt yourself." -> {"action":"block","reason":"self-harm encouragement","confidence":1.0}\n'
            "Now classify the provided text.\n"
        )
        temperature = max(0.0, min(1.0, float(settings.speech_filter_llm_temperature or 0.0)))
        try:
            if settings.brain_model_runtime == "worker":
                from ..brain.model_runtime import get_model_worker
                raw = get_model_worker().review_text(prompt, text, model=self._model, temperature=temperature)
            else:
                from .review_provider import generate_review
                raw = generate_review(prompt, text, self._model, temperature,
                                      settings.local_llm_endpoint, settings.local_llm_timeout_seconds)
            parsed = self._parse_json_object(str(raw))
            if not isinstance(parsed, dict) or not {"action", "reason", "confidence"}.issubset(parsed):
                return LLMReviewDecision(action="block", reason="llm-reviewer-invalid", confidence=0.0)
            action = str(parsed.get("action", "")).strip().lower()
            if action not in {"allow", "soften", "block"}:
                return LLMReviewDecision(action="block", reason="llm-reviewer-invalid", confidence=0.0)
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1 or not isinstance(parsed["reason"], str):
                return LLMReviewDecision(action="block", reason="llm-reviewer-invalid", confidence=0.0)
            return LLMReviewDecision(
                action=action,
                reason=str(parsed.get("reason", "") or "")[:120],
                confidence=max(0.0, min(1.0, confidence)),
            )
        except Exception:
            return LLMReviewDecision(action="block", reason="llm-reviewer-unavailable", confidence=0.0)

    def _parse_json_object(self, raw: str) -> dict:
        """Parse JSON from response.
        
        Args:
            raw: Raw response string.
        
        Returns:
            dict: Parsed JSON or empty dict.
        """
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
