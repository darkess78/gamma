from __future__ import annotations

import re


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


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
    return len(words) <= 2 or len(text) < 12


def _split_into_two_chunks(sentences: list[str], *, full_text: str) -> list[str]:
    if len(sentences) == 2:
        first, second = sentences
        if _too_short(first):
            return [full_text]
        return [first, second]

    if len(sentences) >= 3:
        first_chunk = " ".join(sentences[:2]).strip()
        second_chunk = " ".join(sentences[2:]).strip()
        if _too_short(first_chunk):
            first_chunk = " ".join(sentences[:3]).strip()
            second_chunk = " ".join(sentences[3:]).strip()
        if not second_chunk:
            return [full_text]
        return [first_chunk, second_chunk]

    return [full_text]
