from __future__ import annotations

from dataclasses import dataclass


_BASE_INTENSITY = {
    "neutral": 0.1,
    "happy": 0.55,
    "teasing": 0.45,
    "concerned": 0.6,
    "excited": 0.75,
    "embarrassed": 0.72,
    "annoyed": 0.68,
}


@dataclass(slots=True)
class ExtractedEmotionTurn:
    emotion: str
    intensity: float
    emotional_target: str | None
    cause_summary: str
    trigger_type: str
    relationship_effect: str
    pattern_text: str | None


def extract_emotion_turn(*, emotion: str, user_text: str, reply_text: str) -> ExtractedEmotionTurn:
    lowered = user_text.lower()
    normalized = emotion.strip().lower() if emotion else "neutral"
    intensity = _BASE_INTENSITY.get(normalized, 0.25)
    trigger_type = "general"
    relationship_effect = "none"
    pattern_text: str | None = None

    if any(term in lowered for term in ["thank", "proud", "good job", "nice", "appreciate"]):
        trigger_type = "support"
        relationship_effect = "trust_increase"
        intensity += 0.08
    elif any(term in lowered for term in ["tease", "blush", "embarrass", "feelings"]):
        trigger_type = "teasing"
        relationship_effect = "tension"
        intensity += 0.08
    elif any(term in lowered for term in ["worry", "careful", "safe", "okay", "are you alright"]):
        trigger_type = "care"
        relationship_effect = "trust_increase"
        intensity += 0.06
    elif any(term in lowered for term in ["stupid", "idiot", "worthless", "replaceable", "shut up"]):
        trigger_type = "insult"
        relationship_effect = "trust_decrease"
        intensity += 0.12
    elif any(term in lowered for term in ["sorry", "apologize"]):
        trigger_type = "repair"
        relationship_effect = "trust_increase"
        intensity += 0.05

    if trigger_type == "teasing":
        pattern_text = "She gets defensive and flustered when directly cornered about her feelings."
    elif trigger_type == "support":
        pattern_text = "She warms faster when praise is understated and specific."
    elif trigger_type == "care":
        pattern_text = "She responds strongly to quiet concern that does not feel performative."
    elif trigger_type == "insult":
        pattern_text = "She reacts badly to disrespect and being treated as disposable."

    cause_summary = " ".join(user_text.split())[:140] or "recent interaction"
    return ExtractedEmotionTurn(
        emotion=normalized,
        intensity=max(0.0, min(1.0, intensity)),
        emotional_target="user",
        cause_summary=cause_summary,
        trigger_type=trigger_type,
        relationship_effect=relationship_effect,
        pattern_text=pattern_text,
    )
