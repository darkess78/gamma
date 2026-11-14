"""A small reference client for Gamma's game protocol (specs/games.md).

Copy it into a game, or read it as an example for writing one in another
language. It needs only a connection that sends and receives text frames:

    client = GameClient.connect("ws://gamma-pc:8000/v1/games/play?key=...")
    client.hello("My Game")
    client.declare(Action("move", "Move one square", {"type": "object", ...}))
    client.observe("A door opened.")
    act = client.decide_and_apply("Your move.", ["move"], apply=my_game.apply)
    client.bye()
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

PROTOCOL_VERSION = 1


class ProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    params: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.params is not None:
            out["params"] = self.params
        return out


class GameClient:
    def __init__(self, send_text: Callable[[str], Any], receive_text: Callable[[], str]) -> None:
        self._send_text = send_text
        self._receive_text = receive_text
        self.session = ""
        self.said: list[str] = []      # Shana's lines, for subtitles or TTS on the game side
        self.errors: list[dict] = []   # error messages from Gamma (non-fatal)

    @classmethod
    def connect(cls, url: str) -> "GameClient":
        """Open a websocket with the ``websockets`` package (sync API)."""
        from websockets.sync.client import connect
        ws = connect(url)
        return cls(ws.send, ws.recv)

    @classmethod
    def over(cls, ws) -> "GameClient":
        """Wrap any object with ``send_text``/``receive_text`` (e.g. Starlette's test client)."""
        return cls(ws.send_text, ws.receive_text)

    # ---- low level ------------------------------------------------------------

    def send(self, type_: str, **fields: Any) -> None:
        self._send_text(json.dumps({"type": type_, **fields}))

    def wait_for(self, *types: str) -> dict[str, Any]:
        """Read until one of ``types`` arrives; ``say`` and ``error`` are collected on the way."""
        while True:
            message = json.loads(self._receive_text())
            kind = message.get("type")
            if kind in types:
                return message
            if kind == "say":
                self.said.append(message.get("text", ""))
            elif kind == "error":
                self.errors.append(message)
            elif kind == "bye":
                raise ProtocolError(f"Gamma ended the session: {message.get('reason')}; errors: {self.errors}")

    # ---- messages -------------------------------------------------------------

    def hello(self, game: str, capabilities: list[str] | None = None) -> dict[str, Any]:
        self.send("hello", protocol=PROTOCOL_VERSION, game=game, capabilities=capabilities or [])
        welcome = self.wait_for("welcome")
        self.session = welcome["session"]
        return welcome

    def declare(self, *actions: Action) -> dict[str, Any]:
        """Declare actions; returns Gamma's ``declared`` reply (accepted names and errors)."""
        self.send("actions/declare", actions=[a.to_json() for a in actions])
        reply = self.wait_for("declared")
        if reply.get("errors"):
            raise ProtocolError(f"actions rejected: {reply['errors']}")
        return reply

    def remove(self, *names: str) -> None:
        self.send("actions/remove", names=list(names))

    def observe(self, text: str, *, importance: str = "normal", comment: bool = False) -> None:
        self.send("observe", text=text, importance=importance, comment=comment)

    def decide(self, request_id: str, prompt: str, actions: list[str], *, state: str = "",
               deadline_ms: int = 15_000, priority: str = "normal", speak: bool = True,
               remember: bool = True) -> dict[str, Any]:
        """Ask Shana to choose; returns the ``act`` or ``pass`` for this request."""
        self.send("decide", id=request_id, prompt=prompt, actions=actions, state=state,
                  deadline_ms=deadline_ms, priority=priority, speak=speak, remember=remember)
        return self._answer_to(request_id)

    def outcome(self, request_id: str, ok: bool, message: str = "") -> dict[str, Any] | None:
        """Report what happened. After a rejection Gamma retries: returns the next ``act`` or ``pass``."""
        self.send("outcome", id=request_id, ok=ok, message=message)
        return None if ok else self._answer_to(request_id)

    def cancel(self, request_id: str) -> None:
        self.send("cancel", id=request_id)

    def bye(self, reason: str = "") -> None:
        self.send("bye", reason=reason)

    def _answer_to(self, request_id: str) -> dict[str, Any]:
        while True:
            message = self.wait_for("act", "pass")
            if message.get("id") == request_id:
                return message

    # ---- convenience ----------------------------------------------------------

    def decide_and_apply(self, prompt: str, actions: list[str],
                         apply: Callable[[str, dict[str, Any]], tuple[bool, str]],
                         *, request_id: str | None = None, **options: Any) -> dict[str, Any]:
        """Decide, apply each ``act`` with ``apply(action, params) -> (ok, message)``,
        report outcomes, and return the accepted ``act`` or the final ``pass``."""
        request_id = request_id or uuid.uuid4().hex[:12]
        answer = self.decide(request_id, prompt, actions, **options)
        while answer["type"] == "act":
            ok, message = apply(answer["action"], answer.get("params") or {})
            nxt = self.outcome(request_id, ok, message)
            if nxt is None:
                return answer
            answer = nxt
        return answer
