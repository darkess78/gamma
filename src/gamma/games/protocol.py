"""Gamma's game protocol, version 1: the server side, independent of transport.

A game connects, says ``hello``, declares the actions Shana may take (each with
a small JSON-schema for its params), sends ``observe`` messages as things
happen, and sends ``decide`` when it wants Shana to pick something. Gamma
answers with ``act``; the game reports the ``outcome``. The full message list is
in specs/games.md.

One ``GameSession`` per connection. Feed it each decoded message with
``receive()`` and call ``tick()`` about once a second; both return the messages
to send back. Picking an action is delegated to a ``Chooser`` (a random test
player or Shana's model). Params are checked against the declared schema before
anything is sent, so a bad model answer is retried here instead of reaching the
game.
"""
from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})
CHARACTER = "shana"
DISPLAY_NAME = "Shana"

MAX_ATTEMPTS = 3                 # chooser calls per decision, invalid answers and rejections included
OUTCOME_TIMEOUT_SECONDS = 10.0   # an act with no outcome after this counts as rejected
DEFAULT_DEADLINE_MS = 15_000
MIN_DEADLINE_MS = 1_000
MAX_DEADLINE_MS = 120_000
CONTEXT_LINES = 40
MAX_ACTIONS = 64

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IMPORTANCE = ("low", "normal", "high")
_SCHEMA_TYPES = {"object", "string", "integer", "number", "boolean", "array"}
_SCHEMA_KEYS = {"type", "description", "title", "properties", "required", "enum",
                "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "items"}


# ---- data -------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    name: str
    description: str
    params: dict[str, Any] | None = None  # JSON schema of an object, or None for no params


@dataclass(frozen=True)
class Observation:
    text: str
    importance: str = "normal"
    comment: bool = False


@dataclass
class Decision:
    id: str
    prompt: str
    state: str
    action_names: list[str]
    deadline: float            # clock time by which an act must be sent
    priority: str = "normal"
    speak: bool = True
    remember: bool = True
    game: str = ""
    attempts: int = 0
    last_error: str = ""
    awaiting_since: float | None = None  # set while an act waits for its outcome


@dataclass(frozen=True)
class Choice:
    action: str
    params: Any = field(default_factory=dict)  # should be an object; checked before sending
    say: str = ""  # a line Shana says about the move; empty for none


class Chooser(Protocol):
    def choose(self, decision: Decision, actions: list[Action], context: list[Observation]) -> Choice: ...


# ---- schema subset -----------------------------------------------------------

def check_schema(schema: Any, path: str = "params") -> list[str]:
    """Problems with a declared schema; empty when it uses only the supported subset."""
    if not isinstance(schema, dict):
        return [f"{path}: must be an object"]
    problems = [f"{path}: unsupported keyword {k!r}" for k in schema if k not in _SCHEMA_KEYS]
    kind = schema.get("type")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            problems.append(f"{path}: enum must be a non-empty list")
    elif kind not in _SCHEMA_TYPES:
        problems.append(f"{path}: type must be one of {sorted(_SCHEMA_TYPES)}")
    if kind == "object":
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            problems.append(f"{path}.properties: must be an object")
            props = {}
        for key, sub in props.items():
            problems += check_schema(sub, f"{path}.{key}")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(r not in props for r in required):
            problems.append(f"{path}.required: must list declared properties")
    if kind == "array" and "items" in schema:
        problems += check_schema(schema["items"], f"{path}[]")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            problems.append(f"{path}.{key}: must be a number")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
            problems.append(f"{path}.{key}: must be a non-negative integer")
    return problems


def validate(value: Any, schema: dict[str, Any], path: str = "params") -> list[str]:
    """Why ``value`` doesn't match ``schema`` (the supported subset); empty when it does."""
    if "enum" in schema:
        return [] if value in schema["enum"] else [f"{path}: must be one of {schema['enum']}"]
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}: must be an object"]
        props = schema.get("properties", {})
        errors = [f"{path}.{k}: is required" for k in schema.get("required", []) if k not in value]
        errors += [f"{path}.{k}: is not a declared param" for k in value if k not in props]
        for key, sub in props.items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
        return errors
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: must be an array"]
        errors = []
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], f"{path}[{i}]")
        return errors
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path}: must be a string"]
        if len(value) < schema.get("minLength", 0):
            return [f"{path}: must be at least {schema['minLength']} characters"]
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return [f"{path}: must be at most {schema['maxLength']} characters"]
        return []
    if kind == "boolean":
        return [] if isinstance(value, bool) else [f"{path}: must be true or false"]
    if kind in ("integer", "number"):
        is_int = isinstance(value, int) and not isinstance(value, bool)
        if kind == "integer" and not is_int:
            return [f"{path}: must be an integer"]
        if kind == "number" and not (is_int or isinstance(value, float)):
            return [f"{path}: must be a number"]
        if "minimum" in schema and value < schema["minimum"]:
            return [f"{path}: must be >= {schema['minimum']}"]
        if "maximum" in schema and value > schema["maximum"]:
            return [f"{path}: must be <= {schema['maximum']}"]
        return []
    return []


