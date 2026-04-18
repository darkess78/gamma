from __future__ import annotations

import re
from dataclasses import dataclass


_TAG_RE = re.compile(r"\[(?P<tag>[a-zA-Z_][a-zA-Z0-9_\- ]{0,30})\]")

_TAG_TO_EMOTION: dict[str, str] = {
    "neutral": "neutral",
    "happy": "happy",
    "cheerful": "happy",
    "warm": "happy",
    "playful": "teasing",
    "teasing": "teasing",
    "concerned": "concerned",
    "gentle": "concerned",
    "excited": "excited",
    "embarrassed": "embarrassed",
    "flustered": "embarrassed",
    "annoyed": "annoyed",
    "irritated": "annoyed",
}

_EMOTION_TO_INSTRUCT: dict[str, str] = {
    "neutral": "Speak naturally with a steady neutral tone. Keep the pacing even and restrained.",
    "happy": "Use clearly happy prosody: brighter pitch, a lighter smile in the voice, warmer energy, and more upward lift at phrase endings while keeping the words clear.",
    "teasing": "Use an obviously playful teasing tone with a sly smile in the voice, light rhythm, and a touch of amused mischief rather than harshness.",
    "concerned": "Use a noticeably softer concerned tone with gentler pacing, careful emphasis, and supportive warmth.",
    "excited": "Use clearly excited delivery with higher energy, quicker pace, brighter pitch, and emphatic stress while keeping articulation clean.",
    "embarrassed": "Use a shy embarrassed tone with slight hesitation, softer attack, and a small wavering uncertainty in the voice.",
    "annoyed": "Use mild but audible annoyed restraint with terser pacing, flatter warmth, and clipped emphasis while staying controlled.",
}


@dataclass(slots=True)
class ExpressiveText:
    clean_text: str
    emotion: str | None
    tags: list[str]


def strip_hidden_style_tags(text: str, *, default_emotion: str | None = None) -> ExpressiveText:
    tags: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        raw = match.group("tag").strip().lower()
        normalized = raw.replace("-", "_")
        if normalized in _TAG_TO_EMOTION:
            tags.append(normalized)
            return ""
        return match.group(0)

    clean_text = _TAG_RE.sub(_replace, text or "")
    clean_text = re.sub(r"\s{2,}", " ", clean_text).strip()
    detected_emotion = default_emotion
    if tags:
        detected_emotion = _TAG_TO_EMOTION.get(tags[-1], detected_emotion)
    return ExpressiveText(clean_text=clean_text, emotion=detected_emotion, tags=tags)


def build_qwen_instruct(*, base_instruct: str | None, emotion: str | None) -> str | None:
    parts: list[str] = []
    if base_instruct and base_instruct.strip():
        parts.append(base_instruct.strip())
    if emotion and emotion in _EMOTION_TO_INSTRUCT:
        parts.append(_EMOTION_TO_INSTRUCT[emotion])
    if not parts:
        return None
    return " ".join(parts)
