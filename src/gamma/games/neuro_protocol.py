"""Server side of the Neuro SDK game protocol, independent of transport.

One ``GameSession`` per connected game. Feed it each decoded client message
with ``receive()``; it returns the messages to send back. Choosing an action is
delegated to a ``Chooser`` so the same session works with a random test player
or with Shana's model.

Spec: https://github.com/VedalAI/neuro-sdk/blob/main/API/SPECIFICATION.md
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

CHARACTER_ID = "shana"
DISPLAY_NAME = "Shana"
RESULT_TIMEOUT_SECONDS = 20.0
MAX_FORCE_ATTEMPTS = 3
CONTEXT_LINES = 40
_ACTION_NAME = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class Force:
    query: str
    state: str
    action_names: list[str]
    ephemeral: bool
    priority: str
    silent: bool
    attempts: int = 0
    pending_id: str | None = None
    sent_at: float = 0.0
    last_error: str = ""


@dataclass(frozen=True)
class Choice:
    action: str
    data: dict[str, Any]
    say: str = ""  # what Shana says about it (spoken elsewhere; empty when silent)


class Chooser(Protocol):
    def choose(self, *, game: str, force: Force, actions: list[Action], context: list[str]) -> Choice: ...


class GameSession:
    def __init__(self, chooser: Chooser, *, clock=time.monotonic) -> None:
        self.chooser = chooser
        self.clock = clock
        self.game = ""
        self.actions: dict[str, Action] = {}
        self.context: deque[str] = deque(maxlen=CONTEXT_LINES)
        self.force: Force | None = None
        self.said: list[str] = []  # Shana's lines, for the speech/subtitle path to pick up

    # ---- client -> server -------------------------------------------------

    def receive(self, message: Any) -> list[dict[str, Any]]:
        """Handle one client message. Malformed or unknown messages are ignored (spec)."""
        if not isinstance(message, dict) or not isinstance(message.get("command"), str):
            return []
        game = message.get("game")
        if isinstance(game, str) and game.strip():
            self.game = self.game or game.strip()[:120]
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        handler = {
            "startup": self._startup,
            "context": self._context,
            "actions/register": self._register,
            "actions/unregister": self._unregister,
            "actions/force": self._force,
            "action/result": self._result,
        }.get(message["command"])
        return handler(data) if handler else []

    def _startup(self, _data: dict) -> list[dict]:
        self.actions.clear()
        self.force = None
        return [{"command": "startup", "data": {"session": {
            "sessionId": uuid.uuid4().hex, "characterId": CHARACTER_ID, "displayName": DISPLAY_NAME}}}]

    def _context(self, data: dict) -> list[dict]:
        text = data.get("message")
        if isinstance(text, str) and text.strip():
            self.context.append(text.strip()[:2000])
        return []

    def _register(self, data: dict) -> list[dict]:
        for raw in data.get("actions") or []:
            if not isinstance(raw, dict):
                continue
            name, description = raw.get("name"), raw.get("description")
            schema = raw.get("schema") if isinstance(raw.get("schema"), dict) else {}
            if isinstance(name, str) and _ACTION_NAME.match(name) and isinstance(description, str):
                self.actions[name] = Action(name, description[:2000], schema)
        return []

    def _unregister(self, data: dict) -> list[dict]:
        for name in data.get("action_names") or []:
            self.actions.pop(name, None)
        return []

    def _force(self, data: dict) -> list[dict]:
        names = [n for n in (data.get("action_names") or []) if isinstance(n, str) and n in self.actions]
        if not names or not isinstance(data.get("query"), str):
            return []
        if self.force is not None:
            return []  # one force at a time; the spec says a second one is a game bug
        self.force = Force(
            query=data["query"][:4000],
            state=str(data.get("state") or "")[:8000],
            action_names=names,
            ephemeral=bool(data.get("ephemeral_context", False)),
            priority=str(data.get("priority") or "low"),
            silent=bool(data.get("silent", False)),
        )
        return self._attempt()

    def _result(self, data: dict) -> list[dict]:
        force = self.force
        if force is None or data.get("id") != force.pending_id:
            return []  # unknown or late result
        message = str(data.get("message") or "")[:2000]
        if data.get("success") is True:
            if message:
                self.context.append(message)
            self._finish(force)
            return []
        force.last_error = message or "the game rejected that action"
        force.pending_id = None
        return self._attempt()

    # ---- server -> client -------------------------------------------------

    def tick(self) -> list[dict]:
        """Call periodically: a result that never arrives counts as a failure."""
        force = self.force
        if force and force.pending_id and self.clock() - force.sent_at > RESULT_TIMEOUT_SECONDS:
            force.last_error = "no result from the game in time"
            force.pending_id = None
            return self._attempt()
        return []

    def _attempt(self) -> list[dict]:
        force = self.force
        assert force is not None
        if force.attempts >= MAX_FORCE_ATTEMPTS:
            self.context.append(f"(Shana gave up on: {force.query[:200]} — {force.last_error})")
            self._finish(force)
            return []
        force.attempts += 1
        available = [self.actions[n] for n in force.action_names if n in self.actions]
        if not available:
            self._finish(force)
            return []
        choice = self.chooser.choose(game=self.game, force=force, actions=available, context=list(self.context))
        if choice.action not in force.action_names:
            choice = Choice(available[0].name, {}, choice.say)
        force.pending_id = uuid.uuid4().hex
        force.sent_at = self.clock()
        if choice.say and not force.silent:
            self.said.append(choice.say)
        payload = {"id": force.pending_id, "name": choice.action}
        if choice.data or self.actions[choice.action].schema:
            payload["data"] = json.dumps(choice.data)
        return [{"command": "action", "data": payload}]

    def _finish(self, force: Force) -> None:
        if not force.ephemeral:
            if force.state:
                self.context.append(force.state[:2000])
            self.context.append(f"Game asked: {force.query[:500]}")
        self.force = None


# ---- choosers ---------------------------------------------------------------

def example_for(schema: dict[str, Any], rng: random.Random) -> Any:
    """A value matching a simple JSON schema (the subset the SDK supports)."""
    if "enum" in schema and schema["enum"]:
        return rng.choice(schema["enum"])
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: example_for(v, rng) for k, v in (schema.get("properties") or {}).items() if isinstance(v, dict)}
    if kind == "array":
        item = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        count = max(int(schema.get("minItems", 1)), 1)
        return [example_for(item, rng) for _ in range(count)]
    if kind == "integer":
        lo, hi = int(schema.get("minimum", 0)), int(schema.get("maximum", 10))
        return rng.randint(lo, max(lo, hi))
    if kind == "number":
        lo, hi = float(schema.get("minimum", 0)), float(schema.get("maximum", 1))
        return rng.uniform(lo, max(lo, hi))
    if kind == "boolean":
        return rng.choice([True, False])
    return "ok"


class RandomChooser:
    """Picks a random allowed action with schema-valid data, like the SDK's "Randy"."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    def choose(self, *, game: str, force: Force, actions: list[Action], context: list[str]) -> Choice:
        action = self.rng.choice(actions)
        return Choice(action.name, example_for(action.schema, self.rng) if action.schema else {})


