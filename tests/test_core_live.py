import asyncio
import threading
from unittest.mock import Mock, AsyncMock, patch

import anyio
import pytest

from gamma.brain import runtime as brain_runtime
from gamma.brain.queue import BrainQueue, BrainQueueFull
from gamma.schemas.response import AssistantResponse
from gamma.schemas.voice import VoiceTranscriptionResponse
from gamma.voice.core_live import CoreLiveTurnRuntime


def fixture(tmp_path, **kwargs):
    path = tmp_path / "input.wav"
    path.write_bytes(b"fixture")
    media = Mock()
    media.save_upload = AsyncMock(return_value=path)
    async def transcribe(path):
        path.unlink()
        return VoiceTranscriptionResponse(transcript="fixture")
    media._transcribe_owned = AsyncMock(side_effect=transcribe)
    conversation = Mock()
    conversation.respond.return_value = AssistantResponse(spoken_text="Hello", tts_metadata={
        "delivery_grant": "grant", "delivery_audio_url": "/v1/delivery/grant/audio"})
    bus = Mock()
    runtime = CoreLiveTurnRuntime(media=media, conversation=conversation, bus=bus, **kwargs)
    return runtime, media, conversation, bus


async def settled(runtime):
    deadline = asyncio.get_running_loop().time() + 4
    while runtime._active:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)


def test_live_turn_runs_shared_stages_and_publishes_only_approved_reference(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path)
    queue = BrainQueue()
    worker = queue.submit(threading.get_ident).result(3)
    calls = []
    response = conversation.respond.return_value
    conversation.respond.side_effect = lambda *a, **kw: calls.append(threading.get_ident()) or response
    async def run():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="context"):
            queued = await runtime.start_turn(audio_file=Mock(), session_id="session", synthesize_speech=True, turn_id="one")
            assert queued.status == "queued" and queued.worker_pid is None
            with pytest.raises(ValueError):
                await runtime.start_turn(audio_file=Mock(), session_id="session", synthesize_speech=True, turn_id="one")
            assert media.save_upload.await_count == 1
            await settled(runtime)
            final = runtime.get_turn("one")
            assert final.status == "completed" and final.reply_text == "Hello"
            assert final.audio_base64 is None and not final.reply_chunks
            assert final.playback_expected is True
            assert calls == [worker]
            conversation.check_delivery.assert_called_once_with(response)
            events = [call.args[0] for call in bus.publish.call_args_list]
            assert [event.type for event in events] == ["subtitle_update", "speech_chunk_ready"]
            assert events[-1].payload["delivery_grant"] == "grant"
            assert "audio_base64" not in events[-1].payload
            runtime.cancel_turn("one")
            conversation.cancel_delivery.assert_called_with("grant")
    try:
        anyio.run(run)
    finally:
        queue.close(timeout=3)


