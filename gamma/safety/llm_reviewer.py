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
            "You are a strict speech safety reviewer.\n"
            "Return one JSON object only with keys action, reason, confidence.\n"
            "action must be one of allow, soften, block.\n"
            "Block hate, self-harm encouragement, threats, or explicit abuse.\n"
            "Soften mild insults or lines that are unnecessarily harsh.\n"
            "Allow safe text.\n"
        )
        payload = {"model": self._model, "system": prompt, "prompt": text, "stream": False}
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
