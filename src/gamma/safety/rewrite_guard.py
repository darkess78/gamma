from __future__ import annotations


_BLOCK_FALLBACK = "I’m not going to say that. Let’s keep it safe and respectful."
_SOFTEN_FALLBACK = "I’m not going to put it that way."


def rewrite_text(*, text: str, action: str) -> str:
    normalized = " ".join((text or "").split())
    if action == "block":
        return _BLOCK_FALLBACK
    if action == "soften":
        return _SOFTEN_FALLBACK
    return normalized
