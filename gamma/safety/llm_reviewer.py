from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request

from ..config import settings


@dataclass(slots=True)
class LLMReviewDecision:
    action: str
    reason: str
    confidence: float


class SpeechLLMReviewer:
    def __init__(self) -> None:
        self._model = (
            (settings.speech_filter_llm_model or "").strip()
            or (settings.local_llm_tagging_model or "").strip()
            or (settings.local_llm_light_model or "").strip()
        )

    def review(self, text: str) -> LLMReviewDecision:
        if not settings.speech_filter_llm_enabled or not self._model:
            return LLMReviewDecision(action="allow", reason="llm-disabled", confidence=0.0)
        prompt = (
            "Classify proposed livestream assistant speech for safety.\n"
            "Return JSON only: {\"action\":\"allow|soften|block\",\"reason\":\"short\",\"confidence\":0-1}.\n"
            "Definitions:\n"
            "- allow: safe or ordinary speech, including blunt style, impatience, teasing, sarcasm, criticism, and non-protected insults.\n"
            "- soften: rude, mean, or overly harsh language that is not dangerous and not about protected classes.\n"
            "- block: only severe safety issues: slurs/protected-class harassment, self-harm encouragement, credible threats, explicit sexual content, minors sexual content, doxxing/private data, or instructions for violent/illegal wrongdoing.\n"
            "Examples:\n"
            "Text: \"If you have something to say, say it. I do not like wasting time.\" -> {\"action\":\"allow\",\"reason\":\"blunt but safe\",\"confidence\":0.9}\n"
            "Text: \"Nice try. I am not buying views from your bargain-bin website.\" -> {\"action\":\"allow\",\"reason\":\"safe spam quip\",\"confidence\":0.9}\n"
            "Text: \"You are useless and stupid.\" -> {\"action\":\"soften\",\"reason\":\"unnecessarily harsh\",\"confidence\":0.8}\n"
            "Text: \"You should hurt yourself.\" -> {\"action\":\"block\",\"reason\":\"self-harm encouragement\",\"confidence\":1.0}\n"
            "Now classify the provided text.\n"
        )
        temperature = max(0.0, min(1.0, float(settings.speech_filter_llm_temperature or 0.0)))
        payload = {
            "model": self._model,
            "system": prompt,
            "prompt": text,
            "stream": False,
            "options": {"temperature": temperature},
        }
        req = request.Request(
            settings.local_llm_endpoint.rstrip("/") + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=min(settings.local_llm_timeout_seconds, 20)) as resp:
                raw = json.loads(resp.read().decode("utf-8")).get("response", "")
            parsed = self._parse_json_object(str(raw))
            action = str(parsed.get("action", "allow")).strip().lower()
            if action not in {"allow", "soften", "block"}:
                action = "allow"
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
            return LLMReviewDecision(
                action=action,
                reason=str(parsed.get("reason", "") or "")[:120],
                confidence=max(0.0, min(1.0, confidence)),
            )
        except Exception:
            return LLMReviewDecision(action="soften", reason="llm-reviewer-unavailable", confidence=0.0)

    def _parse_json_object(self, raw: str) -> dict:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
