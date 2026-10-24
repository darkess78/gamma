import asyncio
import threading
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from gamma.brain import runtime
from gamma.brain.queue import BrainQueue
from gamma.schemas.response import AssistantResponse
from gamma.schemas.voice import VoiceTranscriptionResponse
from gamma.voice.roundtrip import VoiceRoundtripService


def test_recognizer_initializes_off_event_loop(tmp_path):
    from gamma.schemas.voice import VoiceInputContext
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"fixture")
    initialized = []
    stt = Mock()
    stt.transcribe_audio.return_value = "fixture"
    perception = Mock()
    perception.analyze_path.return_value = VoiceInputContext(ok=False, detail="disabled")
    with patch("gamma.voice.roundtrip.STTService", side_effect=lambda: initialized.append(threading.get_ident()) or stt), patch(
        "gamma.voice.roundtrip.AudioUnderstandingService", side_effect=lambda: initialized.append(threading.get_ident()) or perception
    ):
        service = VoiceRoundtripService(conversation=Mock())
        service.save_upload = AsyncMock(return_value=path)
        assert not initialized
        async def run():
            response = await service.run_transcription(audio_file=Mock())
            assert response.transcript == "fixture"
            assert len(initialized) == 2 and all(identity != threading.get_ident() for identity in initialized)
        anyio.run(run)


def make_service(path):
    # Tests exercise real run/cleanup logic without loading inference providers.
    service = VoiceRoundtripService.__new__(VoiceRoundtripService)
    service._transcription_lock = threading.Lock()
    service._transcription_slots = threading.BoundedSemaphore(8)
    service._delivery_slots = threading.BoundedSemaphore(8)
    service.save_upload = AsyncMock(return_value=path)
    service._conversation = Mock()
    service._conversation.respond.return_value = AssistantResponse(spoken_text="Hello")
    return service


def test_roundtrip_transcribes_off_loop_then_uses_brain_queue(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"fixture")
    service = make_service(path)
    worker_ids = []
    service.transcribe_path = lambda source: (worker_ids.append(threading.get_ident()), VoiceTranscriptionResponse(transcript="fixture"))[1]
    conversation_ids = []
    service._conversation.respond.side_effect = lambda *args, **kw: (conversation_ids.append(threading.get_ident()), AssistantResponse(spoken_text="Hello"))[1]
    queue = BrainQueue()
    entered, release = threading.Event(), threading.Event()
    async def run():
        with patch.object(runtime, "_queue", queue):
            held = queue.submit(lambda: (entered.set(), release.wait(5), threading.get_ident())[-1])
            assert await asyncio.to_thread(entered.wait, 3)
            response = asyncio.create_task(service.run(audio_file=Mock(), synthesize_speech=False))
            # The barrier is a bounded wait for actual queued work, not timing an inference.
            deadline = asyncio.get_running_loop().time() + 3
            while queue.status()["pending"] == 0:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            service._conversation.respond.assert_not_called()
            assert not path.exists()
            release.set()
            result = await response
            assert result.transcript == "fixture" and result.reply_text == "Hello"
            assert conversation_ids == [held.result(3)]
            assert worker_ids[0] != threading.get_ident()
            assert service._conversation.respond.call_args.kwargs["synthesize_speech"] is False
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_delivery_frees_brain_and_cancel_retains_provider_slot(tmp_path):
    from gamma.brain.queue import BrainQueueFull
    path = tmp_path / "audio.wav"
    path.write_bytes(b"fixture")
    service = make_service(path)
    service._delivery_slots = threading.BoundedSemaphore(1)
    service.transcribe_path = lambda _: VoiceTranscriptionResponse(transcript="fixture")
    response = AssistantResponse(spoken_text="Hello", tts_metadata={"delivery_grant": "fixture"})
    service._conversation.respond.return_value = response
    entered, release = threading.Event(), threading.Event()
    calls = []
    def deliver(*args, **kwargs):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
    service._conversation.deliver_response.side_effect = deliver
    queue = BrainQueue()
    async def run():
        with patch.object(runtime, "_queue", queue):
            job = asyncio.create_task(service.run(audio_file=Mock()))
            assert await asyncio.to_thread(entered.wait, 3)
            worker = await asyncio.wrap_future(queue.submit(threading.get_ident))
            assert calls[0] != worker
            assert service._conversation.respond.call_args.kwargs["defer_delivery"] is True
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            service._conversation.cancel_delivery.assert_called_with("fixture")
            with pytest.raises(BrainQueueFull):
                await service._deliver_owned(response, synthesize_speech=True)
            assert len(calls) == 1
            release.set()
            deadline = asyncio.get_running_loop().time() + 3
            while not service._delivery_slots.acquire(blocking=False):
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            service._delivery_slots.release()
    try:
        anyio.run(run)
    finally:
        release.set()
        queue.close(timeout=3)


