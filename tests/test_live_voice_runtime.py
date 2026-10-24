from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from gamma.performer.bus import PerformerEventBus
from gamma.run_live_voice_worker import _run_incremental_experimental, _run_simple_chunked
from gamma.config import settings
from gamma.schemas.response import AssistantResponse
from gamma.schemas.voice import LiveVoiceJobResponse
from gamma.stream.models import StreamTurnResult, TurnDecision
from gamma.voice.live_jobs import LiveVoiceJob, LiveVoiceJobManager
from gamma.voice.live import LiveVoiceSession
from gamma.voice.live_runtime import SubprocessLiveTurnRuntime
from gamma.voice.reply_state import AssistantTurnState


class _FakeStreamBrain:
    def __init__(self) -> None:
        self.decisions = []
        self.recorded: list[StreamTurnResult] = []

    def decide(self, event):
        self.decisions.append(event)
        return TurnDecision(
            decision="reply",
            reason="mic_transcripts_are_direct_turns",
            should_call_conversation=True,
            response_mode="spoken",
        )

    def record_result(self, result: StreamTurnResult) -> None:
        self.recorded.append(result)


class _FakeConversation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def respond(self, text, **kwargs):
        self.calls.append({"text": text, "kwargs": kwargs})
        return AssistantResponse(
            spoken_text="I heard you.",
            emotion="happy",
            motions=["nod"],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class _FakeIncrementalStreamBrain:
    def __init__(self) -> None:
        self.decisions = []
        self.recorded: list[StreamTurnResult] = []

    def decide(self, event):
        self.decisions.append(event)
        return TurnDecision(
            decision="reply",
            reason="mic_transcripts_are_direct_turns",
            should_call_conversation=True,
            response_mode="spoken",
        )

    def record_result(self, result: StreamTurnResult) -> None:
        self.recorded.append(result)


class _FakeSentenceGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate_next_sentence(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"sentence_text": "[happy] I am routing this through the stream brain.", "is_final": False, "generation_ms": 3.0}
        return {"sentence_text": "The dashboard payload still works.", "is_final": True, "generation_ms": 4.0}


class _FakeJobManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def start_job(self, **kwargs):
        self.calls.append(("start_job", kwargs))
        return LiveVoiceJobResponse(turn_id="turn-1", status="queued")

    def get_job(self, turn_id: str):
        self.calls.append(("get_job", {"turn_id": turn_id}))
        return LiveVoiceJobResponse(turn_id=turn_id, status="completed")

    def cancel_job(self, turn_id: str, *, reason: str = "interrupted"):
        self.calls.append(("cancel_job", {"turn_id": turn_id, "reason": reason}))
        return LiveVoiceJobResponse(turn_id=turn_id, status="cancelled", cancel_reason=reason)

    def cancel_active_jobs(self, *, reason: str = "interrupted"):
        self.calls.append(("cancel_active_jobs", {"reason": reason}))
        return [LiveVoiceJobResponse(turn_id="turn-1", status="cancelled", cancel_reason=reason)]

    def get_recent_history(self, *, limit: int = 20):
        self.calls.append(("get_recent_history", {"limit": limit}))
        return [{"turn_id": "turn-1"}]


class LiveVoiceRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_speech_filter_llm_enabled = settings.speech_filter_llm_enabled
        settings.speech_filter_llm_enabled = False

    def tearDown(self) -> None:
        settings.speech_filter_llm_enabled = self._original_speech_filter_llm_enabled

    def test_retired_chunked_worker_cannot_release_output(self) -> None:
        # Legacy entry points cannot release pre-authorized text or raw audio.
        from unittest.mock import Mock
        conversation = Mock()
        with self.assertRaisesRegex(RuntimeError, "retired"):
            _run_simple_chunked(started_at=0, args=None, transcript="hello", synthesize_speech=True,
                conversation=conversation, stream_brain=Mock(), output_path=None, status_path=None,
                status_payload={}, response_mode="simple_chunked", planner_state={}, turn_state=None)
        conversation.respond.assert_not_called()

    def test_retired_incremental_worker_cannot_release_output(self) -> None:
        from unittest.mock import Mock
        generator = Mock()
        with self.assertRaisesRegex(RuntimeError, "retired"):
            _run_incremental_experimental(started_at=0, args=None, transcript="hello", synthesize_speech=True,
                conversation=Mock(), stream_brain=Mock(), sentence_generator=generator, output_path=None,
                status_path=None, status_payload={}, response_mode="incremental_experimental",
                planner_state={}, turn_state=None)
        generator.generate.assert_not_called()

    def test_legacy_manager_starts_owned_core_turn(self) -> None:
        from unittest.mock import AsyncMock, Mock, patch
        from fastapi import UploadFile
        from io import BytesIO
        core = Mock(start_turn=AsyncMock(return_value="owned"))
        manager = LiveVoiceJobManager(performer_bus=Mock())
        upload = UploadFile(filename="fixture.wav", file=BytesIO(b"fixture"))
        with patch.object(manager, "_core_runtime", return_value=core), patch.object(manager, "_spawn_worker") as spawn:
            result = asyncio.run(manager.start_job(audio_file=upload, session_id="s", synthesize_speech=True, turn_id="turn"))
        self.assertEqual(result, "owned")
        core.start_turn.assert_awaited_once()
        spawn.assert_not_called()

    def test_subprocess_live_turn_runtime_delegates_to_manager(self) -> None:
        manager = _FakeJobManager()
        runtime = SubprocessLiveTurnRuntime(manager=manager)  # type: ignore[arg-type]

        started = asyncio.run(
            runtime.start_turn(
                audio_file=object(),  # type: ignore[arg-type]
                session_id="session-1",
                synthesize_speech=True,
                response_mode="simple_chunked",
                turn_id="turn-1",
            )
        )
        fetched = runtime.get_turn("turn-1")
        cancelled = runtime.cancel_turn("turn-1", reason="test")
        active_cancelled = runtime.cancel_active_turns(reason="active-test")
        history = runtime.get_recent_history(limit=5)

        self.assertEqual(started.status, "queued")
        self.assertEqual(fetched.status, "completed")
        self.assertEqual(cancelled.cancel_reason, "test")
        self.assertEqual(active_cancelled[0].cancel_reason, "active-test")
        self.assertEqual(history, [{"turn_id": "turn-1"}])
        self.assertEqual([name for name, _payload in manager.calls], ["start_job", "get_job", "cancel_job", "cancel_active_jobs", "get_recent_history"])

    def test_interrupt_probe_requires_speech_evidence_and_rejects_echo(self) -> None:
        self.assertEqual(
            LiveVoiceSession.evaluate_interrupt_transcript("maybe", minimum_words=2),
            (False, "too_short"),
        )
        self.assertEqual(
            LiveVoiceSession.evaluate_interrupt_transcript("stop", minimum_words=2),
            (True, "speech_confirmed"),
        )
        self.assertEqual(
            LiveVoiceSession.evaluate_interrupt_transcript(
                "I can explain the routing",
                minimum_words=2,
                assistant_text="I can explain the routing now.",
                reject_echo=True,
            ),
            (False, "assistant_echo"),
        )
        self.assertEqual(
            LiveVoiceSession.evaluate_interrupt_transcript(
                "Shana wait a second",
                minimum_words=2,
                assistant_text="The dashboard is connected.",
                reject_echo=True,
            ),
            (True, "speech_confirmed"),
        )

    def test_legacy_json_chunks_cannot_publish(self) -> None:
        from unittest.mock import Mock
        bus = Mock()
        manager = LiveVoiceJobManager(performer_bus=bus)
        with self.assertRaisesRegex(RuntimeError, "retired"):
            manager._publish_live_performer_events(Mock(), status_payload={}, output_payload={"audio_base64": "raw"})
        bus.publish.assert_not_called()

    def test_legacy_manager_cancels_owned_core_turn(self) -> None:
        from unittest.mock import Mock, patch
        core = Mock()
        manager = LiveVoiceJobManager(performer_bus=Mock())
        with patch.object(manager, "_core_runtime", return_value=core):
            manager.cancel_job("turn", reason="operator-stop")
            manager.cancel_active_jobs(reason="shutdown")
        core.cancel_turn.assert_called_once_with("turn", reason="operator-stop")
        core.cancel_active_turns.assert_called_once_with(reason="shutdown")


if __name__ == "__main__":
    unittest.main()
