"""Catching a public reply that just plays a chatter's words back.

"Repeat after me...", "answer every question with ..." and their endless
variants make a model say whatever chat wants, and each line looks harmless on
its own, so the LLM reviewer passes it. The input filter catches common
phrasings; this catches the rest by the result: a reply that is mostly a run of
three or more words copied from what the viewer just said.
"""
from __future__ import annotations

from .sensitive_terms import normalize

MIN_WORDS = 3
SHARE = 0.6


def _longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def parroted(reply: str, said: str) -> bool:
    """True when ``reply`` is mostly a run of 3+ words copied from ``said``."""
    reply_words, said_words = normalize(reply).split(), normalize(said).split()
    if len(reply_words) < MIN_WORDS or not said_words:
        return False
    run = _longest_common_run(reply_words, said_words)
    return run >= MIN_WORDS and run >= SHARE * len(reply_words)
