import asyncio
import json
import re

from gamma.voice.live import LiveVoiceSession
from gamma.config import settings
from unittest.mock import Mock


def test_oversized_live_audio_is_discarded_and_next_turn_can_run(monkeypatch):
    monkeypatch.setattr(settings, "voice_upload_max_bytes", 4)
    async def exercise():
        incoming = asyncio.Queue()
        sent, captured = [], []
        for message in (
            {"text": '{"type":"start_turn"}'}, {"bytes": b"1234"}, {"bytes": b"5"},
            {"text": '{"type":"end_turn"}'},
            {"text": '{"type":"start_turn"}'}, {"bytes": b"next"}, {"text": '{"type":"end_turn"}'},
        ):
            incoming.put_nowait(message)
        class Socket:
            async def accept(self): pass
            async def receive(self): return await incoming.get()
            async def send_text(self, text):
                payload = json.loads(text)
                sent.append(payload)
                if payload.get("type") == "turn_result":
                    incoming.put_nowait({"type": "websocket.disconnect"})
        def start(**kwargs):
            captured.append(kwargs["pcm_bytes"])
            return {"turn_id": kwargs["turn_id"], "status": "queued"}
        transcriber = Mock()
        session = LiveVoiceSession(job_starter=start, job_fetcher=lambda turn: {"status": "completed"},
            job_canceler=Mock(), partial_transcriber=transcriber)
        await asyncio.wait_for(session.handle(Socket()), timeout=3)
        assert captured == [b"next"]
        assert sum(item.get("code") == "audio_limit" for item in sent) == 1
        transcriber.assert_not_called()
    asyncio.run(exercise())


def test_oversized_interrupt_probe_never_reaches_recognizer(monkeypatch):
    monkeypatch.setattr(settings, "voice_upload_max_bytes", 4)
    async def exercise():
        incoming = asyncio.Queue()
        incoming.put_nowait({"text": json.dumps({"type": "interrupt_probe", "audio_base64": "MTIzNDU="})})
        sent = []
        class Socket:
            async def accept(self): pass
            async def receive(self): return await incoming.get()
            async def send_text(self, text):
                payload = json.loads(text)
                sent.append(payload)
                if payload.get("type") == "interrupt_probe_result":
                    incoming.put_nowait({"type": "websocket.disconnect"})
        transcriber = Mock()
        await asyncio.wait_for(LiveVoiceSession(job_starter=Mock(), job_fetcher=Mock(), job_canceler=Mock(),
            partial_transcriber=transcriber).handle(Socket()), timeout=3)
        transcriber.assert_not_called()
        result = next(item for item in sent if item["type"] == "interrupt_probe_result")
        assert result["confirmed"] is False
    asyncio.run(exercise())


def test_oversized_control_frame_closes_connection(monkeypatch):
    monkeypatch.setattr(settings, "voice_upload_max_bytes", 4)
    async def exercise():
        closed = []
        class Socket:
            async def accept(self): pass
            async def receive(self): return {"text": "x" * 70000}
            async def send_text(self, text): pass
            async def close(self, *, code): closed.append(code)
        starter = Mock()
        await asyncio.wait_for(LiveVoiceSession(job_starter=starter, job_fetcher=Mock(), job_canceler=Mock()).handle(Socket()), timeout=3)
        assert closed == [1009]
        starter.assert_not_called()
    asyncio.run(exercise())


