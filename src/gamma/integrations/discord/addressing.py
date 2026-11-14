"""Deciding whether something said in a Discord voice call is for Shana.

In a group call most speech is people talking to each other. Shana answers when:

- someone says her name (including the ways speech-to-text usually mishears it);
- the person she just answered keeps talking to her within FOLLOW_UP_SECONDS
  (a filler like "yeah" or "mm" doesn't count);
- it's a one-on-one call: only one other person is in the channel.

Anything else is left alone and never sent to her. ``voice_reply_mode =
"everyone"`` in ``[discord]`` restores answering every utterance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FOLLOW_UP_SECONDS = 20.0
_NAME = re.compile(r"\b(shana|shanna|shauna|shawna|shaina|shayna|shahna|sha na)\b", re.IGNORECASE)
_FILLER = re.compile(r"^\W*(yeah|yep|yes|no|nah|ok|okay|mm+|hmm+|uh+|um+|lol|haha+|right|sure|cool|nice|true|wow)\W*$",
                     re.IGNORECASE)


@dataclass(frozen=True)
class Decision:
    answer: bool
    reason: str  # named | follow_up | one_on_one | not_addressed | filler | everyone


def says_name(text: str) -> bool:
    return bool(_NAME.search(text or ""))


def decide(text: str, *, speaker_id: str, others_in_call: int, last_answered: str | None,
           last_reply_at: float | None, now: float, mode: str = "addressed") -> Decision:
    """``others_in_call`` counts the people in the channel besides Shana, the speaker included."""
    if mode == "everyone":
        return Decision(True, "everyone")
    if says_name(text):
        return Decision(True, "named")
    if _FILLER.match(text or "") or not (text or "").strip():
        return Decision(False, "filler")
    if others_in_call <= 1:
        return Decision(True, "one_on_one")
    recent = last_reply_at is not None and now - last_reply_at <= FOLLOW_UP_SECONDS
    if recent and speaker_id == last_answered:
        return Decision(True, "follow_up")
    return Decision(False, "not_addressed")
