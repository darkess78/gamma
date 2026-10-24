from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from test_conversation_recipient import fixture_state
from gamma.config import settings
from gamma.errors import ConversationError
from gamma.llm.base import LLMReply
from gamma.performer.bus import PerformerEventBus
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.schemas.response import ConversationTurnDraft
from gamma.stream import brain as brain_module
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.output import PerformerBusOutputAdapter, StreamOutputDispatcher
from gamma.stream.trace import StreamTraceStore
from gamma.voice.tts import TTSResult


class _Provider:
    def __init__(self):
        self.calls = 0

    def generate_reply(self, *args, **kwargs):
        self.calls += 1
        return LLMReply(text=ConversationTurnDraft(
            action="reply", requested_delivery="speech", final_text="Safe synthetic reply.",
            reason_code="fixture",
        ).model_dump_json())


class _HeldReviewer:
    def __init__(self, action):
        self.action = action
        self.entered = threading.Event()
        self.release = threading.Event()

    def review(self, text):
        self.entered.set()
        assert self.release.wait(5)
        return LLMReviewDecision(action=self.action, reason="synthetic", confidence=1)


class _TTS:
    def __init__(self, path):
        self.path = path
        self.calls = 0
        path.write_bytes(b"RIFFsynthetic")

    def synthesize(self, text, emotion=None, styles=None):
        self.calls += 1
        return TTSResult(provider="synthetic", text=text, audio_path=str(self.path),
                         content_type="audio/wav", metadata={})


class _Dispatch(StreamOutputDispatcher):
    def __init__(self, bus):
        super().__init__([PerformerBusOutputAdapter(bus)])
        self.batches = []

    def dispatch(self, events):
        result = super().dispatch(events)
        self.batches.append(list(events))
        return result


def _setup(fixture_state, tmp_path, monkeypatch, action):
    service = fixture_state.service
    service._llm = _Provider()
    bus = PerformerEventBus()
    dispatcher = _Dispatch(bus)
    reviewer = _HeldReviewer(action)
    tts = _TTS(tmp_path / "synthetic.wav")
    brain = StreamBrain(conversation=service, safety_reviewer=reviewer, speech_synthesizer=tts,
                        trace_store=StreamTraceStore(tmp_path / "trace.jsonl"),
                        output_dispatcher=dispatcher)
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "stream_safety_review_timeout_seconds", 3)
    finished = threading.Event()
    real_thread = threading.Thread

    def thread_factory(*args, **kwargs):
        # Observe completion of the real late callback, including the no-output allow case.
        if str(kwargs.get("name", "")).startswith("stream-safety-"):
            target = kwargs["target"]

            def observed():
                try:
                    target()
                finally:
                    finished.set()

            kwargs["target"] = observed
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(brain_module.threading, "Thread", thread_factory)
    return SimpleNamespace(brain=brain, bus=bus, dispatcher=dispatcher, reviewer=reviewer,
                           finished=finished, service=service, tts=tts)


def _drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def _exercise(state, metadata, action, expected_target, source="local"):
    queues = {}
    for target in ("stream_public", "dashboard_monitor", "discord_call"):
        subscriber, queue = await state.bus.subscribe(target_policy=target)
        queues[target] = (subscriber, queue)
    event = StreamInputEvent(kind="mic_transcript", text="Shana hello",
                             session_id="TARGET_SESSION_CANARY",
                             actor=StreamActor(source=source, platform_id="TARGET_ACTOR_CANARY"),
                             metadata={"public_output": True, **metadata})
    task = asyncio.create_task(asyncio.to_thread(state.brain.handle_event, event, synthesize_speech=True))
    try:
        assert await asyncio.to_thread(state.reviewer.entered.wait, 2)
        await asyncio.sleep(.02)
        assert not task.done()
        assert all(q.empty() for _, q in queues.values()) and state.bus.recent(limit=100) == []
        state.reviewer.release.set()
        result = await asyncio.wait_for(task, 2)
        assert not result.safety_decision.get("review_pending")
        assert state.service._llm.calls == 1
        await asyncio.sleep(0)
        delivered = {target: _drain(q) for target, (_, q) in queues.items()}
        assert len(state.dispatcher.batches) == 1
        batch = state.dispatcher.batches[0]
        assert batch and delivered[expected_target]
        assert all(e.payload.get("target_policy", "stream_public") == expected_target for e in batch)
        assert all(e.input_event_id == event.event_id and e.turn_id == result.output_events[0].turn_id for e in batch)
        assert all(e.payload["actor"]["platform_id"] == "TARGET_ACTOR_CANARY"
                   and e.payload["input"]["session_id"] == "TARGET_SESSION_CANARY" for e in batch)
        if action == "block":
            assert any(e.type == "subtitle_line" and e.payload.get("text") == "filtered" for e in batch)
            assert all("Safe synthetic reply." not in str(e.payload) for e in batch)
        else:
            assert sum(e.type == "speech_started" for e in batch) == 1
        if expected_target != "stream_public":
            assert delivered["stream_public"] == []
        assert len(delivered["dashboard_monitor"]) == (0 if expected_target == "discord_call" else len(batch))
    finally:
        state.reviewer.release.set()
        if not task.done():
            await asyncio.wait_for(task, 3)
        for subscriber, _ in queues.values():
            state.bus.unsubscribe(subscriber)


