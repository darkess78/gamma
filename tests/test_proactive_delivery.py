import asyncio
from datetime import datetime, timedelta, timezone
import threading
from unittest.mock import Mock

import pytest

from gamma.brain import runtime
from gamma.brain.queue import BrainQueue
from gamma.config import settings
from gamma.presence import load_presence_state, save_presence_state, presence_state_for_mode
from gamma.proactive import ProactiveScheduler
from gamma.stream.brain import StreamBrain
from gamma.stream.trace import StreamTraceStore
from test_stream_brain import _FakeConversation, _FakeSpeechSynthesizer, _RecordingDispatcher
from test_tool_authority import conversation


@pytest.mark.parametrize("stop_during_delivery", [False, True])
def test_scheduler_delivery_releases_brain_and_old_generation_cannot_publish(tmp_path, monkeypatch, stop_during_delivery):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'state.db'}")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)
    monkeypatch.setattr(settings, "proactive_idle_enabled", True)
    monkeypatch.setattr(settings, "proactive_idle_speech_enabled", True)
    monkeypatch.setattr(settings, "proactive_idle_tick_seconds", 1)
    monkeypatch.setattr(settings, "proactive_quiet_hours_start", "")
    monkeypatch.setattr("gamma.presence.presence_state_path", lambda: tmp_path / "presence.json")
    state = presence_state_for_mode("wake")
    state["lifecycle"]["last_interaction_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    save_presence_state(state)
    bus = Mock()
    bus.stats.return_value = {"muted_targets": []}
    bus.has_eligible_listener.return_value = True
    bus.recent_turns.return_value = []
    synth = _FakeSpeechSynthesizer()
    entered, release = threading.Event(), threading.Event()
    original = synth.synthesize
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    synth.synthesize = held
    dispatcher = _RecordingDispatcher()
    brain = StreamBrain(conversation=_FakeConversation(), speech_synthesizer=synth,
        output_dispatcher=dispatcher, trace_store=StreamTraceStore(tmp_path / "trace.jsonl"))
    queue = BrainQueue()
    monkeypatch.setattr(runtime, "_queue", queue)
    scheduler = ProactiveScheduler(stream_brain=brain, bus=bus)

    async def run():
        await scheduler.start()
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            assert await runtime.await_brain_job(lambda: "interactive decision") == "interactive decision"
            pending = load_presence_state()["autonomy"]["scheduler"]
            assert pending["attempts_for_topic"] == 1 and not pending.get("last_emitted_at")
            if stop_during_delivery:
                await scheduler.stop()
                assert brain.delivery_jobs.status()["active"]
                await scheduler.start()
            release.set()
            await asyncio.wrap_future(brain.delivery_jobs.submit(lambda: None, on_cancel=lambda: None))
            if stop_during_delivery:
                assert dispatcher.calls == []
                assert not load_presence_state()["autonomy"]["scheduler"].get("last_emitted_at")
            else:
                deadline = asyncio.get_running_loop().time() + 3
                while load_presence_state()["autonomy"]["scheduler"].get("status") != "emitted":
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.01)
                assert len(dispatcher.calls) == 1
        finally:
            release.set()
            await scheduler.stop()
            queue.close()
            brain.delivery_jobs.close()
    asyncio.run(run())


def test_proactive_decision_records_event_without_resetting_human_idle_clock(conversation, monkeypatch):
    from types import SimpleNamespace
    from gamma.schemas.conversation import SpeakerContext
    from sqlmodel import Session, select
    from gamma.memory.continuity import ConversationJournalEntry
    conversation._llm = SimpleNamespace(generate_reply=lambda **kw: SimpleNamespace(text="A fictional brief observation."))
    clock_update = Mock(side_effect=AssertionError("autonomous event reset human idle clock"))
    monkeypatch.setattr(conversation, "_record_presence_interaction", clock_update)
    monkeypatch.setattr("gamma.conversation.service.build_system_prompt", lambda **kw: "Fixture")
    response = conversation.respond("Fictional scheduled check", session_id="auto-fixture", fast_mode=True,
        speaker_ctx=SpeakerContext(source="presence_scheduler"), defer_delivery=True)
    clock_update.assert_not_called()
    with Session(conversation._continuity._engine) as session:
        rows = session.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id == response.route_trace_id)).all()
    assert sorted(row.role for row in rows) == ["assistant", "event"]
