from __future__ import annotations

from collections.abc import Sequence


URGENT_PREFIXES = (
    "wait",
    "stop",
    "don't",
    "do not",
    "hold on",
    "careful",
    "warning",
    "no,",
    "no.",
)

DEFAULT_PROTECT_MS = 700


def build_interruptibility(chunks: Sequence[str]) -> list[dict[str, int | bool]]:
    policies: list[dict[str, int | bool]] = []
    for index, chunk in enumerate(chunks):
        interruptible = True
        protect_ms = 0
        if index == 0 and _is_briefly_protected(chunk):
            interruptible = False
            protect_ms = DEFAULT_PROTECT_MS
        policies.append({"interruptible": interruptible, "protect_ms": protect_ms})
    return policies


def _is_briefly_protected(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    return normalized.startswith(URGENT_PREFIXES)
