from __future__ import annotations

import re
from dataclasses import dataclass


_LIGHT_PATTERNS = [
    re.compile(r"\bidiot\b", re.IGNORECASE),
    re.compile(r"\bstupid\b", re.IGNORECASE),
    re.compile(r"\bdumb\b", re.IGNORECASE),
]


@dataclass(slots=True)
class HeuristicDecision:
    action: str
    matched_rules: list[str]


def review(*, text: str, level: str) -> HeuristicDecision:
    normalized = " ".join((text or "").split())
    matched = [pattern.pattern for pattern in _LIGHT_PATTERNS if pattern.search(normalized)]
    if not matched:
        return HeuristicDecision(action="allow", matched_rules=[])
    if level == "strict":
        return HeuristicDecision(action="block", matched_rules=matched)
    return HeuristicDecision(action="allow", matched_rules=matched)
