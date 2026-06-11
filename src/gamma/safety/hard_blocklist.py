from __future__ import annotations

import re
from pathlib import Path

from ..config import settings


_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:i(?:'m| am) going to|we should|you should)\s+(?:kill|shoot|stab|bomb)\b", re.IGNORECASE),
    re.compile(r"\b(?:how to|instructions? (?:for|to))\s+(?:build|make)\s+(?:a\s+)?(?:bomb|explosive)\b", re.IGNORECASE),
    re.compile(r"\b(?:sexual|explicit)\b.{0,30}\b(?:child|minor|underage)\b", re.IGNORECASE),
]


def matched_rules(text: str) -> list[str]:
    normalized = " ".join((text or "").split())
    matched = [pattern.pattern for pattern in _CONTEXT_PATTERNS if pattern.search(normalized)]
    lowered = _deobfuscate(normalized).casefold()
    for phrase in _configured_phrases():
        if re.search(rf"(?<!\w){re.escape(phrase.casefold())}(?!\w)", lowered):
            matched.append(f"banned_phrase:{phrase}")
    return matched


def _configured_phrases() -> list[str]:
    raw_path = settings.speech_filter_banned_words_path
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
    table = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
    normalized = text.translate(table)
    return re.sub(r"(?<=\w)[._*\-]+(?=\w)", "", normalized)
