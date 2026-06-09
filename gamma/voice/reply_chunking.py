from __future__ import annotations

import re


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_QUESTION_OPENER_RE = re.compile(
    r"^(?:who|what|when|where|why|how|which|is|are|do|does|did|can|could|would|should|will|have|has)\b",
    re.IGNORECASE,
)


def split_reply_text(reply_text: str, *, max_chunks: int = 2) -> list[str]:
    text = " ".join(reply_text.strip().split())
    if not text:
        return []
    if max_chunks <= 1:
        return [text]

    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    if len(sentences) <= 1:
        return [text]
    if max_chunks == 2:
        return _split_into_two_chunks(sentences, full_text=text)

    chunks = [" ".join(sentences).strip()]
    return [chunk for chunk in chunks if chunk] or [text]


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
