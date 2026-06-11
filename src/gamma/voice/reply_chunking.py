from __future__ import annotations

import re


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_QUESTION_OPENER_RE = re.compile(
    r"^(?:who|what|when|where|why|how|which|is|are|do|does|did|can|could|would|should|will|have|has)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK_RE = re.compile(r"(?<=[,;:—-])\s+")


def split_reply_text(reply_text: str, *, max_chunks: int = 2) -> list[str]:
    text = " ".join(reply_text.strip().split())
    if not text:
        return []
    if max_chunks <= 1:
        return [text]

    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    sentences = _split_long_units(sentences or [text])
    if len(sentences) <= 1:
        return [text]
    if max_chunks == 2:
        return _split_into_two_chunks(sentences, full_text=text)
    return _split_into_multiple_chunks(sentences, full_text=text, max_chunks=max_chunks)


def _too_short(text: str) -> bool:
    words = text.split()
    if _is_brief_question_opener(text, words):
        return False
    return len(words) <= 2 or len(text) < 12


def _is_brief_question_opener(text: str, words: list[str] | None = None) -> bool:
    if not text.endswith("?"):
        return False
    if words is None:
        words = text.split()
    if len(words) > 5:
        return False
    return bool(_QUESTION_OPENER_RE.match(text.strip()))


def _split_into_two_chunks(sentences: list[str], *, full_text: str) -> list[str]:
    if len(sentences) == 2:
        first, second = sentences
        if _too_short(first):
            return [full_text]
        return [first, second]

    if len(sentences) >= 3:
        # Prefer a one-sentence opener when it is substantial enough.
        # This improves time-to-first-audio for live speech.
        first_chunk = sentences[0].strip()
        second_chunk = " ".join(sentences[1:]).strip()
        if _too_short(first_chunk):
            first_chunk = " ".join(sentences[:2]).strip()
            second_chunk = " ".join(sentences[2:]).strip()
        if _too_short(first_chunk):
            first_chunk = " ".join(sentences[:3]).strip()
            second_chunk = " ".join(sentences[3:]).strip()
        if not second_chunk:
            return [full_text]
        return [first_chunk, second_chunk]

    return [full_text]


def _split_into_multiple_chunks(sentences: list[str], *, full_text: str, max_chunks: int) -> list[str]:
    if max_chunks <= 2:
        return _split_into_two_chunks(sentences, full_text=full_text)

    first_chunk = sentences[0].strip()
    start_index = 1
    if _too_short(first_chunk) and len(sentences) >= 2:
        first_chunk = " ".join(sentences[:2]).strip()
        start_index = 2
    if _too_short(first_chunk) and len(sentences) >= 3:
        first_chunk = " ".join(sentences[:3]).strip()
        start_index = 3

    remaining = sentences[start_index:]
    if not remaining:
        return [full_text]

    slots = max_chunks - 1
    remaining_count = len(remaining)
    if slots <= 0 or remaining_count <= 0:
        return [first_chunk] if first_chunk else [full_text]

    base = remaining_count // slots
    extra = remaining_count % slots
    chunks = [first_chunk]
    index = 0
    for slot in range(slots):
        take = base + (1 if slot < extra else 0)
        if take <= 0:
            continue
        chunk = " ".join(remaining[index:index + take]).strip()
        index += take
        if chunk:
            chunks.append(chunk)

    return [chunk for chunk in chunks if chunk] or [full_text]


def _split_long_units(units: list[str]) -> list[str]:
    split_units: list[str] = []
    for unit in units:
        text = unit.strip()
        if not text:
            continue
        words = text.split()
        if len(words) <= 16:
            split_units.append(text)
            continue

        clauses = [part.strip() for part in _CLAUSE_BREAK_RE.split(text) if part.strip()]
        if len(clauses) <= 1:
            split_units.append(text)
            continue

        current = clauses[0]
        for clause in clauses[1:]:
            candidate = (current + " " + clause).strip()
            if len(candidate.split()) <= 12:
                current = candidate
            else:
                split_units.append(current)
                current = clause
        if current:
            split_units.append(current)

    return [unit for unit in split_units if unit]
