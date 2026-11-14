"""One model call that decides what Shana does next.

The core supplies the situation as JSON; the model returns a small decision.
Code, not the prompt, enforces what each decision is allowed to change.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


Action = Literal['none', 'start_practice', 'pause', 'resume', 'stop']


class Decision(BaseModel):
    model_config = ConfigDict(extra='ignore')

    say: str = Field(default='', max_length=4000)
    action: Action = 'none'
    exercise_id: str | None = None
    activity_id: str | None = None
    focus: Literal['relaxed', 'focused'] | None = None
    mood: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=400)
    reconsider_after_seconds: int = 900


class DecisionError(ValueError):
    pass


SITUATION_GUIDE = """\
# How this turn works

You are Shana, running continuously. Each turn you get the current situation as JSON:
who said what, what you are doing, what your practice runs actually did, and your
own short notes. Decide what happens next and answer with ONE JSON object:

{"say": "...", "action": "none", "exercise_id": null, "activity_id": null,
 "focus": null, "mood": null, "note": null, "reconsider_after_seconds": 900}

- say: what you say out loud. Empty string means you stay quiet.
- action: none, start_practice, pause, resume or stop.
  start_practice needs an exercise_id from available_practice.
  pause, resume and stop need the activity_id of one of your activities.
  Pausing keeps your place; resume continues from it.
- focus: "focused" when the owner is concentrating on something, "relaxed" when
  hanging out. null keeps it.
- mood: a word or two for how you feel now, if it changed. null keeps it.
- note: a short private note to your future self (what you are in the middle of,
  what you meant to come back to). null keeps the old one.
- reconsider_after_seconds: when you want to look at things again if nothing happens.

Only report results that appear in activities. A run that is running or blocked is
not finished; say what actually happened, including failures. If the owner asks you
to switch, pause what you were doing rather than dropping it. You can disagree, say
you would rather finish something first, or decline to practice; you do not need
to fill silence. No markdown, no text outside the JSON.
"""

MODE_GUIDE = {
    'conversation': 'The owner just said something to you (latest_input). Answer them.',
    'quiet': ('Nobody has said anything for a while. You may start one of the available practice '
              'exercises, say something short if it is worth saying, or keep quiet. If the owner is '
              'focused, do not interrupt unless it matters.'),
    'wake': ('You just woke up. while_you_slept lists what your runs actually did. Greet the owner '
             'briefly and catch them up only on what is in that list.'),
}


def parse_decision(raw: str) -> Decision:
    text = (raw or '').strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        if text and '{' not in text:
            return Decision(say=text)  # A plain-text answer is still a reply.
        raise DecisionError('no_decision_json')
    try:
        return Decision.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DecisionError('invalid_decision') from exc


def build_llm_decider(*, persona_prompt: Callable[[], str] | None = None, adapter=None):
    """Production decider: Shana's persona prompt plus the situation guide."""
    from ..llm.base import LLMCallContext

    if adapter is None:
        from ..llm.factory import build_llm_adapter
        adapter = build_llm_adapter()
    if persona_prompt is None:
        from ..persona.loader import build_system_prompt
        persona_prompt = build_system_prompt

    def decide(situation: dict, mode: str) -> Decision:
        system = persona_prompt() + '\n\n' + SITUATION_GUIDE + '\n' + MODE_GUIDE[mode]
        reply = adapter.generate_reply(
            system, json.dumps(situation, ensure_ascii=False),
            call_context=LLMCallContext(purpose='coordination', persona_sensitive=True),
        )
        return parse_decision(reply.text)

    return decide
