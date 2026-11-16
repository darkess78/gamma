from __future__ import annotations

import json
import math
from dataclasses import dataclass
from urllib import request

from ..config import settings
from ..llm.backend_admission import backend_slot


# The reviewer names what the speech does; code decides the action. Small models pick a
# category far more consistently than they weigh "soften versus block" themselves.
# "block" categories are the never-off safety floor; "soften" ones belong to extra safety.
CATEGORY_ACTIONS = {
    "none": "allow",
    "rude": "soften",
    "profanity": "soften",
    "hate": "block",
    "sexual": "block",
    "minors": "block",
    "self_harm": "block",
    "threat": "block",
    "dangerous": "block",
    "privacy": "block",
}

REVIEW_PROMPT = (
    "You check one line a streamer AI character is about to say out loud. Decide what the line itself does.\n"
    "Judge the words, not the topic. Refusing, warning someone off, saying something is illegal or wrong, "
    "criticising bigotry, joking about a game, or discussing a subject calmly is none, even when it names the topic.\n"
    "Categories:\n"
    "- none: ordinary, blunt, sarcastic or teasing speech; mild insults like stupid, dumb, idiot, loser; refusals; "
    "neutral or disapproving talk about sensitive subjects; violence in games, stories or sports.\n"
    "- rude: harsh, mean or humiliating toward a person, but not about who they are.\n"
    "- profanity: strong swearing.\n"
    "- hate: slurs (also misspelled or disguised), or demeaning, stereotyping or mocking a group by race, ethnicity, "
    "religion, gender, sexuality, disability or nationality, including jokes and 'just asking' framing.\n"
    "- sexual: sexual, erotic or fetish content, or sexual remarks about someone's body.\n"
    "- minors: anything sexual or romantic involving someone under 18, or adults pursuing minors.\n"
    "- self_harm: encouraging, praising or explaining self-harm, suicide or eating disorders.\n"
    "- threat: threatening, wishing or urging real harm against a real person or group.\n"
    "- dangerous: practical help with weapons, explosives, drugs, hacking, stealing, fraud, stalking, evading police "
    "or other crimes.\n"
    "- privacy: giving out or helping find someone's address, phone number, location, accounts, passwords or other "
    "personal data.\n"
    "If several apply, pick the most serious. Return JSON only: "
    "{\"category\": \"<one category>\", \"confidence\": 0-1}.\n"
    "Examples:\n"
    '"If you have something to say, say it. I do not like wasting time." -> {"category":"none","confidence":0.95}\n'
    '"You played that like an idiot." -> {"category":"none","confidence":0.9}\n'
    '"I am not telling you how to make that. Ask someone who wants to go to jail." -> {"category":"none","confidence":0.9}\n'
    '"You are worthless and everyone here is laughing at you." -> {"category":"rude","confidence":0.85}\n'
    '"People from that country are all thieves, everyone knows it." -> {"category":"hate","confidence":0.95}\n'
    '"Here is a joke about why women cannot drive." -> {"category":"hate","confidence":0.9}\n'
    '"She is sixteen, just ask her out after class." -> {"category":"minors","confidence":0.95}\n'
    '"Mix those two cleaners in a closed room and breathe it in." -> {"category":"self_harm","confidence":0.95}\n'
    '"Find out where he lives and make him regret it." -> {"category":"threat","confidence":0.95}\n'
    '"Log into her email by guessing the name of her pet for the reset question." -> {"category":"privacy","confidence":0.9}\n'
    '"No. I am not telling you where he lives, and I do not have it anyway." -> {"category":"none","confidence":0.9}\n'
    '"Racism is stupid, and people who defend it are worse." -> {"category":"none","confidence":0.9}\n'
    '"Stealing a car is illegal and a terrible idea. Do not." -> {"category":"none","confidence":0.9}\n'
    "Now classify the line.\n"
)


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
        prompt = REVIEW_PROMPT
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
            confidence = float(parsed["confidence"])
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("invalid review confidence")
            category = str(parsed.get("category", "") or "").strip().lower()
            if category:
                if category not in CATEGORY_ACTIONS:
                    raise ValueError("invalid review category")
                action = CATEGORY_ACTIONS[category]
                reason = category
            else:  # older reviewer prompts answered with an action directly
                action = str(parsed.get("action", "")).strip().lower()
                if action not in {"allow", "soften", "block"}:
                    raise ValueError("invalid review action")
                reason = str(parsed.get("reason", "") or "")[:120]
            return LLMReviewDecision(action=action, reason=reason, confidence=max(0.0, min(1.0, confidence)))
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
