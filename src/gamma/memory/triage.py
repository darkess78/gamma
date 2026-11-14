"""Deciding whether a memory candidate is worth keeping.

Not everything said is a memory. Automatic candidates (from the model's
metadata pass or the rule-based builder) go through two steps before storage:

1. ``obvious_skip``: a cheap filter for candidates that are clearly not
   memories: greetings and reactions, fragments, Shana's own reply text, and
   instructions for the current turn only. It is checked against the owner's
   labelled dataset (research/cs4700_memory_triage) to never drop a labelled
   "keep".
2. A ``Judge`` for everything else. The keep/skip rules in the owner's
   labelling guide need judgment that simple patterns can't make (many "skip"
   examples read like durable facts but are scoped to one release, one
   attempt or one audience), so the judge is pluggable:
   - ``KeepJudge``: keeps everything that passes step 1 (the default today);
   - ``ModelJudge``: asks a small local model with the guide's rules;
   - a trained classifier from the class project can implement ``Judge`` later.

Explicit "remember this" saves skip both steps, as the guide treats them separately.
``python -m gamma.evals.memory_triage`` scores any judge on the labelled dataset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

_REACTION = re.compile(
    r"^\s*(?:lol|lmao|haha+|nice|cool|great|awesome|thanks?(?: you)?|ok(?:ay)?|sure|yes|no|yeah|nope|wow|"
    r"hi|hello|hey|good (?:morning|night)|that sounds (?:great|good|fun)|sounds good)\b[\s.!?]*$", re.I)
_ASSISTANT = re.compile(r"^\s*(?:shana|the assistant|assistant)\s+(?:said|replied|answered|responded|told|"
                        r"suggested|thinks)\b", re.I)
_CURRENT_TURN = re.compile(
    r"\b(?:for this (?:response|reply|answer|message|screenshot)|just this once|in this (?:reply|response|answer)|"
    r"do not summarize this)\b", re.I)


@dataclass(frozen=True)
class Verdict:
    keep: bool
    reason: str


def obvious_skip(text: str) -> Verdict | None:
    """A skip verdict for candidates that are clearly not memories, else None."""
    text = " ".join((text or "").split())
    words = re.findall(r"[a-z0-9']+", text.lower())
    if len(words) < 2:  # "I like tea" is a real memory; a lone word isn't
        return Verdict(False, "fragment")
    if _REACTION.match(text):
        return Verdict(False, "casual_reaction")
    if _ASSISTANT.match(text):
        return Verdict(False, "assistant_reaction")
    if _CURRENT_TURN.search(text):
        return Verdict(False, "current_turn_only")
    return None


class Judge(Protocol):
    def judge(self, text: str) -> Verdict: ...


class KeepJudge:
    def judge(self, text: str) -> Verdict:
        return Verdict(True, "kept_without_judge")


JUDGE_GUIDE = """You decide whether a memory candidate is worth keeping for a SEPARATE future conversation.
Keep: lasting preferences or dislikes; accessibility or communication preferences; ongoing project goals,
durable project facts and reusable constraints; recurring workflows or rules; stable, safe relationship facts;
lasting boundaries or permissions; meaningful milestones; durable corrections.
Skip: short-lived states (mood, hunger, weather); reactions and greetings; passed one-off appointments or
details; vague references that need the conversation; instructions for the current turn only; outdated or
superseded information; Shana's own reply text; speculation. Also skip facts explicitly limited to one release,
one attempt, one document, one audience or one encounter, and choices made by a system rather than the user.
Project-specific rules must name the project to be kept.
Answer with one word: keep or skip."""


class ModelJudge:
    """A small local model applying the labelling guide (run it off the reply path)."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def judge(self, text: str) -> Verdict:
        reply = self.adapter.generate_reply(system_prompt=JUDGE_GUIDE, user_text=f"Candidate: {text}")
        answer = getattr(reply, "text", str(reply)).strip().lower()
        return Verdict(answer.startswith("keep"), "model_judge")


def triage(text: str, judge: Judge | None = None) -> Verdict:
    return obvious_skip(text) or (judge or KeepJudge()).judge(text)