def test_roundtrip_returns_grant_without_exporting_playable_bytes(tmp_path):
    import base64
    path = tmp_path / "input.wav"
    path.write_bytes(b"fixture")
    producer = tmp_path / "producer.wav"
    producer.write_bytes(b"changed producer bytes")
    service = make_service(path)
    service.transcribe_path = lambda _: VoiceTranscriptionResponse(transcript="fixture")
    response = AssistantResponse(spoken_text="Hello", audio_path=str(producer), audio_content_type="audio/wav",
        tts_metadata={"delivery_grant": "fixture-grant", "delivery_audio_url": "/v1/delivery/fixture-grant/audio"})
    service._conversation.respond.return_value = response
    service._conversation.read_delivery_audio.return_value = (b"approved snapshot", "audio/wav", "digest")
    queue = BrainQueue()
    async def run():
        with patch.object(runtime, "_queue", queue):
            result = await service.run(audio_file=Mock())
            assert result.audio_base64 is None
            assert result.delivery_grant == "fixture-grant"
            assert result.delivery_audio_url == "/v1/delivery/fixture-grant/audio"
            service._conversation.read_delivery_audio.assert_not_called()
    try:
        anyio.run(run)
    finally:
        queue.close(timeout=3)


@pytest.mark.parametrize("fails", [False, True])
def test_cancelled_transcription_keeps_upload_until_provider_exits(tmp_path, fails):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"fixture")
    service = make_service(path)
    entered, release, exited = (threading.Event() for _ in range(3))
    def transcribe(source):
        entered.set()
        assert release.wait(5)
        assert source.read_bytes() == b"fixture"
        exited.set()
        if fails:
            raise ValueError("controlled provider failure")
        return VoiceTranscriptionResponse(transcript="fixture")
    service.transcribe_path = transcribe
    async def run():
        response = asyncio.create_task(service.run_transcription(audio_file=Mock()))
        assert await asyncio.to_thread(entered.wait, 3)
        response.cancel()
        with pytest.raises(asyncio.CancelledError):
            await response
        assert path.exists() and not exited.is_set()
        release.set()
        assert await asyncio.to_thread(exited.wait, 3)
        deadline = asyncio.get_running_loop().time() + 3
        while path.exists():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        service._conversation.respond.assert_not_called()
    try:
        anyio.run(run)
    finally:
        release.set()


def test_transcription_failure_cleans_upload_and_never_calls_brain(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"fixture")
    service = make_service(path)
    service.transcribe_path = Mock(side_effect=ValueError("controlled"))
    async def run():
        with pytest.raises(ValueError, match="controlled"):
            await service.run(audio_file=Mock())
    anyio.run(run)
    assert not path.exists()
    service._conversation.respond.assert_not_called()


def test_transcription_overload_rejects_and_cleans_unadmitted_upload(tmp_path):
    from gamma.brain.queue import BrainQueueFull
    path = tmp_path / "fixture.wav"
    path.write_bytes(b"fixture")
    service = make_service(path)
    service.transcribe_path = Mock()
    for _ in range(8):
        assert service._transcription_slots.acquire(blocking=False)
    async def run():
        with pytest.raises(BrainQueueFull):
            await service.run_transcription(audio_file=Mock())
    anyio.run(run)
    assert not path.exists()
    service.transcribe_path.assert_not_called()


def test_cancelled_waiting_transcription_does_not_enter_provider(tmp_path):
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    service = make_service(first)
    service.save_upload.side_effect = [first, second]
    entered, release = threading.Event(), threading.Event()
    calls = []
    def transcribe(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return VoiceTranscriptionResponse(transcript="fixture")
    service.transcribe_path = transcribe
    async def run():
        active = asyncio.create_task(service.run_transcription(audio_file=Mock()))
        assert await asyncio.to_thread(entered.wait, 3)
        waiting = asyncio.create_task(service.run_transcription(audio_file=Mock()))
        await asyncio.sleep(0)
        assert service.save_upload.await_count == 2
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert calls == [first]
        release.set()
        await active
        deadline = asyncio.get_running_loop().time() + 3
        while second.exists():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        assert calls == [first]
        assert not first.exists()
    try:
        anyio.run(run)
    finally:
        release.set()
