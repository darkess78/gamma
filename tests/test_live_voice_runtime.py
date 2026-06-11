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

    def test_simple_chunked_live_voice_turn_routes_through_stream_brain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            stream_brain = _FakeStreamBrain()
            conversation = _FakeConversation()
            payload = _run_simple_chunked(
                started_at=0.0,
                args=argparse.Namespace(turn_id="turn-1", session_id="session-1"),
                transcript="Gamma, can you explain what you are doing right now in one sentence?",
                synthesize_speech=False,
                conversation=conversation,  # type: ignore[arg-type]
                stream_brain=stream_brain,  # type: ignore[arg-type]
                output_path=temp_path / "out.json",
                status_path=temp_path / "status.json",
                status_payload={"status": "running"},
                response_mode="simple_chunked",
                planner_state={"planner_ms": 0.0},
                turn_state=AssistantTurnState(
                    turn_id="turn-1",
                    session_id="session-1",
                    user_text="Gamma, can you explain what you are doing right now in one sentence?",
                    response_mode="simple_chunked",
                ),
            )

        self.assertEqual(payload["transcript"], "Gamma, can you explain what you are doing right now in one sentence?")
        self.assertEqual(payload["reply_text"], "I heard you.")
        self.assertEqual(payload["reply_chunks"], [])
        self.assertEqual(payload["stream"]["input_event"]["kind"], "mic_transcript")
        self.assertEqual(payload["stream"]["decision"]["decision"], "reply")
        self.assertEqual(payload["stream"]["output_events"][1]["type"], "subtitle_line")
        self.assertEqual(stream_brain.decisions[0].text, "Gamma, can you explain what you are doing right now in one sentence?")
        self.assertEqual(stream_brain.recorded[0].assistant_response.spoken_text, "I heard you.")
        self.assertEqual(conversation.calls[0]["kwargs"]["brief_mode"], False)
        self.assertEqual(conversation.calls[0]["kwargs"]["micro_mode"], False)

    def test_incremental_live_voice_turn_records_stream_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            stream_brain = _FakeIncrementalStreamBrain()
            payload = _run_incremental_experimental(
                started_at=0.0,
                args=argparse.Namespace(turn_id="turn-2", session_id="session-2"),
                transcript="Gamma, explain the routing change.",
                synthesize_speech=False,
                conversation=object(),  # type: ignore[arg-type]
                stream_brain=stream_brain,  # type: ignore[arg-type]
                sentence_generator=_FakeSentenceGenerator(),  # type: ignore[arg-type]
                output_path=temp_path / "out.json",
                status_path=temp_path / "status.json",
                status_payload={"status": "running"},
                response_mode="incremental_experimental",
                planner_state={"planner_ms": 2.0, "estimated_sentence_count": 2},
                turn_state=AssistantTurnState(
                    turn_id="turn-2",
                    session_id="session-2",
                    user_text="Gamma, explain the routing change.",
                    response_mode="incremental_experimental",
                ),
            )

        self.assertEqual(payload["reply_text"], "I am routing this through the stream brain. The dashboard payload still works.")
        self.assertEqual(payload["stream"]["input_event"]["kind"], "mic_transcript")
        self.assertEqual(payload["stream"]["decision"]["decision"], "reply")
        self.assertEqual(payload["stream"]["assistant_response"]["emotion"], "happy")
        self.assertEqual(payload["stream"]["output_events"][1]["type"], "subtitle_line")
        self.assertEqual(len(stream_brain.recorded), 1)
        self.assertEqual(stream_brain.recorded[0].assistant_response.spoken_text, payload["reply_text"])

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

    def test_live_job_manager_bridges_reply_chunks_to_performer_bus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bus = PerformerEventBus()
            manager = LiveVoiceJobManager(performer_bus=bus)
            status_path = temp_path / "status.json"
            output_path = temp_path / "output.json"
            status_path.write_text(
                json.dumps({"turn_id": "turn-live", "status": "completed", "created_at": "2026-05-25T00:00:00Z"}),
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(
                    {
                        "turn_id": "turn-live",
                        "status": "completed",
                        "reply_text": "Live voice now reaches the monitor.",
                        "reply_chunks": [
                            {
                                "chunk_index": 1,
                                "text": "Live voice now reaches the monitor.",
                                "audio_content_type": "audio/wav",
                                "audio_base64": "UklGRg==",
                                "is_final": True,
                            }
                        ],
                        "stream": {
                            "input_event": {
                                "event_id": "turn-live",
                                "kind": "mic_transcript",
                                "session_id": "session-1",
                                "actor": {
                                    "source": "local",
                                    "platform_id": "live_voice",
                                    "display_name": "Live Voice",
                                    "roles": ["voice"],
                                },
                            },
                            "assistant_response": {"emotion": "happy"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            manager._jobs["turn-live"] = LiveVoiceJob(  # type: ignore[attr-defined]
                turn_id="turn-live",
                session_id="session-1",
                synthesize_speech=True,
                response_mode="simple_chunked",
                input_path=temp_path / "input.wav",
                output_path=output_path,
                status_path=status_path,
                stdout_log=temp_path / "stdout.log",
                stderr_log=temp_path / "stderr.log",
                created_at="2026-05-25T00:00:00Z",
                process=None,
            )

            response = manager.get_job("turn-live")
            manager.get_job("turn-live")

        self.assertEqual(response.turn_id, "turn-live")
        events = bus.recent(limit=10, target_policy="dashboard_monitor")
        self.assertEqual(
            [event.type for event in events],
            ["turn_started", "turn_state_changed", "subtitle_update", "expression_set", "speech_started", "speech_chunk_ready", "speech_ended"],
        )
        self.assertEqual(events[0].target_policy, "dashboard_monitor")
        self.assertEqual(events[1].payload["state"], "completed")
        self.assertEqual(events[2].payload["input"]["kind"], "mic_transcript")
        self.assertEqual(events[2].payload["actor"]["platform_id"], "live_voice")
        self.assertEqual(events[4].payload["status"], "speaking")
        self.assertEqual(events[5].payload["audio_base64"], "UklGRg==")
        self.assertEqual(events[5].payload["actor"]["roles"], ["voice"])

    def test_live_job_manager_cancellation_clears_monitor_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bus = PerformerEventBus()
            manager = LiveVoiceJobManager(performer_bus=bus)
            status_path = temp_path / "status.json"
            output_path = temp_path / "output.json"
            status_path.write_text(
                json.dumps({"turn_id": "turn-cancel", "status": "cancelled", "cancel_reason": "test-stop"}),
                encoding="utf-8",
            )
            output_path.write_text("{}", encoding="utf-8")
            manager._jobs["turn-cancel"] = LiveVoiceJob(  # type: ignore[attr-defined]
                turn_id="turn-cancel",
                session_id="session-1",
                synthesize_speech=True,
                response_mode="simple_chunked",
                input_path=temp_path / "input.wav",
                output_path=output_path,
                status_path=status_path,
                stdout_log=temp_path / "stdout.log",
                stderr_log=temp_path / "stderr.log",
                created_at="2026-05-25T00:00:00Z",
                process=None,
                cancel_reason="test-stop",
            )

            response = manager.get_job("turn-cancel")
            manager.get_job("turn-cancel")

        self.assertEqual(response.status, "cancelled")
        events = bus.recent(limit=10, target_policy="dashboard_monitor")
        self.assertEqual([event.type for event in events], ["turn_state_changed", "output_cleared"])
        self.assertEqual(events[0].payload["state"], "cancelled")
        self.assertEqual(events[1].payload["reason"], "test-stop")
        self.assertEqual(events[1].payload["actor"]["source"], "dashboard")


if __name__ == "__main__":
    unittest.main()
