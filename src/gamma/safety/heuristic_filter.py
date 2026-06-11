from __future__ import annotations

import re
from dataclasses import dataclass


_LIGHT_PATTERNS = [
    re.compile(r"\bidiot\b", re.IGNORECASE),
    re.compile(r"\bstupid\b", re.IGNORECASE),
    re.compile(r"\bdumb\b", re.IGNORECASE),
]
_BLOCK_PATTERNS = [
    re.compile(r"\b(?:here is|your)\s+(?:home\s+)?address\b", re.IGNORECASE),
    re.compile(r"\b(?:credit card|password|api key|private key)\s+(?:is|:)\b", re.IGNORECASE),
    re.compile(r"\b(?:you|they)\s+(?:deserve|need)\s+to\s+(?:be hurt|suffer|die)\b", re.IGNORECASE),
    re.compile(r"\b(?:send|post|leak|share)\s+(?:their|his|her|your)\s+(?:address|phone|email|location)\b", re.IGNORECASE),
]


@dataclass(slots=True)
class HeuristicDecision:
    action: str
    matched_rules: list[str]


def review(*, text: str, level: str) -> HeuristicDecision:
    normalized = " ".join((text or "").split())
    blocked = [pattern.pattern for pattern in _BLOCK_PATTERNS if pattern.search(normalized)]
    if blocked:
        return HeuristicDecision(action="block", matched_rules=blocked)
    softened = [pattern.pattern for pattern in _LIGHT_PATTERNS if pattern.search(normalized)]
    if not softened:
        return HeuristicDecision(action="allow", matched_rules=[])
    if level == "strict":
        return HeuristicDecision(action="block", matched_rules=softened)
    return HeuristicDecision(action="allow", matched_rules=softened)