def test_turn_identity_is_announced_before_provider_starts():
    async def exercise():
        sent = []
        submitted = []
        incoming = asyncio.Queue()
        for message in (
            {"text": json.dumps({"type": "start_turn"})},
            {"bytes": b"synthetic pcm"},
            {"text": json.dumps({"type": "end_turn"})},
        ):
            incoming.put_nowait(message)

        class Socket:
            async def accept(self):
                pass

            async def receive(self):
                return await incoming.get()

            async def send_text(self, text):
                message = json.loads(text)
                sent.append(message)
                if message.get("type") == "turn_result":
                    incoming.put_nowait({"type": "websocket.disconnect"})

        def start(**kwargs):
            submitted.append(kwargs["turn_id"])
            # The assertion runs inside the provider callback, before its result.
            assert sent[-1] == {"type": "turn_submitting", "turn_id": kwargs["turn_id"]}
            assert re.fullmatch(r"[a-f0-9]{32}", kwargs["turn_id"])
            return {"turn_id": kwargs["turn_id"], "status": "queued"}

        session = LiveVoiceSession(job_starter=start,
            job_fetcher=lambda turn_id: {"turn_id": turn_id, "status": "completed", "transcript": "fixture", "reply_text": "reply"},
            job_canceler=lambda *args, **kwargs: {})
        await asyncio.wait_for(session.handle(Socket()), timeout=3)
        assert len(submitted) == 1
        result = next(message for message in sent if message.get("type") == "turn_result")
        assert result["turn_id"] == submitted[0]

    asyncio.run(exercise())


def test_duplicate_final_does_not_submit_or_reset_processing_state():
    async def exercise():
        incoming, sent, submitted = asyncio.Queue(), [], []
        for message in ({"text": '{"type":"start_turn"}'}, {"bytes": b"fixture"},
                        {"text": '{"type":"end_turn"}'}, {"text": '{"type":"end_turn"}'}):
            incoming.put_nowait(message)
        class Socket:
            async def accept(self): pass
            async def receive(self): return await incoming.get()
            async def send_text(self, text):
                payload = json.loads(text)
                sent.append(payload)
                if payload.get("type") == "turn_result":
                    incoming.put_nowait({"type": "websocket.disconnect"})
        def start(**kwargs):
            submitted.append(kwargs)
            return {"turn_id": kwargs["turn_id"], "status": "queued"}
        session = LiveVoiceSession(job_starter=start, job_fetcher=lambda _: {"status": "completed"}, job_canceler=Mock())
        await asyncio.wait_for(session.handle(Socket()), 3)
        assert len(submitted) == 1
        assert submitted[0]["pcm_bytes"] == b"fixture"
        assert not any(item.get("state") == "idle" for item in sent)
    asyncio.run(exercise())


def test_new_capture_cancels_old_turn_and_discards_inflight_poll():
    import threading
    async def exercise():
        incoming, sent, cancelled = asyncio.Queue(), [], []
        entered, release = threading.Event(), threading.Event()
        for message in ({"text": '{"type":"start_turn"}'}, {"bytes": b"fixture"}, {"text": '{"type":"end_turn"}'}):
            incoming.put_nowait(message)
        class Socket:
            async def accept(self): pass
            async def receive(self): return await incoming.get()
            async def send_text(self, text): sent.append(json.loads(text))
        def fetch(_):
            entered.set()
            assert release.wait(3)
            return {"status": "completed", "reply_text": "STALE", "reply_chunks": [
                {"chunk_index": 1, "audio_base64": "c3RhbGU=", "audio_content_type": "audio/wav"}]}
        def cancel(turn, **kwargs):
            cancelled.append((turn, kwargs["reason"]))
            return {"status": "cancelled"}
        session = LiveVoiceSession(job_starter=lambda **kw: {"turn_id": "old", "status": "queued"}, job_fetcher=fetch, job_canceler=cancel)
        task = asyncio.create_task(session.handle(Socket()))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            incoming.put_nowait({"text": '{"type":"start_turn"}'})
            for _ in range(100):
                if len([x for x in sent if x.get("state") == "listening"]) == 2: break
                await asyncio.sleep(.01)
            assert cancelled == [("old", "superseded-by-capture")]
            release.set()
            incoming.put_nowait({"text": '{"type":"ping"}'})
            for _ in range(100):
                if any(x.get("type") == "pong" for x in sent): break
                await asyncio.sleep(.01)
            assert not any(x.get("type") in {"turn_result", "reply_chunk_ready"} for x in sent)
        finally:
            release.set()
            incoming.put_nowait({"type": "websocket.disconnect"})
            await asyncio.wait_for(task, 3)
    asyncio.run(exercise())