@pytest.mark.parametrize("target", ["stream_public", "dashboard_monitor", "discord_call"])
@pytest.mark.parametrize("action", ["allow", "block"])
def test_review_retains_target_through_subscriber_queues(fixture_state, tmp_path, monkeypatch, target, action):
    state = _setup(fixture_state, tmp_path, monkeypatch, action)
    asyncio.run(_exercise(state, {"output_target_policy": target}, action, target,
                          source="discord" if target == "discord_call" else "local"))


@pytest.mark.parametrize("metadata,target", [
    ({}, "stream_public"),
    ({"output_target_policy": "  "}, "stream_public"),
    ({"output_target_policy": "  DaShBoArD_MoNiToR  "}, "dashboard_monitor"),
])
def test_review_uses_existing_default_and_normalization(fixture_state, tmp_path, monkeypatch, metadata, target):
    state = _setup(fixture_state, tmp_path, monkeypatch, "block")
    asyncio.run(_exercise(state, metadata, "block", target))


def test_unknown_target_still_rejected_before_conversation(fixture_state, tmp_path, monkeypatch):
    state = _setup(fixture_state, tmp_path, monkeypatch, "block")
    event = StreamInputEvent(kind="mic_transcript", text="Shana hello", metadata={
        "public_output": True, "output_target_policy": "unknown-target",
    })
    with pytest.raises(ConversationError, match="unknown_recipient_output_target"):
        state.brain.handle_event(event, synthesize_speech=True)
    assert state.service._llm.calls == 0
    assert state.dispatcher.batches == []


def test_late_callback_preserves_unknown_metadata_fallback(fixture_state, tmp_path, monkeypatch):
    state = _setup(fixture_state, tmp_path, monkeypatch, "block")

    async def run():
        subscriber, queue = await state.bus.subscribe(target_policy="stream_public")
        review = Future()
        review.set_result(LLMReviewDecision(action="block", reason="synthetic", confidence=1))
        executor = ThreadPoolExecutor(max_workers=1)
        event = StreamInputEvent(kind="mic_transcript", text="Shana hello", metadata={
            "output_target_policy": "unknown-target",
        })
        try:
            # The normal respond route rejects unknown targets. This direct callback checks
            # the pre-existing helper fallback, without granting a new recipient route.
            state.brain._arm_late_safety_interrupt(event=event, turn_id="fixture-turn",
                                                  review_future=review, executor=executor)
            assert await asyncio.to_thread(state.finished.wait, 2)
            await asyncio.sleep(0)
            delivered = _drain(queue)
            assert delivered and all(e["target_policy"] == "stream_public" for e in delivered)
        finally:
            state.bus.unsubscribe(subscriber)
            executor.shutdown(wait=True)

    asyncio.run(run())

@pytest.mark.parametrize("voice,subtitles", [(False, False), (True, False), (False, True)])
def test_late_callback_retains_existing_twitch_output_controls(fixture_state, tmp_path, monkeypatch, voice, subtitles):
    state = _setup(fixture_state, tmp_path, monkeypatch, "block")

    async def run():
        public_id, public_queue = await state.bus.subscribe(target_policy="stream_public")
        monitor_id, monitor_queue = await state.bus.subscribe(target_policy="dashboard_monitor")
        review = Future()
        review.set_result(LLMReviewDecision(action="block", reason="synthetic", confidence=1))
        executor = ThreadPoolExecutor(max_workers=1)
        event = StreamInputEvent(kind="chat_message", text="Shana hello",
                                 actor=StreamActor(source="twitch", platform_id="filter-fixture"),
                                 metadata={"output_target_policy": "dashboard_monitor", "twitch_controls": {
                                     "voice_enabled": voice, "subtitles_enabled": subtitles,
                                 }})
        try:
            state.brain._arm_late_safety_interrupt(event=event, turn_id="filter-turn",
                                                  review_future=review, executor=executor)
            assert await asyncio.to_thread(state.finished.wait, 2)
            await asyncio.sleep(0)
            delivered = _drain(monitor_queue)
            assert delivered and public_queue.empty()
            assert all(e["target_policy"] == "dashboard_monitor" for e in delivered)
            assert any(e["type"] == "output_cleared" for e in delivered) is voice
            assert any(e["type"] == "subtitle_update" for e in delivered) is subtitles
        finally:
            state.bus.unsubscribe(public_id)
            state.bus.unsubscribe(monitor_id)
            executor.shutdown(wait=True)

    asyncio.run(run())
