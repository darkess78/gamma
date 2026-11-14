"""Small helpers shared by the conversation modules: recipient normalization, the memory-candidate view, allowed emotions and code-fence cleanup."""
from __future__ import annotations

import re

from ..errors import ConversationError
from ..schemas.conversation import RecipientContext
from ..schemas.response import MemoryCandidate


ALLOWED_EMOTIONS: set[str] = {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"}


_CODE_FENCE_RE = re.compile(r"```+")


_INLINE_TICK_RE = re.compile(r"`([^`]*)`")


def _recipient_context(value: RecipientContext | None, *, delivery_context: str | None = None) -> RecipientContext:
    if value is None:
        value = RecipientContext()
    if type(value) is not RecipientContext:
        raise ConversationError("recipient_context_must_be_internal")
    if value.allows_private_memory and delivery_context == "public_stream":
        raise ConversationError("private_recipient_conflicts_with_public_delivery")
    return value


def _memory_candidate_view(
    candidates: list[MemoryCandidate], *, recipient: RecipientContext | None = None,
) -> list[MemoryCandidate]:
    if not _recipient_context(recipient).allows_private_memory:
        return []
    return [candidate.model_copy(deep=True) for candidate in candidates]
