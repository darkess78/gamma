import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, update
from sqlmodel import Session

from gamma.config import settings
from gamma.delivery.authority import DeliveryAuthority, DeliveryDenied
from gamma.memory.continuity import ContinuityService
from gamma.memory.documents import StateDocuments
from gamma.memory.service import MemoryService
from gamma.memory.state_authority import (
    StateConflict, generations, outbox, publications, delivery_grants, exchanges,
)
from gamma.schemas.response import AssistantResponse


def prepare(owner, name):
    return owner.begin_exchange(session_id=name, text="Synthetic input", speaker_name="Fixture",
        input_source=name, privacy_scope="local_private")


def commit(owner, decision, name, text="Fixture reply", **kwargs):
    owner.complete_exchange(exchange_id=decision, session_id=name, assistant_text=text,
        trace_id=decision, output_target="dashboard_monitor", privacy_scope="local_private", **kwargs)
    return AssistantResponse(spoken_text=text, route_trace_id=decision)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    return ContinuityService(), DeliveryAuthority(), dict(target="dashboard_monitor", audience="private", policy="fixture")


def test_unowned_and_operator_invalidated_decisions_cannot_publish(state):
    owner, delivery, context = state
    with pytest.raises(DeliveryDenied, match="ownership"):
        delivery.issue(AssistantResponse(spoken_text="Unowned"), **context)
    decision = prepare(owner, "text")
    response = commit(owner, decision, "text")
    grant = delivery.issue(response, **context)
    MemoryService().create_item({"kind": "profile_fact", "category": "fact", "summary": "Operator correction"})
    with pytest.raises(DeliveryDenied):
        delivery.dispatch(grant, response, **context)
    with Session(owner._engine) as session:
        assert not session.execute(select(publications)).all()


def test_publication_transaction_rolls_back_grant_and_outbox_on_failure(state, monkeypatch):
    owner, delivery, context = state
    response = commit(owner, prepare(owner, "text"), "text")
    grant = delivery.issue(response, **context)
    publish = delivery._state.publish
    def failing(*args, **kwargs):
        publish(*args, **kwargs)
        raise RuntimeError("crash before transaction commit")
    monkeypatch.setattr(delivery._state, "publish", failing)
    with pytest.raises(RuntimeError):
        delivery.dispatch(grant, response, **context)
    with Session(owner._engine) as session:
        assert session.execute(select(delivery_grants.c.status)).scalar_one() == "authorized"
        assert session.execute(select(outbox.c.status)).scalar_one() == "awaiting_authorization"
        assert not session.execute(select(publications)).all()
    monkeypatch.setattr(delivery._state, "publish", publish)
    delivery.dispatch(grant, response, **context)
    with pytest.raises(DeliveryDenied):
        delivery.dispatch(grant, response, **context)


def test_late_publication_cannot_replace_newer_target_across_sessions(state):
    owner, delivery, context = state
    old = commit(owner, prepare(owner, "voice"), "voice", text="Older")
    old_grant = delivery.issue(old, **context)
    new = commit(owner, prepare(owner, "text"), "text", text="Newer")
    new_grant = delivery.issue(new, **context)
    delivery.dispatch(new_grant, new, **context)
    with pytest.raises(DeliveryDenied, match="superseded"):
        delivery.dispatch(old_grant, old, **context)
    assert owner.last_output("dashboard_monitor")["text"] == "Newer"
    with pytest.raises(StateConflict):
        owner.record_output(target_policy="dashboard_monitor", turn_id="late", text="Older", status="completed",
            spoken=False, decision_id=old.route_trace_id, grant_id=old_grant)


def test_playback_permission_survives_new_decision_but_not_operator_edit(state, tmp_path):
    owner, delivery, context = state
    response = commit(owner, prepare(owner, "voice"), "voice")
    audio = tmp_path / "fixture.wav"
    audio.write_bytes(b"synthetic audio")
    response.audio_path = str(audio)
    grant = delivery.issue(response, **context)
    delivery.dispatch(grant, response, **context)
    commit(owner, prepare(owner, "text"), "text", persona_emotion="happy")
    assert delivery.read_audio(grant)[0] == b"synthetic audio"
    with owner._authority.transaction(operator=True) as session:
        session.commit()
    with pytest.raises(DeliveryDenied):
        delivery.read_audio(grant)