# ---- session -----------------------------------------------------------------

def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _error(message: str, about: str = "") -> dict[str, Any]:
    reply: dict[str, Any] = {"type": "error", "message": message}
    if about:
        reply["about"] = about
    return reply


class GameSession:
    def __init__(self, chooser: Chooser, *, clock=time.monotonic) -> None:
        self.chooser = chooser
        self.clock = clock
        self.session_id = ""
        self.game = ""
        self.capabilities: set[str] = set()
        self.actions: dict[str, Action] = {}
        self.context: deque[Observation] = deque(maxlen=CONTEXT_LINES)
        self.decision: Decision | None = None
        self.said: list[str] = []  # Shana's lines, for the speech/subtitle path to pick up
        self.closed = False

    # ---- game -> Gamma ------------------------------------------------------

    def receive(self, message: Any) -> list[dict[str, Any]]:
        """Handle one decoded message; returns the replies."""
        if self.closed:
            return []
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            return [_error("each message must be a JSON object with a string 'type'")]
        kind = message["type"]
        if kind == "hello":
            return self._hello(message)
        if not self.session_id:
            return [_error("send hello first", kind)]
        handler = {
            "actions/declare": self._declare,
            "actions/remove": self._remove,
            "observe": self._observe,
            "decide": self._decide,
            "cancel": self._cancel,
            "outcome": self._outcome,
            "bye": self._bye,
        }.get(kind)
        if handler is None:
            return [_error(f"unknown message type {kind!r}", kind)]
        return handler(message)

    def _hello(self, msg: dict) -> list[dict]:
        version = msg.get("protocol")
        if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
            self.closed = True
            return [_error(f"unsupported protocol version {version!r}; this server speaks "
                           f"{sorted(SUPPORTED_VERSIONS)}", "hello"),
                    {"type": "bye", "reason": "unsupported protocol version"}]
        game = _text(msg.get("game"), 120)
        if not game:
            return [_error("hello needs a 'game' name", "hello")]
        caps = msg.get("capabilities")
        self.game = game
        self.capabilities = {c for c in caps if isinstance(c, str)} if isinstance(caps, list) else set()
        self.actions.clear()
        self.decision = None
        self.session_id = uuid.uuid4().hex
        return [{"type": "welcome", "protocol": PROTOCOL_VERSION, "session": self.session_id,
                 "character": CHARACTER, "display_name": DISPLAY_NAME,
                 "limits": {"max_attempts": MAX_ATTEMPTS, "outcome_timeout_ms": int(OUTCOME_TIMEOUT_SECONDS * 1000),
                            "deadline_ms": [MIN_DEADLINE_MS, MAX_DEADLINE_MS], "max_actions": MAX_ACTIONS}}]

    def _declare(self, msg: dict) -> list[dict]:
        raw_actions = msg.get("actions")
        if not isinstance(raw_actions, list):
            return [_error("actions/declare needs an 'actions' list", "actions/declare")]
        accepted: list[str] = []
        errors: list[str] = []
        for raw in raw_actions:
            name = raw.get("name") if isinstance(raw, dict) else None
            if not isinstance(name, str) or not _NAME.match(name):
                errors.append(f"action name {name!r} must match {_NAME.pattern}")
                continue
            description = _text(raw.get("description"), 1000)
            if not description:
                errors.append(f"{name}: needs a description")
                continue
            params = raw.get("params")
            if params is not None:
                problems = check_schema(params)
                if not problems and params.get("type") != "object":
                    problems = ["params: must have type 'object'"]
                if problems:
                    errors.append(f"{name}: " + "; ".join(problems))
                    continue
            if name not in self.actions and len(self.actions) >= MAX_ACTIONS:
                errors.append(f"{name}: more than {MAX_ACTIONS} actions")
                continue
            self.actions[name] = Action(name, description, params)
            accepted.append(name)
        return [{"type": "declared", "actions": accepted, "errors": errors}]

    def _remove(self, msg: dict) -> list[dict]:
        for name in msg.get("names") or []:
            if isinstance(name, str):
                self.actions.pop(name, None)
        return []

    def _observe(self, msg: dict) -> list[dict]:
        text = _text(msg.get("text"), 2000)
        if not text:
            return [_error("observe needs 'text'", "observe")]
        importance = msg.get("importance") if msg.get("importance") in _IMPORTANCE else "normal"
        self.context.append(Observation(text, importance, msg.get("comment") is True))
        return []

    def _decide(self, msg: dict) -> list[dict]:
        request_id = _text(msg.get("id"), 64)
        if not request_id:
            return [_error("decide needs an 'id'", "decide")]
        if self.decision is not None:
            return [{"type": "pass", "id": request_id, "reason": "busy",
                     "message": f"still deciding {self.decision.id}"}]
        prompt = _text(msg.get("prompt"), 4000)
        names = msg.get("actions")
        if not prompt or not isinstance(names, list) or not names:
            return [_error("decide needs 'prompt' and a non-empty 'actions' list", "decide")]
        unknown = [n for n in names if n not in self.actions]
        if unknown:
            return [{"type": "pass", "id": request_id, "reason": "unknown_action",
                     "message": f"not declared: {unknown}"}]
        deadline_ms = msg.get("deadline_ms", DEFAULT_DEADLINE_MS)
        if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, (int, float)):
            deadline_ms = DEFAULT_DEADLINE_MS
        deadline_ms = min(max(deadline_ms, MIN_DEADLINE_MS), MAX_DEADLINE_MS)
        self.decision = Decision(
            id=request_id, prompt=prompt, state=_text(msg.get("state"), 8000),
            action_names=list(dict.fromkeys(names)),
            deadline=self.clock() + deadline_ms / 1000,
            priority=msg.get("priority") if msg.get("priority") in _IMPORTANCE else "normal",
            speak=msg.get("speak") is not False, remember=msg.get("remember") is not False,
            game=self.game)
        return self._attempt()

    def _cancel(self, msg: dict) -> list[dict]:
        if self.decision is not None and msg.get("id") == self.decision.id:
            self.decision = None
        return []

    def _outcome(self, msg: dict) -> list[dict]:
        decision = self.decision
        if decision is None or decision.awaiting_since is None or msg.get("id") != decision.id:
            return []  # late, duplicate or unknown: nothing to do
        message = _text(msg.get("message"), 2000)
        if msg.get("ok") is True:
            if message:
                self.context.append(Observation(message))
            self._finish(decision)
            return []
        decision.last_error = message or "the game rejected that action"
        decision.awaiting_since = None
        return self._attempt()

    def _bye(self, _msg: dict) -> list[dict]:
        self.closed = True
        self.decision = None
        return []

    # ---- Gamma -> game ------------------------------------------------------

    def tick(self) -> list[dict]:
        """Call about once a second: expires outcomes that never arrive."""
        decision = self.decision
        if decision is None or decision.awaiting_since is None:
            return []
        if self.clock() - decision.awaiting_since > OUTCOME_TIMEOUT_SECONDS:
            decision.last_error = "no outcome from the game in time"
            decision.awaiting_since = None
            return self._attempt()
        return []

    def _attempt(self) -> list[dict]:
        """Ask the chooser until it gives valid params, then send one act (or pass)."""
        decision = self.decision
        assert decision is not None
        while True:
            available = [self.actions[n] for n in decision.action_names if n in self.actions]
            if not available:
                return self._give_up(decision, "no_actions", "the allowed actions were removed")
            if decision.attempts >= MAX_ATTEMPTS:
                return self._give_up(decision, "attempts", decision.last_error)
            if self.clock() >= decision.deadline:
                return self._give_up(decision, "deadline", "the deadline passed")
            decision.attempts += 1
            try:
                choice = self.chooser.choose(decision, available, list(self.context))
            except Exception as exc:  # a broken model call counts as a failed attempt
                decision.last_error = f"chooser failed: {type(exc).__name__}"
                continue
            problem = self._check_choice(choice, decision)
            if problem:
                decision.last_error = problem
                continue
            if self.clock() >= decision.deadline:
                return self._give_up(decision, "deadline", "the deadline passed while choosing")
            replies: list[dict] = []
            if choice.say and decision.speak:
                self.said.append(choice.say)
                replies.append({"type": "say", "id": decision.id, "text": choice.say})
            decision.awaiting_since = self.clock()
            replies.append({"type": "act", "id": decision.id, "attempt": decision.attempts,
                            "action": choice.action, "params": choice.params or {}})
            return replies

    def _check_choice(self, choice: Choice, decision: Decision) -> str:
        if choice.action not in decision.action_names or choice.action not in self.actions:
            return f"{choice.action!r} is not one of the allowed actions {decision.action_names}"
        schema = self.actions[choice.action].params
        if schema is None:
            return "" if choice.params in ({}, None) else f"{choice.action} takes no params"
        errors = validate(choice.params, schema)
        return "; ".join(errors[:5])

    def _give_up(self, decision: Decision, reason: str, message: str) -> list[dict]:
        self.context.append(Observation(f"(Shana passed on: {decision.prompt[:200]} - {message})"))
        self._finish(decision)
        return [{"type": "pass", "id": decision.id, "reason": reason, "message": message[:500]}]

    def _finish(self, decision: Decision) -> None:
        if decision.remember:
            if decision.state:
                self.context.append(Observation(decision.state[:2000], "low"))
            self.context.append(Observation(f"Game asked: {decision.prompt[:500]}", "low"))
        self.decision = None