class ModelChooser:
    """Asks a language model (any gamma.llm adapter) to pick the action as JSON.

    The answer is checked against the action list and the action's schema; if it
    doesn't parse, the session's retry handles it like a game-side failure.
    """

    def __init__(self, adapter, *, persona: str = "You are Shana, playing a game with the owner.") -> None:
        self.adapter = adapter
        self.persona = persona

    def choose(self, *, game: str, force: Force, actions: list[Action], context: list[str]) -> Choice:
        catalogue = "\n".join(
            f"- {a.name}: {a.description}" + (f" (data schema: {json.dumps(a.schema)})" if a.schema else "")
            for a in actions)
        recent = "\n".join(context[-15:]) or "(nothing yet)"
        retry = f"\nYour last choice failed: {force.last_error}" if force.last_error else ""
        system = (f"{self.persona}\nGame: {game or 'unknown'}.\nReply with JSON only: "
                  '{"action": <one name below>, "data": <object matching its schema, or {}>, '
                  '"say": <one short line you might say, or "">}.\nActions:\n' + catalogue)
        user = f"Recent game context:\n{recent}\n\nCurrent state:\n{force.state or '(none)'}\n\n{force.query}{retry}"
        reply = self.adapter.generate_reply(system_prompt=system, user_text=user)
        return parse_choice(getattr(reply, "text", str(reply)), actions)


def parse_choice(text: str, actions: list[Action]) -> Choice:
    start, end = text.find("{"), text.rfind("}")
    try:
        payload = json.loads(text[start:end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        payload = {}
    names = {a.name for a in actions}
    action = payload.get("action") if payload.get("action") in names else actions[0].name
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    say = payload.get("say") if isinstance(payload.get("say"), str) else ""
    return Choice(action, data, say[:300])