def test_voice_text_and_autonomy_compete_for_shared_persona_version(state):
    owner, _, _ = state
    owners = [ContinuityService() for _ in range(3)]
    names = ["voice", "text", "autonomy"]
    decisions = [prepare(item, name) for item, name in zip(owners, names)]
    barrier = Barrier(3)
    def finish(args):
        item, name, decision = args
        barrier.wait(timeout=5)
        try:
            commit(item, decision, name, persona_emotion="happy")
            return True
        except StateConflict:
            return False
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(finish, zip(owners, names, decisions)))
    assert sum(results) == 1
    with Session(owner._engine) as session:
        assert len(session.execute(select(outbox)).all()) == 1


def test_raw_document_sessions_and_retired_late_failure_are_rejected(state, tmp_path):
    owner, _, _ = state
    with Session(owner._engine) as session:
        with pytest.raises(StateConflict, match="transaction_required"):
            StateDocuments().mutate("test", tmp_path / "missing.json", lambda p: {"value": 1}, session=session)
    pending = prepare(owner, "voice")
    owner.retire_generation()
    with pytest.raises(StateConflict):
        owner.fail_exchange(pending)


@pytest.mark.parametrize("boundary", ["prepared", "committed", "publishing", "published"])
def test_real_crash_boundaries_recover_only_dead_owner(state, tmp_path, boundary):
    owner, delivery, context = state
    survivor = prepare(owner, "survivor")
    script = '''
import os, sys
from gamma.memory.continuity import ContinuityService
from gamma.delivery.authority import DeliveryAuthority
from gamma.schemas.response import AssistantResponse
url, boundary = sys.argv[1:]
c = ContinuityService(database_url=url)
x = c.begin_exchange(session_id='crashed',text='Fixture',speaker_name='Fixture',input_source='voice',privacy_scope='local_private')
if boundary == 'prepared': os._exit(31)
c.complete_exchange(exchange_id=x,session_id='crashed',assistant_text='Committed fixture',trace_id=x,output_target='dashboard_monitor',privacy_scope='local_private')
if boundary == 'committed': os._exit(31)
d = DeliveryAuthority(database_url=url)
r = AssistantResponse(spoken_text='Committed fixture',route_trace_id=x)
ctx = dict(target='dashboard_monitor',audience='private',policy='fixture')
g = d.issue(r,**ctx)
if boundary == 'publishing':
    original = d._state.publish
    def crash(*a, **kw):
        original(*a, **kw)
        os._exit(31)
    d._state.publish = crash
d.dispatch(g,r,**ctx)
os._exit(31)
'''
    result = subprocess.run([sys.executable, "-c", script, str(owner._engine.url), boundary], timeout=20)
    assert result.returncode == 31
    with owner._authority.transaction() as session:
        crashed = session.execute(select(exchanges).where(exchanges.c.session_id == "crashed")).mappings().one()
        session.execute(update(generations).where(generations.c.id == crashed["generation_id"]).values(lease_until=0))
        session.commit()
    assert owner.mark_pending_interrupted(generation_id=crashed["generation_id"]) == (1 if boundary == "prepared" else 0)
    commit(owner, survivor, "survivor")
    with Session(owner._engine) as session:
        retained = session.execute(select(publications)).all()
        assert len(retained) == (1 if boundary == "published" else 0)
        dead_outbox = session.execute(select(outbox).where(outbox.c.exchange_id == crashed["exchange_id"])).mappings().first()
        if boundary != "prepared":
            assert dead_outbox["status"] == "cancelled_generation"
    with pytest.raises(DeliveryDenied):
        delivery.issue(AssistantResponse(spoken_text="Committed fixture", route_trace_id=crashed["exchange_id"]), **context)


def test_silent_tool_results_survive_in_atomic_decision_record(state):
    owner, _, _ = state
    decision = prepare(owner, "silent")
    commit(owner, decision, "silent", text="", tool_metadata={"tool_results": [{"name": "fixture", "output": "complete"}]})
    with Session(owner._engine) as session:
        payload = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert payload["tool_metadata"]["tool_results"][0]["output"] == "complete"


