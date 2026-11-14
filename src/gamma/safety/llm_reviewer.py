from __future__ import annotations

import json
import math
from dataclasses import dataclass
from urllib import request

from ..config import settings
from ..llm.backend_admission import backend_slot


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

    def __init__(self, *, model: str | None = None, endpoint: str | None = None, keep_alive: str | int | None = None) -> None:
        """Initialize LLM reviewer.

        Reads model from settings, preferring speech_filter_llm_model, then local_llm_tagging_model.
        ``model``, ``endpoint`` and ``keep_alive`` override settings (the reviewer benchmark uses them).
        """
        self._model = (model or "").strip() or (
            (settings.speech_filter_llm_model or "").strip()
            or (settings.local_llm_tagging_model or "").strip()
            or (settings.local_llm_light_model or "").strip()
        )
        self._endpoint = (endpoint or "").strip()
        self._keep_alive = keep_alive

    def endpoint(self) -> str:
        return (self._endpoint or settings.local_llm_endpoint).rstrip("/")

    def keep_alive(self) -> str | int:
        value = self._keep_alive if self._keep_alive is not None else (
            (settings.speech_filter_llm_keep_alive or "").strip() or settings.local_llm_keep_alive
        )
        return int(value) if isinstance(value, str) and value.strip().isdigit() else value

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
            return LLMReviewDecision(action="soften", reason="llm-reviewer-missing-model", confidence=0.0)
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
        if not isinstance(text, str) or len((prompt + text).encode("utf-8")) > 6000:
            return LLMReviewDecision(action="soften", reason="llm-review-input-too-large", confidence=0.0)
        payload = {
            "model": self._model,
            "system": prompt,
            "prompt": text,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": self.keep_alive(),
            "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": 128},
        }
        req = request.Request(
            self.endpoint() + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with backend_slot(self.endpoint(), foreground=True,
                              timeout=min(settings.local_llm_timeout_seconds, 20)):
                with request.urlopen(req, timeout=min(settings.local_llm_timeout_seconds, 20)) as resp:
                    raw = json.loads(resp.read().decode("utf-8")).get("response", "")
            parsed = self._parse_json_object(str(raw))
            if not isinstance(parsed, dict) or type(parsed.get("confidence")) not in {int, float}:
                raise ValueError("invalid review")
            action = str(parsed.get("action", "")).strip().lower()
            if action not in {"allow", "soften", "block"}:
                raise ValueError("invalid review action")
            confidence = float(parsed["confidence"])
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("invalid review confidence")
            return LLMReviewDecision(
                action=action,
                reason=str(parsed.get("reason", "") or "")[:120],
                confidence=max(0.0, min(1.0, confidence)),
            )
        except Exception:
            return LLMReviewDecision(action="soften", reason="llm-reviewer-unavailable", confidence=0.0)

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
