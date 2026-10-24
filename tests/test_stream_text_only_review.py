from __future__ import annotations

import asyncio
import threading

import pytest

from test_conversation_recipient import fixture_state
from gamma.config import settings
from gamma.integrations.discord.delivery import approved_reply
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


TEXT = "Reviewed fixture reply."


class _Provider:
    def __init__(self, delivery="text_only"):
        self.delivery = delivery
        self.calls = 0

    def generate_reply(self, *args, **kwargs):
        self.calls += 1
        return LLMReply(text=ConversationTurnDraft(action="reply", requested_delivery=self.delivery,
                                                 final_text=TEXT, reason_code="fixture").model_dump_json())


class _Reviewer:
    def __init__(self, action, *, held=False):
        self.action = action
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not held:
            self.release.set()

    def review(self, text):
        self.calls.append(text)
        self.entered.set()
        assert self.release.wait(5)
        return LLMReviewDecision(action=self.action, reason="synthetic", confidence=1)


class _TTS:
    def __init__(self, path, *, allowed=False):
        self.path, self.allowed = path, allowed
        self.calls = 0
        path.write_bytes(b"RIFFsynthetic")

    def synthesize(self, text, emotion=None, styles=None):
        self.calls += 1
        assert self.allowed, "Text-only delivery must not synthesize TTS"
        return TTSResult(provider="synthetic", text=text, audio_path=str(self.path),
                         content_type="audio/wav", metadata={})


def _setup(fixture_state, tmp_path, monkeypatch, target, action, *, delivery="text_only",
           enabled=True, event_control=True, held=False):
    service = fixture_state.service
    service._llm = _Provider(delivery)
    tts = _TTS(tmp_path / "synthetic.wav", allowed=delivery == "speech")
    service._tts = tts
    reviewer = _Reviewer(action, held=held)
    bus = PerformerEventBus()
    brain = StreamBrain(conversation=service, safety_reviewer=reviewer, speech_synthesizer=tts,
                        trace_store=StreamTraceStore(tmp_path / "trace.jsonl"),
                        output_dispatcher=StreamOutputDispatcher([PerformerBusOutputAdapter(bus)]))
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", enabled)
    event = StreamInputEvent(kind="mic_transcript", text="Shana hello", session_id="selection-fixture",
                             actor=StreamActor(source="discord" if target == "discord_call" else "local",
                                               platform_id="fixture-actor"),
                             metadata={"public_output": True, "output_target_policy": target,
                                       "twitch_controls": {"llm_safety_review_enabled": event_control}})
    return brain, bus, reviewer, tts, event


@pytest.mark.parametrize("target", ["stream_public", "dashboard_monitor", "discord_call"])
@pytest.mark.parametrize("fast_mode", [True, False])
@pytest.mark.parametrize("action", ["allow", "block", "soften"])
@pytest.mark.parametrize("request_speech", [True, False])
def test_text_only_uses_enabled_synchronous_review(fixture_state, tmp_path, monkeypatch,
                                                  target, fast_mode, action, request_speech):
    brain, bus, reviewer, tts, event = _setup(fixture_state, tmp_path, monkeypatch, target, action)
    result = brain.handle_event(event, synthesize_speech=request_speech, fast_mode=fast_mode)
    assert fixture_state.service._llm.calls == 1
    assert reviewer.calls == [TEXT]
    assert tts.calls == 0
    assert result.safety_decision["stage"] == "llm"
    assert not result.safety_decision.get("review_pending")
    assert result.safety_decision["playback_approved"] is (action == "allow")
    assert result.safety_decision["blocked"] is (action != "allow")
    assert approved_reply(result.model_dump()) == (TEXT if action == "allow" else None)
    events = bus.recent(limit=100, target_policy=target)
    assert events and all(e.target_policy == target for e in events)
    assert all(e.payload["actor"]["platform_id"] == "fixture-actor"
               and e.payload["input"]["session_id"] == "selection-fixture" for e in events)
    # Text-only draft text is returned to the caller; the bus emits expression metadata.
    assert all(TEXT not in str(e.payload) for e in events)
    if action == "allow":
        assert result.assistant_response.delivery_mode == "text_only"
        assert not result.assistant_response.speech_text
        assert not result.assistant_response.audio_path
        assert [e.type for e in events] == ["expression_set"]
    else:
        assert result.assistant_response.display_text != TEXT
        assert any(e.payload.get("text") == "filtered" for e in events)
    if target != "stream_public":
        assert bus.recent(limit=100, target_policy="stream_public") == []


@pytest.mark.parametrize("target", ["stream_public", "dashboard_monitor", "discord_call"])
@pytest.mark.parametrize("fast_mode", [True, False])
@pytest.mark.parametrize("disabled", ["setting", "event_control"])
def test_text_only_preserves_optional_review_disable(fixture_state, tmp_path, monkeypatch,
                                                     target, fast_mode, disabled):
    brain, bus, reviewer, tts, event = _setup(fixture_state, tmp_path, monkeypatch, target, "block",
                                             enabled=disabled != "setting",
                                             event_control=disabled != "event_control")
    result = brain.handle_event(event, synthesize_speech=True, fast_mode=fast_mode)
    assert fixture_state.service._llm.calls == 1
    assert reviewer.calls == [] and tts.calls == 0
    assert result.safety_decision["source"] == "stream_output_gate_fast"
    assert result.safety_decision["playback_approved"] is True
    assert approved_reply(result.model_dump()) == TEXT
    assert result.assistant_response.delivery_mode == "text_only"
    assert [e.type for e in bus.recent(limit=100, target_policy=target)] == ["expression_set"]


@pytest.mark.parametrize("target", ["stream_public", "dashboard_monitor", "discord_call"])
@pytest.mark.parametrize("fast_mode", [True, False])
def test_actual_speech_prepares_without_publication_until_allow(fixture_state, tmp_path,
                                                               monkeypatch, target, fast_mode):
    brain, bus, reviewer, tts, event = _setup(fixture_state, tmp_path, monkeypatch, target, "allow",
                                             delivery="speech", held=True)

    async def run():
        subscriber, queue = await bus.subscribe(target_policy=target)
        task = asyncio.create_task(asyncio.to_thread(brain.handle_event, event, synthesize_speech=True,
                                                     fast_mode=fast_mode))
        try:
            assert await asyncio.to_thread(reviewer.entered.wait, 2)
            for _ in range(100):
                if tts.calls:
                    break
                await asyncio.sleep(.01)
            assert reviewer.calls == [TEXT] and tts.calls == 1
            await asyncio.sleep(.02)
            assert not task.done() and queue.empty() and bus.recent(limit=100) == []
            reviewer.release.set()
            result = await asyncio.wait_for(task, 2)
            assert result.assistant_response.delivery_mode == "speech"
            assert result.safety_decision["playback_approved"] is True
            assert not result.safety_decision.get("review_pending")
            await asyncio.sleep(0)
            delivered = []
            while not queue.empty():
                delivered.append(queue.get_nowait())
            assert sum(e["type"] == "speech_started" for e in delivered) == 1
            before = len(bus.recent(limit=100, target_policy=target))
            await asyncio.sleep(.02)
            assert queue.empty() and len(bus.recent(limit=100, target_policy=target)) == before
        finally:
            reviewer.release.set()
            if not task.done():
                await asyncio.wait_for(task, 3)
            bus.unsubscribe(subscriber)

    asyncio.run(run())
