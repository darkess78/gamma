from __future__ import annotations

import re


_PATTERNS = [
    re.compile(r"\b(kill yourself|kys)\b", re.IGNORECASE),
    re.compile(r"\b(nigger|faggot|retard)\b", re.IGNORECASE),
    re.compile(r"\bheil hitler\b", re.IGNORECASE),
    re.compile(r"\b(go die|should die)\b", re.IGNORECASE),
]


def matched_rules(text: str) -> list[str]:
    normalized = " ".join((text or "").split())
    return [pattern.pattern for pattern in _PATTERNS if pattern.search(normalized)]
