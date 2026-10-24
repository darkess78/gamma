"""Real SQLite evidence for stream writers and stale autonomous work."""
import pytest
from sqlmodel import Session

from gamma.config import settings
from gamma.memory.continuity import ContinuityService
from gamma.memory.state_authority import StateConflict
from gamma.stream.self_goals import StreamSelfGoalStore
from gamma.stream.temp_memory import StreamTempMemoryStore
from gamma.stream.models import StreamInputEvent, StreamActor, StreamTurnResult, TurnDecision


@pytest.fixture
def stores(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'state.sqlite3'}"
    monkeypatch.setattr(settings, "database_url", url)
    continuity = ContinuityService(database_url=url)
    return continuity, StreamTempMemoryStore(database_url=url), StreamSelfGoalStore(database_url=url)


def decision(continuity):
    exchange = continuity.begin_exchange(session_id="stream", text="event", speaker_name="viewer",
        input_source="twitch", privacy_scope="public_stream", input_role="event")
    continuity.complete_exchange(exchange_id=exchange, session_id="stream", assistant_text="",
        trace_id=exchange, output_target="internal", privacy_scope="public_stream")
    return exchange


def test_operator_edit_invalidates_pending_stream_effect(stores):
    continuity, temporary, goals = stores
    exchange = decision(continuity)
    goals.propose(title="Operator plan", description="Preserve correction")
    with pytest.raises(StateConflict):
        with continuity._authority.decision_transaction(exchange, effect="stream_state") as session:
            temporary.upsert(bucket="chat_mood", key="recent_activity", value="old result", session=session)
            session.commit()
    assert temporary.list_records()["items"] == []


@pytest.mark.parametrize("operation", ["temporary_clear", "goal_clear", "goal_approve"])
def test_each_operator_control_invalidates_pending_decision(stores, operation):
    continuity, temporary, goals = stores
    goal = goals.propose(title="Review me", description="proposal")
    exchange = continuity.begin_exchange(session_id="stream", text="old snapshot", speaker_name="viewer",
        input_source="twitch", privacy_scope="local_generic", input_role="event")
    if operation == "temporary_clear":
        temporary.clear()
    elif operation == "goal_clear":
        goals.clear()
    else:
        goals.set_status(goal.id, status="approved")
    with pytest.raises(StateConflict):
        continuity.complete_exchange(exchange_id=exchange, session_id="stream", assistant_text="stale",
            trace_id=exchange, output_target="internal", privacy_scope="local_generic")


def test_stream_effect_atomic_and_exactly_once(stores):
    continuity, temporary, goals = stores
    exchange = decision(continuity)
    with pytest.raises(RuntimeError):
        with continuity._authority.decision_transaction(exchange, effect="stream_state") as session:
            goals.propose(title="Pending", description="autonomous", session=session)
            temporary.upsert(bucket="chat_mood", key="recent_activity", value="pending", session=session)
            raise RuntimeError("crash before effect commit")
    assert goals.list_goals()["items"] == []
    assert temporary.list_records()["items"] == []
    with continuity._authority.decision_transaction(exchange, effect="stream_state") as session:
        goals.propose(title="Pending", description="autonomous", session=session)
        temporary.upsert(bucket="chat_mood", key="recent_activity", value="committed", session=session)
        session.commit()
    with pytest.raises(StateConflict):
        with continuity._authority.decision_transaction(exchange, effect="stream_state"):
            pass
    assert temporary.get(bucket="chat_mood", key="recent_activity").value == "committed"


def test_approved_goal_is_immutable_to_reproposal(stores):
    _, _, goals = stores
    goal = goals.propose(title="Goal", description="operator approved text")
    goals.set_status(goal.id, status="approved")
    changed = goals.propose(title="Goal", description="late autonomous overwrite")
    assert changed.description == "operator approved text"
    assert changed.status == "approved"


def test_foreign_generation_cannot_write_stream_effect(stores):
    continuity, temporary, _ = stores
    exchange = decision(continuity)
    other = ContinuityService(database_url=str(continuity._engine.url))
    with pytest.raises(StateConflict):
        with other._authority.decision_transaction(exchange, effect="stream_state") as session:
            temporary.add(bucket="event_history", key="foreign", value="bad", session=session)
            session.commit()
    assert temporary.list_records()["items"] == []


def test_raw_session_and_wrong_database_are_rejected(stores, tmp_path):
    continuity, temporary, _ = stores
    with Session(continuity._engine) as session, pytest.raises(StateConflict):
        temporary.add(bucket="event_history", key="raw", value="bad", session=session)
    other = StreamTempMemoryStore(database_url=f"sqlite:///{tmp_path / 'other.sqlite3'}")
    with continuity._authority.transaction(operator=True) as session, pytest.raises(StateConflict):
        other.add(bucket="event_history", key="wrong", value="bad", session=session)


def test_record_turn_rolls_back_both_records(stores, monkeypatch):
    continuity, temporary, _ = stores
    result = StreamTurnResult(input_event=StreamInputEvent(kind="chat_message", text="hello",
        actor=StreamActor(source="twitch")), decision=TurnDecision(decision="ignore", reason="quiet"))
    monkeypatch.setattr(temporary, "upsert", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("second write")))
    with pytest.raises(RuntimeError):
        temporary.record_turn(result)
    assert temporary.list_records()["items"] == []


def test_operator_change_during_stream_policy_rejects_result(stores, monkeypatch, tmp_path):
    from gamma.stream.brain import StreamBrain
    from gamma.stream.trace import StreamTraceStore
    continuity, temporary, goals = stores
    class NoModel:
        _continuity = continuity
    brain = StreamBrain(conversation=NoModel(), temp_memory_store=temporary,
        self_goal_store=goals, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"))
    def policy(event):
        temporary.upsert(bucket="owner_directives", key="direction", value="new operator intent")
        return TurnDecision(decision="ignore", reason="old evaluation")
    monkeypatch.setattr(brain, "decide", policy)
    with pytest.raises(StateConflict):
        brain.handle_event(StreamInputEvent(kind="chat_message", text="hello",
            actor=StreamActor(source="twitch"), session_id="stream"))
    items = temporary.list_records()["items"]
    assert len(items) == 1 and items[0]["value"] == "new operator intent"
    assert goals.list_goals()["items"] == []


def test_operator_change_during_context_read_rejects_nested_conversation(stores, monkeypatch, tmp_path):
    from gamma.conversation.service import ConversationService
    from gamma.stream.brain import StreamBrain
    from gamma.stream.trace import StreamTraceStore
    continuity, temporary, goals = stores
    conversation = ConversationService()
    conversation._continuity = continuity
    brain = StreamBrain(conversation=conversation, temp_memory_store=temporary,
        self_goal_store=goals, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"))
    monkeypatch.setattr(brain, "decide", lambda event: TurnDecision(decision="reply",
        reason="addressed", should_call_conversation=True))
    def stale_context():
        temporary.upsert(bucket="owner_directives", key="direction", value="new operator intent")
        return "old stream context"
    monkeypatch.setattr(brain, "_assemble_recent_context", stale_context)
    monkeypatch.setattr(conversation, "_respond", lambda **kwargs: pytest.fail("stale context reached inference"))
    with pytest.raises(StateConflict):
        brain.handle_event(StreamInputEvent(kind="chat_message", text="Shana hello",
            actor=StreamActor(source="twitch", platform_id="viewer"), session_id="stream"))
    assert temporary.list_records()["items"][0]["value"] == "new operator intent"
