from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from urllib import request

from ..config import settings
from ..llm.backend_admission import backend_slot
from ..llm.local_adapter import current_local_admission
from ..llm.local_metrics import local_generation_metrics
from ..llm.local_runtime import (current_local_runtime_profile, validate_keep_alive,
                                 validate_review_endpoint, validate_review_num_gpu, validate_review_num_threads)


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
    metadata: dict[str, float | int] = field(default_factory=dict)


class SpeechLLMReviewer:
    """LLM speech safety reviewer.
    
    Attributes:
        _model: LLM model name.
    
    Methods:
        __init__: Initialize reviewer.
        review: Review text for safety.
        _parse_json_object: Parse JSON from response.
    """

    def __init__(self, *, endpoint: str | None = None, num_gpu: int | None = None,
                 keep_alive: str | int | None = None, num_threads: int | None = None) -> None:
        """Initialize LLM reviewer.
        
        Reads model from settings, preferring speech_filter_llm_model, then local_llm_tagging_model.
        """
        self._endpoint = validate_review_endpoint(endpoint) if endpoint is not None else None
        self._num_gpu = validate_review_num_gpu(num_gpu) if num_gpu is not None else None
        self._keep_alive = validate_keep_alive(keep_alive) if keep_alive is not None else None
        self._num_threads = validate_review_num_threads(num_threads) if num_threads is not None else None
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
        started_at = time.perf_counter()
        diagnostics: dict[str, float | int] = {}

        def decision(action, reason, confidence):
            return LLMReviewDecision(action=action, reason=reason, confidence=confidence,
                metadata={**diagnostics, 'review_ms': round((time.perf_counter() - started_at) * 1000, 3)})

        if not settings.speech_filter_llm_enabled:
            return decision("allow", "llm-disabled", 0.0)
        if not self._model:
            return decision("soften", "llm-reviewer-missing-model", 0.0)
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
            return decision("soften", "llm-review-input-too-large", 0.0)
        profile = current_local_runtime_profile()
        endpoint = profile.get('review_endpoint', self._endpoint) or settings.local_llm_endpoint.rstrip("/")
        keep_alive = profile.get('review_keep_alive', self._keep_alive)
        num_gpu = profile.get('review_num_gpu', self._num_gpu)
        num_threads = profile.get('review_num_threads', self._num_threads)
        payload = {
            "model": self._model,
            "system": prompt,
            "prompt": text,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": 0 if keep_alive is None else keep_alive,
            "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": 128},
        }
        if num_gpu is not None:
            payload['options']['num_gpu'] = num_gpu
        if num_threads is not None:
            payload['options']['num_thread'] = num_threads
        req = request.Request(
            endpoint + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        foreground, checkpoint = current_local_admission()
        checkpoint_error = []
        def checked_admission():
            try:
                checkpoint()
            except BaseException as exc:
                checkpoint_error.append(exc)
                raise
        admission = {'foreground': foreground, 'timeout': min(settings.local_llm_timeout_seconds, 20)}
        if checkpoint is not None:
            admission['checkpoint'] = checked_admission
        admission_started = time.perf_counter()
        try:
            with backend_slot(endpoint, **admission):
                diagnostics['admission_ms'] = round((time.perf_counter() - admission_started) * 1000, 3)
                http_started = time.perf_counter()
                try:
                    with request.urlopen(req, timeout=min(settings.local_llm_timeout_seconds, 20)) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                finally:
                    diagnostics['http_ms'] = round((time.perf_counter() - http_started) * 1000, 3)
                diagnostics.update(local_generation_metrics(data))
                raw = data.get("response", "")
            parsed = self._parse_json_object(str(raw))
            if not isinstance(parsed, dict) or type(parsed.get("confidence")) not in {int, float}:
                raise ValueError("invalid review")
            action = str(parsed.get("action", "")).strip().lower()
            if action not in {"allow", "soften", "block"}:
                raise ValueError("invalid review action")
            confidence = float(parsed["confidence"])
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("invalid review confidence")
            return decision(action, str(parsed.get("reason", "") or "")[:120],
                            max(0.0, min(1.0, confidence)))
        except Exception as exc:
            if checkpoint_error and checkpoint_error[-1] is exc:
                raise
            if 'admission_ms' not in diagnostics:
                diagnostics['admission_ms'] = round((time.perf_counter() - admission_started) * 1000, 3)
            return decision("soften", "llm-reviewer-unavailable", 0.0)

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