def test_stream_effect_invalidates_pending_same_session_snapshot(state):
    owner, _, _ = state
    done = prepare(owner, "shared")
    commit(owner, done, "shared")
    pending = prepare(owner, "shared")
    with owner._authority.decision_transaction(done, effect="stream_state") as session:
        session.commit()
    with pytest.raises(StateConflict):
        commit(owner, pending, "shared")


def test_people_materialization_invalidates_once_and_readbacks_do_not(state):
    owner, _, _ = state
    memory = MemoryService()
    memory.create_item({"kind": "profile_fact", "summary": "Fixture knows gardening", "subject_type": "other_person", "subject_name": "Fixture"})
    pending = prepare(owner, "text")
    assert memory.get_known_people()[0]["name"] == "Fixture"
    with pytest.raises(StateConflict):
        commit(owner, pending, "text")
    pending = prepare(owner, "text")
    memory.get_known_people()
    memory.stats()
    commit(owner, pending, "text")


def test_recovery_requires_verifiable_dead_owner_and_preserves_other_generation(state, monkeypatch):
    import psutil
    owner, _, _ = state
    pending = prepare(owner, "voice")
    other = ContinuityService()
    survivor = prepare(other, "text")
    with owner._authority.transaction() as session:
        session.execute(update(generations).where(generations.c.id == owner.generation_id).values(lease_until=0))
        session.commit()
    with monkeypatch.context() as patcher:
        patcher.setattr("gamma.memory.state_authority.psutil.Process", lambda *a: (_ for _ in ()).throw(psutil.AccessDenied()))
        assert other.mark_pending_interrupted(generation_id=owner.generation_id) == 0
    commit(owner, pending, "voice")
    commit(other, survivor, "text")


def test_identity_resolution_and_prepare_are_atomic_against_operator_edit(state, monkeypatch):
    from threading import Event
    from gamma.conversation.service import ConversationService
    owner, _, _ = state
    service = ConversationService()
    service._continuity = owner
    resolve = service._identity.resolve
    started, done = Event(), Event()
    future = []
    with ThreadPoolExecutor(1) as pool:
        def edit():
            started.set()
            MemoryService().create_item({"kind": "profile_fact", "summary": "Operator change during identity lookup"})
            done.set()
        def resolving(context):
            speaker = resolve(context)
            future.append(pool.submit(edit))
            assert started.wait(2)
            assert not done.wait(.05), "operator crossed the identity/snapshot transaction"
            return speaker
        def inference(**kwargs):
            future[0].result(timeout=5)
            return AssistantResponse(spoken_text="Stale response")
        monkeypatch.setattr(service._identity, "resolve", resolving)
        monkeypatch.setattr(service, "_respond", inference)
        with pytest.raises(StateConflict):
            service.respond("Fixture input", session_id="identity-race")
    with Session(owner._engine) as session:
        assert not session.execute(select(outbox)).all()


def test_real_local_stream_owns_only_one_publication(state, monkeypatch, tmp_path):
    from gamma.conversation.service import ConversationService
    from gamma.stream.brain import StreamBrain
    from gamma.stream.models import StreamInputEvent, StreamActor, TurnDecision
    from gamma.stream.trace import StreamTraceStore
    from gamma.stream.output import StreamOutputDispatcher
    owner, _, _ = state
    service = ConversationService()
    service._continuity = owner
    monkeypatch.setattr(service, "_respond", lambda **kwargs: AssistantResponse(spoken_text="Local stream fixture"))
    brain = StreamBrain(conversation=service, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"),
                        output_dispatcher=StreamOutputDispatcher(adapters=[]))
    monkeypatch.setattr(brain, "decide", lambda event: TurnDecision(decision="reply", reason="fixture", should_call_conversation=True))
    monkeypatch.setattr(brain, "_review_stream_output", lambda *args, **kwargs: {})
    result = brain.handle_event(StreamInputEvent(kind="mic_transcript", text="Fixture", session_id="local-stream",
        actor=StreamActor(source="local")))
    with Session(owner._engine) as session:
        assert len(session.execute(select(publications)).all()) == 1
        assert session.execute(select(delivery_grants.c.status).where(delivery_grants.c.id ==
            result.assistant_response.tts_metadata["delivery_grant"])).scalar_one() == "dispatched"
        assert len(session.execute(select(delivery_grants).where(delivery_grants.c.status == "cancelled")).all()) == 1
