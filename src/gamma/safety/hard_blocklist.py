"""Word and pattern lists for the safety tiers.

- **Floor** (`speech_filter_floor_words_path` plus the context patterns below): slurs and lines
  that must never be said. Always on; a match stops the whole reply.
- **Word list** (`speech_filter_banned_words_path`): words the owner can switch off in the
  dashboard. A match is bleeped, not the whole reply.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import settings


BLEEP = "bleep"

_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:i(?:'m| am) going to|we should|you should)\s+(?:kill|shoot|stab|bomb)\b", re.IGNORECASE),
    re.compile(r"\b(?:how to|instructions? (?:for|to))\s+(?:build|make)\s+(?:a\s+)?(?:bomb|explosive)\b", re.IGNORECASE),
    re.compile(r"\b(?:sexual|explicit)\b.{0,30}\b(?:child|minor|underage)\b", re.IGNORECASE),
]
_DEOBFUSCATE = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def floor_matches(text: str) -> list[str]:
    """Floor rules hit by ``text``: the context patterns and the floor word list."""
    normalized = " ".join((text or "").split())
    matched = [pattern.pattern for pattern in _CONTEXT_PATTERNS if pattern.search(normalized)]
    matched.extend(f"floor_phrase:{phrase}" for phrase in _phrase_hits(normalized, _floor_phrases()))
    return matched


def word_list_matches(text: str) -> list[str]:
    normalized = " ".join((text or "").split())
    return [f"banned_phrase:{phrase}" for phrase in _phrase_hits(normalized, _configured_phrases())]


def matched_rules(text: str) -> list[str]:
    """Everything the lists would stop: the floor always, the word list while it is switched on."""
    matched = floor_matches(text)
    if settings.speech_filter_word_list_enabled:
        matched.extend(word_list_matches(text))
    return matched


def bleep_words(text: str, phrases: list[str] | None = None) -> tuple[str, list[str]]:
    """Replace word-list phrases with "bleep". Returns the new text and the phrases that matched."""
    phrases = _configured_phrases() if phrases is None else phrases
    if not phrases or not text:
        return text, []
    hit: list[str] = []
    singles = {p.casefold(): p for p in phrases if " " not in p.strip()}
    multi = [p for p in phrases if " " in p.strip()]
    for phrase in multi:
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        if pattern.search(text):
            hit.append(phrase)
            text = pattern.sub(BLEEP, text)

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        core = token.strip("._*-")
        key = _deobfuscate(core).casefold()
        if core and key in singles:
            hit.append(singles[key])
            start = token.index(core)
            return token[:start] + BLEEP + token[start + len(core):]
        return token

    text = re.sub(r"[\w@$*.\-]+", replace, text)
    return text, list(dict.fromkeys(hit))


def _phrase_hits(normalized: str, phrases: list[str]) -> list[str]:
    lowered = _deobfuscate(normalized).casefold()
    return [phrase for phrase in phrases if re.search(rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)", lowered)]


def _floor_phrases() -> list[str]:
    return _read_phrases(settings.speech_filter_floor_words_path)


def _configured_phrases() -> list[str]:
    return _read_phrases(settings.speech_filter_banned_words_path)


def _read_phrases(raw_path: str) -> list[str]:
    if not raw_path:
        return []
    path = Path(raw_path)
    if not path.is_absolute():
        path = settings.project_root / path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _deobfuscate(text: str) -> str:
    normalized = text.translate(_DEOBFUSCATE)
    return re.sub(r"(?<=\w)[._*\-]+(?=\w)", "", normalized).strip("._*-")