# ---- choosers ----------------------------------------------------------------

def example_for(schema: dict[str, Any], rng: random.Random) -> Any:
    """A random value that satisfies a schema in the supported subset."""
    if schema.get("enum"):
        return rng.choice(schema["enum"])
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        return {k: example_for(v, rng) for k, v in props.items() if k in required or rng.random() < 0.5}
    if kind == "array":
        item = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "integer"}
        lo = schema.get("minItems", 0)
        count = rng.randint(lo, max(lo, schema.get("maxItems", lo + 2)))
        return [example_for(item, rng) for _ in range(count)]
    if kind == "integer":
        lo = math.ceil(schema.get("minimum", 0))
        hi = math.floor(schema.get("maximum", lo + 10))
        return rng.randint(lo, max(lo, hi))
    if kind == "number":
        lo = float(schema.get("minimum", 0))
        return rng.uniform(lo, max(lo, float(schema.get("maximum", lo + 1))))
    if kind == "boolean":
        return rng.choice([True, False])
    lo = schema.get("minLength", 1)
    return "x" * rng.randint(lo, max(lo, min(schema.get("maxLength", lo + 4), lo + 4)))


class RandomChooser:
    """Picks a random allowed action with valid params. For testing a game without a model."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    def choose(self, decision: Decision, actions: list[Action], context: list[Observation]) -> Choice:
        action = self.rng.choice(actions)
        return Choice(action.name, example_for(action.params, self.rng) if action.params else {})


class ModelChooser:
    """Asks a language model to pick the action as JSON.

    ``adapter`` is anything with ``generate_reply(system_prompt=..., user_text=...)``
    returning an object with ``.text`` (every gamma.llm adapter). A reply that
    doesn't parse or doesn't fit the schema is retried by the session, with the
    problem included in the next prompt.
    """

    def __init__(self, adapter, *, persona: str = "You are Shana, playing a game on stream.") -> None:
        self.adapter = adapter
        self.persona = persona

    def prompts(self, decision: Decision, actions: list[Action], context: list[Observation]) -> tuple[str, str]:
        catalogue = "\n".join(
            f"- {a.name}: {a.description}" + (f"\n  params schema: {json.dumps(a.params)}" if a.params else
                                                "\n  no params")
            for a in actions)
        say_rule = ('"say": one short line you would say out loud, or ""' if decision.speak
                    else '"say": "" (stay quiet this time)')
        system = (f"{self.persona}\nGame: {decision.game or 'unknown'}.\n"
                  'Reply with one JSON object and nothing else: {"action": <one name below>, '
                  f'"params": <object matching that action\'s schema, or {{}}>, {say_rule}}}.\n'
                  "Actions:\n" + catalogue)
        recent = context[-15:]
        older_important = [o for o in context[:-15] if o.importance == "high"]
        lines = "\n".join(("! " if o.importance == "high" else "- ") + o.text for o in older_important + recent)
        retry = (f"\n\nYour previous answer didn't work: {decision.last_error}. Try again."
                 if decision.last_error else "")
        user = (f"What has happened:\n{lines or '(nothing yet)'}\n\n"
                f"Current state:\n{decision.state or '(not given)'}\n\n{decision.prompt}{retry}")
        return system, user

    def choose(self, decision: Decision, actions: list[Action], context: list[Observation]) -> Choice:
        system, user = self.prompts(decision, actions, context)
        reply = self.adapter.generate_reply(system_prompt=system, user_text=user)
        return parse_choice(getattr(reply, "text", str(reply)))


def parse_choice(text: str) -> Choice:
    """The first JSON object in a model reply as a Choice; an empty action if there is none.

    No fallback action is guessed: an empty or wrong action fails validation and
    the session asks again.
    """
    start = text.find("{")
    payload: Any = None
    if start >= 0:
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            end = text.rfind("}")
            try:
                payload = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return Choice("")
    action = payload.get("action") if isinstance(payload.get("action"), str) else ""
    params = payload.get("params", {})
    say = payload.get("say") if isinstance(payload.get("say"), str) else ""
    return Choice(action, params, say.strip()[:300])