def test_cancelled_active_generation_keeps_capacity_and_revokes_late_grant(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path, capacity=1)
    queue = BrainQueue()
    entered, release = threading.Event(), threading.Event()
    response = conversation.respond.return_value
    def generate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response
    conversation.respond.side_effect = generate
    async def run():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="context"):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="one")
            assert await asyncio.to_thread(entered.wait, 3)
            assert runtime.cancel_turn("one").status == "cancelled"
            with pytest.raises(BrainQueueFull):
                await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="two")
            release.set()
            await settled(runtime)
            assert runtime.get_turn("one").status == "cancelled"
            bus.publish.assert_not_called()
            conversation.cancel_delivery.assert_called_with("grant")
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_synthesis_releases_brain_but_retains_live_capacity_until_provider_exits(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path, capacity=1)
    queue = BrainQueue()
    entered, release = threading.Event(), threading.Event()
    threads = []
    def deliver(response, **kwargs):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(4)
        return response
    conversation.deliver_response.side_effect = deliver
    async def run():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="context"):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="synthesis")
            assert await asyncio.to_thread(entered.wait, 3)
            worker_thread = await asyncio.wrap_future(queue.submit(threading.get_ident))
            assert threads == [threads[0]] and threads[0] != worker_thread
            assert conversation.respond.call_args.kwargs["defer_delivery"] is True
            assert runtime.get_turn("synthesis").status == "synthesizing"
            task = next(iter(runtime._tasks))
            task.cancel()
            await asyncio.sleep(0)
            with pytest.raises(BrainQueueFull):
                await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True)
            assert "synthesis" in runtime._active
            release.set()
            await settled(runtime)
            conversation.deliver_response.assert_called_once()
            assert runtime.get_turn("synthesis").status == "cancelled"
            conversation.cancel_delivery.assert_called_with("grant")
            bus.publish.assert_not_called()
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_live_job_rejects_invalid_id_and_unsupported_mode_without_upload(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path)
    async def run():
        with pytest.raises(ValueError):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=False, response_mode="incremental_experimental")
        with pytest.raises(ValueError):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=False, turn_id="../unsafe")
        media.save_upload.assert_not_called()
    anyio.run(run)


def test_publication_denied_by_current_grant_check(tmp_path):
    from gamma.delivery.authority import DeliveryDenied
    runtime, media, conversation, bus = fixture(tmp_path)
    conversation.check_delivery.side_effect = DeliveryDenied("policy changed")
    queue = BrainQueue()
    async def run():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="context"):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="one")
            await settled(runtime)
            assert runtime.get_turn("one").status == "failed"
            assert runtime.get_turn("one").error == "DeliveryDenied"
            bus.publish.assert_not_called()
            conversation.cancel_delivery.assert_called_with("grant")
            runtime.close()
            with pytest.raises(BrainQueueFull):
                await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True)
    try:
        anyio.run(run)
    finally:
        queue.close(timeout=3)


def test_presence_change_after_transcription_prevents_generation(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path)
    context = ["before"]
    async def transcribe(path):
        path.unlink()
        context[0] = "after"
        return VoiceTranscriptionResponse(transcript="fixture")
    media._transcribe_owned.side_effect = transcribe
    async def run():
        with patch("gamma.voice.core_live._context", side_effect=lambda: context[0]):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="one")
            await settled(runtime)
            assert runtime.get_turn("one").status == "cancelled"
            assert runtime.get_turn("one").cancel_reason == "presence_changed"
            conversation.respond.assert_not_called()
            bus.publish.assert_not_called()
    anyio.run(run)


def test_cancelled_recognition_never_enters_brain(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def transcribe(path):
        entered.set()
        await release.wait()
        path.unlink()
        return VoiceTranscriptionResponse(transcript="obsolete audio")
    media._transcribe_owned.side_effect = transcribe
    async def run():
        with patch("gamma.voice.core_live._context", return_value="context"):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="stt-cancel")
            await entered.wait()
            runtime.cancel_turn("stt-cancel")
            release.set()
            await settled(runtime)
            assert runtime.get_turn("stt-cancel").status == "cancelled"
            conversation.respond.assert_not_called()
            bus.publish.assert_not_called()
    anyio.run(run)


def test_live_trace_links_reviewed_decision_and_delivery(tmp_path):
    runtime, media, conversation, bus = fixture(tmp_path)
    conversation.respond.return_value.route_trace_id = "decision-fixture"
    queue = BrainQueue()
    async def run():
        with patch.object(brain_runtime, "_queue", queue), patch("gamma.voice.core_live._context", return_value="context"):
            await runtime.start_turn(audio_file=Mock(), session_id=None, synthesize_speech=True, turn_id="trace-fixture")
            await settled(runtime)
            result = runtime.get_turn("trace-fixture")
            assert result.decision_trace_id == "decision-fixture"
            assert result.delivery_grant == "grant"
            assert result.playback_expected
    try: anyio.run(run)
    finally: queue.close(timeout=3)
