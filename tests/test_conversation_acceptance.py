"""Task 6A integration checks: real conversation, journal, authority and delivery."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest
from sqlalchemy import select
from sqlmodel import Session, select as model_select

from gamma.brain import runtime as brain_runtime
from gamma.brain.queue import BrainQueue
from gamma.config import settings
from gamma.memory.continuity import ConversationJournalEntry
from gamma.memory.core import CoreMemoryStore
from gamma.memory.state_authority import outbox, delivery_grants, exchanges
from gamma.schemas.voice import VoiceTranscriptionResponse
from gamma.voice.core_live import CoreLiveTurnRuntime
from test_tool_authority import conversation


def rows(conversation, table):
    with Session(conversation._continuity._engine) as session:
        return session.execute(select(table)).mappings().all()


async def finish(runtime):
    if runtime._tasks:
        await asyncio.wait_for(asyncio.gather(*list(runtime._tasks)), 5)


def test_cancel_pending_real_conversation_cannot_commit(conversation, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "assistant_state_enabled", False)
    entered, release = threading.Event(), threading.Event()
    prompts = []
    def generate(*args, **kwargs):
        prompts.append(kwargs.get("system_prompt", args[0] if args else ""))
        if len(prompts) == 1:
            entered.set()
            assert release.wait(5)
            return SimpleNamespace(text=json.dumps({"action": "tool_first", "requested_delivery": "text_only",
                "final_text": "Obsolete fictional reply.", "tool_calls": [
                    {"tool": "save_core_memory", "args": {"fact": "Obsolete violet lantern preference"}}]}))
        return SimpleNamespace(text="A fresh fictional reply.")
    conversation._llm = SimpleNamespace(generate_reply=generate)
    # A second pending session proves cancellation is scoped to the originating exchange.
    unrelated = conversation._continuity.begin_exchange(session_id="other-session", text="Unrelated user input",
        speaker_name="Fixture", input_source="local", privacy_scope="local_private")
    path = tmp_path / "input.wav"
    path.write_bytes(b"controlled audio provider")
    async def transcribe(path):
        path.unlink()
        return VoiceTranscriptionResponse(transcript="Remember my fictional lantern preference.")
    media = SimpleNamespace(save_upload=AsyncMock(return_value=path), _transcribe_owned=transcribe)
    bus, queue = Mock(), BrainQueue()
    runtime = CoreLiveTurnRuntime(media=media, conversation=conversation, bus=bus)
    async def exercise():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="fixture"):
            await runtime.start_turn(audio_file=Mock(), session_id="cancel-session", synthesize_speech=False, turn_id="cancel-before-commit")
            assert await asyncio.to_thread(entered.wait, 3)
            runtime.cancel_turn("cancel-before-commit")
            release.set()
            await finish(runtime)
            assert runtime.get_turn("cancel-before-commit").status == "cancelled"
            bus.publish.assert_not_called()
    try:
        with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"):
            anyio.run(exercise)
            assert rows(conversation, outbox) == []
            assert rows(conversation, delivery_grants) == []
            assert "Obsolete violet" not in CoreMemoryStore().load()
            with Session(conversation._continuity._engine) as session:
                journal = list(session.exec(model_select(ConversationJournalEntry)))
            assert len(journal) == 2
            canceled = next(row for row in journal if row.session_id == "cancel-session")
            assert canceled.role == "user" and canceled.completion_status == "cancelled"
            assert next(row for row in rows(conversation, exchanges) if row["exchange_id"] == unrelated)["status"] == "pending"
            conversation.respond("What happened to my request?", session_id="cancel-session", fast_mode=True, defer_delivery=True)
            assert "Obsolete fictional reply" not in prompts[-1]
            assert "Remember my fictional lantern preference." in prompts[-1]
    finally:
        release.set()
        queue.close(timeout=3)


def test_undelivered_reply_is_not_confirmed_next_prompt(conversation):
    prompts = []
    def generate(*args, **kwargs):
        prompts.append(kwargs.get("system_prompt", args[0] if args else ""))
        return SimpleNamespace(text="The fictional violet lantern is ready.")
    conversation._llm = SimpleNamespace(generate_reply=generate)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"):
        response = conversation.respond("Tell me about the fictional lantern.", session_id="history",
            fast_mode=True, defer_delivery=True)
        assert response.tts_metadata["delivery_grant"]
        conversation.cancel_delivery(response.tts_metadata["delivery_grant"])
        conversation.respond("What did you communicate?", session_id="history", fast_mode=True, defer_delivery=True)
    assert "assistant: The fictional violet lantern is ready." not in prompts[-1]
    assert '"last_assistant_action": "The fictional violet lantern is ready."' not in prompts[-1]
    assert "delivery unknown" in prompts[-1].lower() or "not delivered" in prompts[-1].lower()


def recorded_provider(conversation):
    prompts = []
    def generate(*args, **kwargs):
        prompts.append(kwargs.get("system_prompt", args[0] if args else ""))
        return SimpleNamespace(text="The fictional violet lantern is ready.")
    conversation._llm = SimpleNamespace(generate_reply=generate)
    return prompts


def prepared_audio(conversation, tmp_path):
    response = conversation.respond("Tell me about the fictional lantern.", session_id="history",
        fast_mode=True, synthesize_speech=True, defer_delivery=True)
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"controlled audio fixture, no hardware acceptance")
    response.audio_path, response.audio_content_type = str(audio), "audio/wav"
    conversation.deliver_response(response, synthesize_speech=False)
    return response


@pytest.mark.parametrize("outcome", ["never_started", "interrupted", "completed", "missing", "failed"])
def test_receipt_outcomes_qualify_actual_next_prompt(conversation, tmp_path, monkeypatch, outcome):
    from gamma.delivery.playback import PlaybackLedger
    from gamma.delivery.authority import DeliveryDenied
    from gamma.memory.state_authority import playback_receipts
    from gamma.performer.models import PerformerOutputEvent
    from test_playback_leases import ack, permit
    monkeypatch.setattr(settings, "tts_runtime", "direct")
    prompts = recorded_provider(conversation)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"):
        response = prepared_audio(conversation, tmp_path)
        ledger = PlaybackLedger(database_url=settings.database_url)
        grant = response.tts_metadata["delivery_grant"]
        lease = ledger.open(receiver="fictional-listener", target="dashboard_monitor")
        event = PerformerOutputEvent(type="speech_chunk_ready", turn_id=response.route_trace_id,
            target_policy="dashboard_monitor", payload={"audio_url": response.tts_metadata["delivery_audio_url"],
                "text": response.speech_text, "delivery_grant": grant}).model_dump()
        ledger.offer(lease, "fictional-listener", event)
        if outcome != "never_started":
            permit(ledger, lease, "fictional-listener", event["event_id"])
            ledger.acknowledge(lease, "fictional-listener", ack(lease, event["event_id"], "started"))
        if outcome in {"interrupted", "completed", "failed"}:
            ledger.acknowledge(lease, "fictional-listener", ack(lease, event["event_id"], outcome))
        if outcome in {"never_started", "interrupted"}:
            conversation.cancel_delivery(grant)
            ledger.revoke_obsolete(lease, "fictional-listener", eligible=lambda _: True,
                target_epoch=lambda _: 0, presence="")
            with pytest.raises((DeliveryDenied, ValueError)):
                ledger.acknowledge(lease, "fictional-listener", ack(lease, event["event_id"], "completed"))
            assert ledger.revoke_obsolete(lease, "fictional-listener", eligible=lambda _: True,
                target_epoch=lambda _: 0, presence="") == []
        before = rows(conversation, playback_receipts)
        assert len(before) == 1
        # A different receiver cannot redirect an old receipt to itself.
        other = ledger.open(receiver="other-listener", target="dashboard_monitor")
        with pytest.raises(DeliveryDenied):
            ledger.acknowledge(other, "other-listener", ack(lease, event["event_id"], "completed"))
        conversation.respond("What did you communicate?", session_id="history", fast_mode=True, defer_delivery=True)
    prompt = prompts[-1]
    assert len(rows(conversation, outbox)) == 2  # Cancellation never deletes authorized decisions.
    assert "assistant: The fictional violet lantern is ready." not in prompt
    if outcome == "completed":
        assert "audio completed" in prompt and response.speech_text in prompt
    else:
        assert response.speech_text not in prompt
        assert "unknown" in prompt
    if outcome == "interrupted":
        assert "heard words unknown" in prompt
    assert rows(conversation, playback_receipts) == before


def test_text_sent_before_audio_cancel_remains_text_history(conversation, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gamma.api import routes
    from gamma.delivery.playback import PlaybackLedger
    from gamma.memory.state_authority import dispatch_attempts
    from gamma.performer.bus import PerformerEventBus
    from gamma.performer.models import PerformerOutputEvent
    from gamma.presence import presence_state_for_mode
    monkeypatch.setattr(settings, "tts_runtime", "direct")
    prompts = recorded_provider(conversation)
    ledger = PlaybackLedger(database_url=settings.database_url)
    bus = PerformerEventBus()
    state = presence_state_for_mode("wake")
    monkeypatch.setattr("gamma.presence.load_presence_state", lambda **kwargs: state)
    monkeypatch.setattr(routes, "load_presence_state", lambda **kwargs: state)
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "get_performer_bus", lambda: bus)
    monkeypatch.setattr("gamma.delivery.execution.get_playback_ledger", lambda: ledger)
    monkeypatch.setattr(routes, "_websocket_api_auth_ok", lambda _: True)
    app = FastAPI()
    app.include_router(routes.router)
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"):
        response = prepared_audio(conversation, tmp_path)
        with TestClient(app).websocket_connect("/v1/performer/events?target_policy=dashboard_monitor") as receiver:
            assert receiver.receive_json()["type"] == "ready"
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id=response.route_trace_id,
                target_policy="dashboard_monitor", payload={"text": response.display_text,
                    "delivery_grant": response.tts_metadata["delivery_grant"]}))
            assert receiver.receive_json()["payload"]["text"] == response.display_text
            # A control roundtrip is an explicit barrier after send accounting.
            receiver.send_json(dict(type="subscriber_capabilities", text_ready=True, audio_ready=False))
            assert receiver.receive_json()["type"] == "subscriber_capabilities"
            conversation.cancel_delivery(response.tts_metadata["delivery_grant"])
            conversation.respond("What did you communicate?", session_id="history", fast_mode=True, defer_delivery=True)
    attempts = rows(conversation, dispatch_attempts)
    assert any(row["consumer"].startswith("websocket_text:") and row["status"] == "dispatched" for row in attempts)
    assert "text sent" in prompts[-1] and response.display_text in prompts[-1]
    assert "audio completed" not in prompts[-1]
    assert "audio delivery unknown" in prompts[-1]


def test_commit_wins_preserves_memory_then_cancels_delivery(conversation, tmp_path, monkeypatch):
    from gamma.conversation.cancellation import CancellationBoundary
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(settings, "assistant_state_enabled", False)
    boundary = CancellationBoundary()
    entered, release, cancel_attempted = threading.Event(), threading.Event(), threading.Event()
    complete = conversation._continuity._authority.complete
    def held_complete(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return complete(*args, **kwargs)
    monkeypatch.setattr(conversation._continuity._authority, "complete", held_complete)
    conversation._llm = SimpleNamespace(generate_reply=lambda *a, **kw: SimpleNamespace(text=json.dumps({
        "action": "tool_first", "requested_delivery": "text_only", "final_text": "Committed fictional reply.",
        "tool_calls": [{"tool": "save_core_memory", "args": {"fact": "Legitimate fictional lantern preference"}}]})))
    def cancel():
        cancel_attempted.set()
        boundary.set()
    with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"), ThreadPoolExecutor(2) as pool:
        response_future = pool.submit(conversation.respond, "Remember my preference.", session_id="committed",
            fast_mode=True, defer_delivery=True, _cancellation=boundary)
        try:
            assert entered.wait(3)
            cancel_future = pool.submit(cancel)
            assert cancel_attempted.wait(3)
            assert not cancel_future.done()  # The real state transaction owns the boundary.
        finally:
            release.set()
        response = response_future.result(5)
        cancel_future.result(5)
    assert boundary.is_set()
    assert len(rows(conversation, outbox)) == 1
    assert "Legitimate fictional lantern preference" in CoreMemoryStore().load()
    conversation.cancel_delivery(response.tts_metadata["delivery_grant"])
    assert rows(conversation, delivery_grants)[0]["status"] == "cancelled"
    assert conversation.continuity_snapshot("committed")["summary"]["completed_exchanges"] == 1


def test_owner_preference_correction_and_unrelated_identity_retrieval(conversation, monkeypatch):
    from gamma.identity.profile import SpeakerProfile
    from gamma.schemas.response import MemoryCandidate
    monkeypatch.setattr(settings, "assistant_state_enabled", False)
    owner = SpeakerProfile("Fictional Owner", "owner", is_owner=True)
    monkeypatch.setattr(conversation._identity, "resolve", lambda _: owner)
    prompts = recorded_provider(conversation)
    memory = conversation._memory
    memory.persist_candidates([
        MemoryCandidate(type="preference", text="My favorite drink is tea.", tags=["preference"], importance=1),
        MemoryCandidate(type="preference", text="Fictional Morgan likes violet marmalade.",
            subject_type="other_person", subject_name="Fictional Morgan", confidence=1)])
    conversation.respond("What drink do I like?", session_id="preference", fast_mode=True, defer_delivery=True)
    memory_section = prompts[-1].split("# Runtime Memory", 1)[1].split("# Response Rules", 1)[0]
    assert "tea" in memory_section.lower()
    assert "violet marmalade" not in prompts[-1]
    memory.persist_candidates([MemoryCandidate(type="preference", text="My favorite drink is coffee.", tags=["preference"], importance=1)])
    conversation.respond("What drink do I like now?", session_id="preference", fast_mode=True, defer_delivery=True)
    facts = memory.get_profile_facts()
    assert any("coffee" in fact.fact_text.lower() for fact in facts)
    assert all("tea" not in fact.fact_text.lower() for fact in facts)
    memory_section = prompts[-1].split("# Runtime Memory", 1)[1].split("# Response Rules", 1)[0]
    assert "coffee" in memory_section.lower() and "tea" not in memory_section.lower()
    monkeypatch.setattr(conversation._identity, "resolve", lambda _: SpeakerProfile("Fictional Guest", "public"))
    conversation.respond("What private drink preference do you remember?", session_id="guest", fast_mode=True, defer_delivery=True)
    assert "My favorite drink is coffee." not in prompts[-1]
    assert "violet marmalade" not in prompts[-1]


def test_review_fault_keeps_uncertain_capacity_and_requires_explicit_recovery(conversation, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from gamma.errors import ConversationError
    from gamma.resources import shared_execution
    from gamma.safety.llm_reviewer import SpeechLLMReviewer
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "brain_model_runtime", "direct")
    monkeypatch.setattr(settings, "local_llm_timeout_seconds", 0.1)
    reviewer = SpeechLLMReviewer()
    conversation._speech_filter._policy._reviewer = reviewer
    prompts = recorded_provider(conversation)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            first = len(calls) == 1
            if first:
                entered.set()
                assert release.wait(5)
            body = json.dumps({"response": json.dumps({
                "action": "allow", "reason": "controlled fixture", "confidence": 1})}).encode()
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if first:
                    completed.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "local_llm_endpoint", f"http://127.0.0.1:{server.server_port}")
    try:
        with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"), ThreadPoolExecutor(1) as pool:
            work = pool.submit(conversation.respond, "Hello fictional reviewer.", session_id="review-fault",
                fast_mode=True, defer_delivery=True)
            assert entered.wait(3)
            with pytest.raises(ConversationError, match="required_review_unavailable"):
                work.result(3)
            ledger, _, _ = shared_execution.authority()
            unresolved = ledger.snapshot()["unresolved"]
            assert len(unresolved) == 1 and unresolved[0]["workload"] == "safety"
            token = unresolved[0]["token"]
            assert rows(conversation, outbox) == [] and rows(conversation, delivery_grants) == []
            assert len(calls) == 1 and calls[0]["model"] == reviewer._model
            assert shared_execution.reconcile()["released"] == []
            assert ledger.snapshot()["allocations"] and not completed.is_set()
            # This task owns the controlled single-request provider and observes
            # that request's terminal handler event before releasing its token.
            release.set()
            assert completed.wait(3)
            assert ledger.snapshot()["unresolved"][0]["token"] == token
            ledger.release(token)
            response = conversation.respond("Explicit new attempt.", session_id="review-recovery",
                fast_mode=True, defer_delivery=True)
        assert response.tts_metadata["delivery_grant"]
        assert len(rows(conversation, outbox)) == 1
        assert not ledger.snapshot()["allocations"]
        assert len(prompts) == 2 and len(calls) == 2
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()


@pytest.mark.parametrize("stage", ["after_commit_before_return", "during_playback"])
def test_live_cancellation_after_commit_preserves_audit_and_revokes_delivery(conversation, tmp_path, monkeypatch, stage):
    from gamma.delivery.playback import PlaybackLedger
    from gamma.delivery.authority import DeliveryDenied
    from gamma.memory.state_authority import playback_receipts
    from test_playback_leases import ack, permit
    monkeypatch.setattr(settings, "tts_runtime", "direct")
    entered, release = threading.Event(), threading.Event()
    prompts = recorded_provider(conversation)
    original = conversation.respond
    def respond(*args, **kwargs):
        result = original(*args, **kwargs)
        if stage == "after_commit_before_return":
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(conversation, "respond", respond)
    path = tmp_path / "input.wav"
    path.write_bytes(b"controlled audio input")
    audio = tmp_path / "output.wav"
    audio.write_bytes(b"controlled audio output")
    conversation._tts = SimpleNamespace(synthesize=lambda **kw: SimpleNamespace(
        audio_path=str(audio), content_type="audio/wav", metadata={}))
    async def transcribe(path):
        path.unlink()
        return VoiceTranscriptionResponse(transcript="Tell me about the fictional lantern.")
    media = SimpleNamespace(save_upload=AsyncMock(return_value=path), _transcribe_owned=transcribe)
    bus, queue = Mock(), BrainQueue()
    runtime = CoreLiveTurnRuntime(media=media, conversation=conversation, bus=bus)
    async def exercise():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="fixture"):
            await runtime.start_turn(audio_file=Mock(), session_id="history", synthesize_speech=True, turn_id="committed-live")
            if stage == "after_commit_before_return":
                assert await asyncio.to_thread(entered.wait, 3)
                runtime.cancel_turn("committed-live")
                release.set()
                await finish(runtime)
                bus.publish.assert_not_called()
            else:
                await finish(runtime)
                assert runtime.get_turn("committed-live").status == "completed"
                event = next(call.args[0].model_dump() for call in bus.publish.call_args_list
                    if call.args[0].type == "speech_chunk_ready")
                ledger = PlaybackLedger(database_url=settings.database_url)
                lease = ledger.open(receiver="fictional", target="dashboard_monitor")
                ledger.offer(lease, "fictional", event)
                permit(ledger, lease, "fictional", event["event_id"])
                ledger.acknowledge(lease, "fictional", ack(lease, event["event_id"], "started"))
                runtime.cancel_turn("committed-live")
                assert ledger.revoke_obsolete(lease, "fictional", eligible=lambda _: True,
                    target_epoch=lambda _: 0, presence="") == [event["event_id"]]
                with pytest.raises(DeliveryDenied):
                    ledger.acknowledge(lease, "fictional", ack(lease, event["event_id"], "completed"))
                assert rows(conversation, playback_receipts)[0]["status"] == "interrupted"
            assert runtime.get_turn("committed-live").status == "cancelled"
            assert rows(conversation, delivery_grants)[0]["status"] == "cancelled"
            assert len(rows(conversation, outbox)) == 1
            with Session(conversation._continuity._engine) as session:
                journal = list(session.exec(model_select(ConversationJournalEntry)))
            assert [row.role for row in journal] == ["user", "assistant"]
            assert all(row.completion_status == "completed" for row in journal)
    try:
        with patch("gamma.conversation.service.build_system_prompt", return_value="Fictional owner"):
            anyio.run(exercise)
            original("What did you communicate?", session_id="history", fast_mode=True, defer_delivery=True)
            assert "The fictional violet lantern is ready." not in prompts[-1]
            assert "unknown" in prompts[-1]
    finally:
        release.set()
        queue.close(timeout=3)
