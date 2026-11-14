"""A game's move lines and comment events reach the stream through the normal safety and pacing path."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from gamma.config import settings
from gamma.games.protocol import GameSession, RandomChooser
from gamma.games.stream_output import take_stream_events
from gamma.stream.brain import StreamBrain
from gamma.stream.trace import StreamTraceStore
from tests.test_stream_brain import _AllowReviewer, _BlockReviewer, _FakeConversation, _RecordingDispatcher


def _session():
    session = GameSession(RandomChooser(seed=1))
    session.receive({"type": "hello", "protocol": 1, "game": "Tic-tac-toe"})
    return session


def test_each_line_and_comment_is_handed_over_once():
    session = _session()
    session.said.append("Center, obviously.")
    session.receive({"type": "observe", "text": "The opponent took a corner.", "comment": True})
    session.receive({"type": "observe", "text": "Board updated."})
    sent = {}
    events = take_stream_events(session, sent)
    assert [e.metadata.get("game_say") for e in events] == ["Center, obviously.", None]
    assert events[1].metadata["game_comment"] and "opponent took a corner" in events[1].text
    assert all(e.kind == "game_state" and e.metadata["public_output"] for e in events)
    assert take_stream_events(session, sent) == []


def _brain(reviewer, conversation):
    temp = tempfile.mkdtemp()
    return StreamBrain(conversation=conversation, trace_store=StreamTraceStore(Path(temp) / "t.jsonl"),
                       safety_reviewer=reviewer, output_dispatcher=_RecordingDispatcher())


def _subtitles(result):
    return [e.payload.get("text") for e in result.output_events if e.type == "subtitle_line"]


def test_a_move_line_is_said_as_written_without_the_model():
    session = _session(); session.said.append("Center, obviously.")
    (event,) = take_stream_events(session, {})
    conversation = _FakeConversation()
    result = _brain(_AllowReviewer(), conversation).handle_event(event, synthesize_speech=False)
    assert conversation.calls == [] and result.decision.reason == "game_move_line"
    assert "Center, obviously." in " ".join(t or "" for t in _subtitles(result))


def test_a_comment_event_gets_a_reaction_as_an_unknown_speaker():
    session = _session(); session.receive({"type": "observe", "text": "You lost a rook.", "comment": True})
    (event,) = take_stream_events(session, {})
    conversation = _FakeConversation()
    result = _brain(_AllowReviewer(), conversation).handle_event(event, synthesize_speech=False)
    assert result.decision.reason == "game_event_comment" and len(conversation.calls) == 1
    call = conversation.calls[0]
    assert "You lost a rook." in call["user_text"] and not call["recipient"].allows_private_memory
    assert call["owner_verified"] is False


def test_plain_game_state_stays_silent():
    from gamma.stream.models import StreamActor, StreamInputEvent
    event = StreamInputEvent(kind="game_state", text="Board updated.", actor=StreamActor(source="game"))
    result = _brain(_AllowReviewer(), _FakeConversation()).handle_event(event, synthesize_speech=False)
    assert result.decision.decision == "defer" and result.output_events == []


def test_a_move_line_the_reviewer_blocks_is_filtered():
    session = _session(); session.said.append("some line the reviewer dislikes")
    (event,) = take_stream_events(session, {})
    with patch.object(settings, "speech_filter_llm_enabled", True):
        result = _brain(_BlockReviewer(), _FakeConversation()).handle_event(event, synthesize_speech=False)
    assert "some line the reviewer dislikes" not in " ".join(t or "" for t in _subtitles(result))
    assert result.safety_decision.get("blocked")
