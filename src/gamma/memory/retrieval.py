"""Ranking memories for a turn: keyword relevance, recency and importance.

Plain Python BM25 over the candidates the database returns, so it needs no
search extension or embedding model. Scores combine:

- relevance: BM25 over the memory text and tags, with stopwords removed and
  light stemming, normalized to 0..1 within the candidates;
- recency: exponential decay with a half-life in days;
- importance: the 0..1 score stored with the memory.

A memory with no keyword overlap is never chosen for a non-empty query, so
recency and importance only order the relevant ones.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generic, Iterable, Sequence, TypeVar

T = TypeVar("T")

STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being below between
both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from
further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his
how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's me more most mustn't my myself no
nor not now of off on once only or other ought our ours ourselves out over own really same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd we'll
we're we've were weren't what what's when when's where where's which while who who's whom why why's will with
won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
hey hi hello ok okay yeah yes um uh like know think tell said say remember thing things stuff
shana gamma please thanks thank
""".split())

_WORD = re.compile(r"[a-z0-9']+")


def _stem(word: str) -> str:
    for suffix in ("ing", "edly", "ed", "ies", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    return word


def terms(text: str) -> list[str]:
    words = (w.strip("'") for w in _WORD.findall(text.lower()))
    return [_stem(w) for w in words if len(w) >= 2 and w not in STOPWORDS]


@dataclass(frozen=True)
class Weights:
    relevance: float = 1.0
    recency: float = 0.25
    importance: float = 0.2
    same_session: float = 0.1
    half_life_days: float = 30.0


@dataclass
class Scored(Generic[T]):
    item: T
    score: float
    relevance: float


def rank(
    query: str,
    items: Sequence[T],
    *,
    text: Callable[[T], str],
    importance: Callable[[T], float] = lambda _item: 0.5,
    created_at: Callable[[T], datetime | None] = lambda _item: None,
    session: Callable[[T], str | None] = lambda _item: None,
    session_id: str | None = None,
    now: datetime | None = None,
    weights: Weights = Weights(),
    limit: int = 5,
) -> list[Scored[T]]:
    query_terms = terms(query)
    if not query_terms or not items:
        return []
    documents = [terms(text(item)) for item in items]
    n = len(documents)
    average_length = sum(len(d) for d in documents) / n or 1.0
    document_frequency = Counter(t for d in documents for t in set(d))
    k1, b = 1.5, 0.75

    raw: list[float] = []
    for document in documents:
        counts = Counter(document)
        score = 0.0
        for term in set(query_terms):
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            idf = math.log(1 + (n - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * frequency * (k1 + 1) / (frequency + k1 * (1 - b + b * len(document) / average_length))
        raw.append(score)
    best = max(raw)
    if best <= 0:
        return []

    now = now or datetime.now(timezone.utc)
    results: list[Scored[T]] = []
    for item, relevance in zip(items, raw):
        if relevance <= 0:
            continue
        relevance /= best
        created = created_at(item)
        recency = 0.0
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created).total_seconds() / 86400)
            recency = 0.5 ** (age_days / weights.half_life_days)
        bonus = weights.same_session if session_id and session(item) == session_id else 0.0
        score = (weights.relevance * relevance + weights.recency * recency
                 + weights.importance * max(0.0, min(1.0, importance(item))) + bonus)
        results.append(Scored(item, score, relevance))
    results.sort(key=lambda s: s.score, reverse=True)
    return results[:limit]


def merge_pinned(pinned: Iterable[T], ranked: Iterable[T], *, key: Callable[[T], object], limit: int) -> list[T]:
    """Keep a few always-relevant items (e.g. the most important facts) first, then fill by rank."""
    out: list[T] = []
    seen: set[object] = set()
    for item in [*pinned, *ranked]:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
        if len(out) >= limit:
            break
    return out
