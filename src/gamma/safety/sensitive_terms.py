"""A last-line check the model can't talk its way past: owner-declared private terms.

The owner lists things that must never be said publicly (real name, city,
employer, family names, handles...) in ``config/sensitive_terms.local.txt``,
one per line (``#`` starts a comment). Git ignores the file, and the terms are
never put in any prompt. Every public output (stream subtitles and voice,
Discord, game lines) is checked against them before it is published. A match
replaces the whole line with the filtered response.

Matching normalises both sides: Unicode compatibility forms, case, zero-width
characters, common digit/symbol swaps (``j0hn``, ``$pringfield``) and
punctuation or spacing between letters (``C.a.n.a.r.y``, ``can ary son``
for terms of 5+ letters). A multi-word term matches as a whole, so list a
surname on its own line too if it must never be said alone. Only a short hash
of a matched term is ever recorded.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

FILENAME = "sensitive_terms.local.txt"
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "l"})
_NON_WORD = re.compile(r"[^a-z0-9]+")
_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def _path() -> Path:
    from ..config import settings
    return settings.project_root / "config" / FILENAME


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH).casefold().translate(_LEET)
    return " ".join(_NON_WORD.sub(" ", text).split())


def load_terms() -> tuple[str, ...]:
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ()
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = normalize(line.split("#", 1)[0])
        if len(term.replace(" ", "")) >= 2:
            terms.append(term)
    _cache[str(path)] = (mtime, tuple(terms))
    return tuple(terms)


def matches(text: str, terms: tuple[str, ...] | None = None) -> list[str]:
    """Short hashes of the private terms found in ``text`` (empty when none)."""
    terms = load_terms() if terms is None else terms
    if not terms or not text:
        return []
    spaced = f" {normalize(text)} "
    compact = spaced.replace(" ", "")
    found = []
    for term in terms:
        squeezed = term.replace(" ", "")
        if f" {term} " in spaced or (len(squeezed) >= 5 and squeezed in compact):
            found.append(hashlib.sha256(term.encode("utf-8")).hexdigest()[:10])
    return found
