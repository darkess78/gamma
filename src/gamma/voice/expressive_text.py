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

_TAG_TO_STYLE: dict[str, str] = {
    "soft": "soft",
    "quiet": "quiet",
    "nearby": "quiet",
    "gentle": "soft",
    "firm": "firm",
    "stern": "firm",
    "fast": "fast",
    "quick": "fast",
    "slow": "slow",
    "slower": "slow",
    "warm": "warm",
    "bright": "bright",
    "deadpan": "deadpan",
}

_EMOTION_TO_INSTRUCT: dict[str, str] = {
    "neutral": "",
    "happy": "Use a clearly happier mood through a small smile in the voice, warmer cadence, and lighter rhythm. Keep the same distance and controlled volume; do not sound louder or closer.",
    "teasing": "Use an obviously playful teasing tone with a sly smile in the voice, light rhythm, and a touch of amused mischief rather than harshness.",
    "concerned": "Use a noticeably softer concerned tone with gentler pacing, careful emphasis, and supportive warmth.",
    "excited": "Use excited delivery through quicker pace, brighter rhythm, and more animated timing while keeping articulation clean and volume controlled.",
    "embarrassed": "Use a shy embarrassed tone with slight hesitation, softer attack, and a small wavering uncertainty in the voice.",
    "annoyed": "Use mild but audible annoyed restraint with terser pacing, flatter warmth, and clipped emphasis while staying controlled.",
}

_STYLE_TO_INSTRUCT: dict[str, str] = {
    "soft": "Use a softer delivery with low vocal pressure, gentle consonants, and restrained emphasis.",
    "quiet": "Use a quiet nearby voice without projecting or sounding like stage delivery.",
    "firm": "Use a firmer controlled tone while keeping volume moderate and avoiding harshness.",
    "fast": "Use a slightly quicker pace while keeping articulation clear.",
    "slow": "Use a slightly slower pace with calmer spacing between phrases.",
    "warm": "Use a warmer tone with smooth vowels and a small smile in the voice.",
    "bright": "Use a little more brightness and lift while staying controlled.",
    "deadpan": "Use a flatter, dry delivery with minimal pitch lift.",
}


@dataclass(slots=True)
class ExpressiveText:
    clean_text: str
    emotion: str | None
    tags: list[str]
    styles: list[str]


def strip_hidden_style_tags(text: str, *, default_emotion: str | None = None) -> ExpressiveText:
    tags: list[str] = []
    styles: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        raw = match.group("tag").strip().lower()
        normalized = raw.replace("-", "_")
        if normalized in _TAG_TO_EMOTION:
            tags.append(normalized)
            return ""
        if normalized in _TAG_TO_STYLE:
            styles.append(_TAG_TO_STYLE[normalized])
            return ""
        return match.group(0)

    clean_text = _TAG_RE.sub(_replace, text or "")
    clean_text = re.sub(r"\s{2,}", " ", clean_text).strip()
    detected_emotion = default_emotion
    if tags:
        detected_emotion = _TAG_TO_EMOTION.get(tags[-1], detected_emotion)
    return ExpressiveText(clean_text=clean_text, emotion=detected_emotion, tags=tags, styles=styles)


def build_qwen_instruct(*, base_instruct: str | None, emotion: str | None, styles: list[str] | None = None) -> str | None:
    parts: list[str] = []
    if base_instruct and base_instruct.strip():
        parts.append(base_instruct.strip())
    if emotion and emotion in _EMOTION_TO_INSTRUCT and _EMOTION_TO_INSTRUCT[emotion]:
        parts.append(_EMOTION_TO_INSTRUCT[emotion])
    seen: set[str] = set()
    for style in styles or []:
        if style in seen:
            continue
        seen.add(style)
        if style in _STYLE_TO_INSTRUCT:
            parts.append(_STYLE_TO_INSTRUCT[style])
    if not parts:
        return None
    return " ".join(parts)
