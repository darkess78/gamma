"""Getting a game session's lines onto the stream.

Move lines (``say``) and events the game marked ``comment: true`` become
``game_state`` stream events. The stream brain then applies the same safety
review, pacing and subtitle/voice output as her other stream speech
(``stream.brain._game_decision``). The game is an unknown speaker: no private
memory, no tools.
"""
from __future__ import annotations

from ..stream.models import StreamActor, StreamInputEvent
from .protocol import GameSession


def _event(session: GameSession, text: str, **metadata) -> StreamInputEvent:
    return StreamInputEvent(
        kind="game_state", text=text, session_id=f"game:{session.session_id}",
        actor=StreamActor(source="game", platform_id=None, display_name=session.game or "game"),
        metadata={"public_output": True, "output_target_policy": "stream_public", "game": session.game, **metadata},
    )


def take_stream_events(session: GameSession, sent: dict[str, int]) -> list[StreamInputEvent]:
    """New move lines and comment events since the last call; ``sent`` remembers how far we got."""
    events = [_event(session, line, game_say=line) for line in session.said[sent.get("said", 0):]]
    events += [_event(session, f"In {session.game or 'the game'}: {text}\nReact briefly, in one short sentence.",
                      game_comment=True)
               for text in session.comments[sent.get("comments", 0):]]
    sent["said"], sent["comments"] = len(session.said), len(session.comments)
    return events
