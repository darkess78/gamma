from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamma.config import settings
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.schemas.response import AssistantResponse, ToolCall, ToolExecutionResult
from gamma.stream.actions import ActionPlanner
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent, StreamTurnResult, TurnDecision
from gamma.stream.trace import StreamTraceStore


class _FakeConversation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def respond(self, **kwargs):
        self.calls.append(kwargs)
        return AssistantResponse(
            spoken_text="I heard you.",
            emotion="happy",
            motions=["nod"],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class _FakeToolConversation:
    def respond(self, **_kwargs):
        return AssistantResponse(
            spoken_text="Memory stats are ready.",
            emotion="neutral",
            motions=[],
            tool_calls=[ToolCall(tool="memory_stats", args={})],
            tool_results=[ToolExecutionResult(tool="memory_stats", ok=True, output="{}", metadata={"count": 1})],
            memory_candidates=[],
        )


class _FakeAudioConversation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def respond(self, **kwargs):
        self.calls.append(kwargs)
        return AssistantResponse(
            spoken_text="Audio reply.",
            emotion="happy",
            audio_path="audio.wav",
            audio_content_type="audio/wav",
            motions=[],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class _FakeLongConversation:
    def respond(self, **_kwargs):
        return AssistantResponse(
            spoken_text="one two three four five six seven eight nine ten",
            emotion="happy",
            audio_path="audio.wav",
            audio_content_type="audio/wav",
            motions=[],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class _FakeUnsafeConversation:
    def respond(self, **_kwargs):
        return AssistantResponse(
            spoken_text="You are an idiot.",
            emotion="annoyed",
            motions=["point"],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class _AllowReviewer:
    def review(self, _text: str) -> LLMReviewDecision:
        return LLMReviewDecision(action="allow", reason="safe", confidence=1.0)


class _BlockReviewer:
    def review(self, _text: str) -> LLMReviewDecision:
        return LLMReviewDecision(action="block", reason="unsafe-by-reviewer", confidence=0.9)


class _SlowReviewer:
    def review(self, _text: str) -> LLMReviewDecision:
        time.sleep(0.2)
        return LLMReviewDecision(action="allow", reason="late", confidence=1.0)


class _FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class StreamBrainTest(unittest.TestCase):
    def test_mic_transcript_becomes_reply_and_output_events(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        conversation = _FakeConversation()
        brain = StreamBrain(
            conversation=conversation,  # type: ignore[arg-type]
            trace_store=StreamTraceStore(Path(temp_dir.name) / "trace.jsonl"),
        )
        event = StreamInputEvent(
            kind="mic_transcript",
            text="Gamma, are you there?",
            session_id="live-1",
            actor=StreamActor(source="local", platform_id="owner"),
        )

        result = brain.handle_event(event, synthesize_speech=False, fast_mode=True)

        self.assertEqual(result.decision.decision, "reply")
        self.assertEqual(result.assistant_response.spoken_text if result.assistant_response else None, "I heard you.")
        self.assertEqual(conversation.calls[0]["user_text"], "Gamma, are you there?")
        self.assertEqual(conversation.calls[0]["session_id"], "live-1")
        self.assertEqual(conversation.calls[0]["fast_mode"], True)
        self.assertEqual([item.type for item in result.output_events], ["emotion_changed", "subtitle_line", "avatar_motion"])
        self.assertEqual(result.output_events[0].payload["emotion"], "happy")
        self.assertEqual(result.output_events[1].payload["text"], "I heard you.")

    def test_ambient_chat_is_ignored_without_generation(self) -> None:
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(StreamInputEvent(kind="chat_message", text="just chatting"))

        self.assertEqual(result.decision.decision, "ignore")
        self.assertIsNone(result.assistant_response)
        self.assertEqual(conversation.calls, [])
        self.assertEqual(result.output_events, [])

    def test_moderator_action_escalates_without_generation(self) -> None:
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(StreamInputEvent(kind="moderator_action", text="timeout user"))

        self.assertEqual(result.decision.decision, "moderation_escalation")
        self.assertTrue(result.decision.requires_moderation_review)
        self.assertEqual(conversation.calls, [])

    def test_conversation_lull_is_dry_run_proactive_decision(self) -> None:
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="conversation_lull",
                    session_id="live-1",
                    metadata={
                        "idle_policy_decision": "reply",
                        "idle_policy_reason": "conversation_lull_after_target_silence",
                        "silence_ms": 60000,
                    },
                )
            )

        self.assertEqual(result.decision.decision, "defer")
        self.assertEqual(result.decision.response_mode, "proactive_live")
        self.assertTrue(result.decision.metadata["dry_run"])
        self.assertTrue(result.decision.metadata["would_reply"])
        self.assertEqual(conversation.calls, [])

    def test_stream_brain_adds_action_plan_for_existing_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=_FakeToolConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(StreamInputEvent(kind="mic_transcript", text="memory stats"))

        self.assertEqual(len(result.action_plan.items), 1)
        item = result.action_plan.items[0]
        self.assertEqual(item.action_type, "tool.memory_stats")
        self.assertEqual(item.risk_tier, "low")
        self.assertEqual(item.requires_approval, False)
        self.assertEqual(item.status, "executed")
        self.assertEqual(item.result["ok"], True)

    def test_action_planner_marks_core_memory_high_risk(self) -> None:
        response = AssistantResponse(
            spoken_text="Stored.",
            tool_calls=[ToolCall(tool="save_core_memory", args={"fact": "Gamma has a test fact."})],
            tool_results=[ToolExecutionResult(tool="save_core_memory", ok=True, output="Stored.", metadata={"saved": 1})],
        )

        plan = ActionPlanner().plan_from_response(response)

        self.assertEqual(plan.items[0].risk_tier, "high")
        self.assertEqual(plan.items[0].requires_approval, True)
        self.assertEqual(plan.items[0].status, "executed")

    def test_twitch_subtitle_toggle_suppresses_subtitle_output_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=_FakeConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                    metadata={"twitch_controls": {"subtitles_enabled": False}},
                )
        )

        self.assertEqual(result.decision.decision, "reply")
        self.assertEqual([event.type for event in result.output_events], ["emotion_changed", "avatar_motion"])

    def test_twitch_voice_toggle_suppresses_speech_output_events_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=_FakeAudioConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                    metadata={"twitch_controls": {"voice_enabled": False, "subtitles_enabled": True}},
                ),
                synthesize_speech=True,
            )

        self.assertEqual(result.decision.decision, "reply")
        self.assertEqual([event.type for event in result.output_events], ["emotion_changed", "subtitle_line"])
        self.assertEqual(result.assistant_response.audio_path if result.assistant_response else None, "audio.wav")

    def test_twitch_speech_pacing_defers_second_low_priority_reply(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            first = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )
            clock.value = 102.0
            second = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana again",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                )
            )

        self.assertEqual(first.decision.decision, "reply")
        self.assertEqual(second.decision.decision, "defer")
        self.assertEqual(second.decision.reason, "stream_speech_pacing_deferred")
        self.assertEqual(second.decision.metadata["would_decision"], "reply")
        self.assertEqual(len(conversation.calls), 1)
        queue = brain.pending_queue()
        self.assertEqual(queue["slots"]["ambient"]["event_id"], second.input_event.event_id)
        self.assertEqual(queue["slots"]["ambient"]["kind"], "chat_message")

    def test_twitch_pending_queue_replaces_ambient_slot(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana first",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )
            clock.value = 101.0
            first_deferred = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana second",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                )
            )
            clock.value = 102.0
            second_deferred = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana third",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u3"),
                )
            )

        queue = brain.pending_queue()
        self.assertEqual(queue["slots"]["ambient"]["event_id"], second_deferred.input_event.event_id)
        self.assertEqual(queue["slots"]["ambient"]["replaced_event_id"], first_deferred.input_event.event_id)
        self.assertEqual(second_deferred.decision.metadata["replaced_event_id"], first_deferred.input_event.event_id)

    def test_twitch_pending_queue_uses_high_priority_slot_for_redeem(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )
            clock.value = 101.0
            deferred = brain.handle_event(
                StreamInputEvent(
                    kind="redeem",
                    text="Say hi",
                    priority=10,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                )
            )

        self.assertEqual(deferred.decision.decision, "defer")
        self.assertEqual(deferred.decision.metadata["pending_slot"], "high_priority")
        self.assertEqual(brain.pending_queue()["slots"]["high_priority"]["kind"], "redeem")

    def test_twitch_high_priority_event_bypasses_speech_pacing(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            first = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )
            clock.value = 101.0
            second = brain.handle_event(
                StreamInputEvent(
                    kind="follow",
                    text="Viewer followed the channel.",
                    priority=20,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                )
            )

        self.assertEqual(first.decision.decision, "reply")
        self.assertEqual(second.decision.decision, "acknowledge")
        self.assertEqual(len(conversation.calls), 2)

    def test_twitch_speech_budget_defers_when_minute_budget_is_full(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeLongConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            controls = {
                "voice_enabled": True,
                "min_speech_gap_seconds": 0,
                "max_speech_seconds_per_minute": 6,
            }
            first = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana first",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                    metadata={"twitch_controls": controls},
                ),
                synthesize_speech=True,
            )
            clock.value = 101.0
            second = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana second",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                    metadata={"twitch_controls": controls},
                ),
                synthesize_speech=True,
            )

        self.assertEqual(first.decision.decision, "reply")
        self.assertEqual(second.decision.decision, "defer")
        self.assertEqual(second.decision.reason, "stream_speech_budget_deferred")
        self.assertEqual(second.output_events, [])
        self.assertIn("ambient", brain.pending_queue()["slots"])
        self.assertEqual(second.decision.metadata["max_speech_seconds_per_minute"], 6.0)

    def test_twitch_output_safety_gate_replaces_blocked_reply_with_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=_FakeUnsafeConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana say something spicy",
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )

        self.assertTrue(result.safety_decision["blocked"])
        self.assertEqual(result.safety_decision["action"], "filtered")
        self.assertEqual(result.assistant_response.spoken_text if result.assistant_response else None, "filtered")
        self.assertEqual([event.type for event in result.output_events], ["emotion_changed", "subtitle_line"])
        self.assertEqual(result.output_events[1].payload["text"], "filtered")
        self.assertTrue(result.output_events[1].payload["filtered"])
        self.assertNotIn("idiot", str([event.payload for event in result.output_events]).lower())
        self.assertEqual(result.action_plan.items, [])

    def test_twitch_llm_safety_reviewer_failure_uses_filtered_output(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "speech_filter_llm_enabled", True),
            patch.object(settings, "stream_safety_review_timeout_seconds", 1.0),
        ):
            brain = StreamBrain(
                conversation=_FakeConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
                safety_reviewer=_BlockReviewer(),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana say hello",
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )

        self.assertEqual(result.safety_decision["stage"], "llm")
        self.assertTrue(result.safety_decision["blocked"])
        self.assertEqual(result.safety_decision["action"], "filtered")
        self.assertEqual(result.assistant_response.spoken_text if result.assistant_response else None, "filtered")
        self.assertEqual(result.output_events[1].payload["text"], "filtered")

    def test_twitch_llm_safety_reviewer_timeout_skips_output_without_filtered(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(settings, "speech_filter_llm_enabled", True),
            patch.object(settings, "stream_safety_review_timeout_seconds", 0.01),
            patch.object(settings, "stream_safety_review_timeout_action", "skip"),
        ):
            brain = StreamBrain(
                conversation=_FakeConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
                safety_reviewer=_SlowReviewer(),
            )
            result = brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana say hello",
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )

        self.assertEqual(result.safety_decision["action"], "skip")
        self.assertTrue(result.safety_decision["review_timeout"])
        self.assertFalse(result.safety_decision["blocked"])
        self.assertEqual(result.decision.decision, "defer")
        self.assertEqual(result.decision.reason, "stream_safety_review_timeout")
        self.assertIsNone(result.assistant_response)
        self.assertEqual(result.output_events, [])

    def test_stream_route_delegates_to_brain(self) -> None:
        from gamma.api.routes import stream_event

        input_event = StreamInputEvent(kind="chat_message", text="Gamma, hello", session_id="stream-1")
        turn_result = StreamTurnResult(
            input_event=input_event,
            decision=TurnDecision(
                decision="reply",
                reason="chat_message_addresses_assistant_or_has_priority",
                should_call_conversation=True,
                response_mode="chat",
            ),
            assistant_response=AssistantResponse(spoken_text="Hello."),
        )
        stream_brain = Mock()
        stream_brain.handle_event.return_value = turn_result

        with patch("gamma.api.routes.get_stream_brain", return_value=stream_brain):
            result = stream_event(input_event, synthesize_speech=False, fast_mode=True)

        self.assertEqual(result.input_event.kind, "chat_message")
        self.assertEqual(result.decision.decision, "reply")
        self.assertEqual(result.assistant_response.spoken_text if result.assistant_response else None, "Hello.")
        stream_brain.handle_event.assert_called_once()
        call = stream_brain.handle_event.call_args
        self.assertEqual(call.args[0].kind, "chat_message")
        self.assertEqual(call.kwargs["synthesize_speech"], False)
        self.assertEqual(call.kwargs["fast_mode"], True)

    def test_stream_queue_route_delegates_to_brain(self) -> None:
        from gamma.api.routes import stream_pending_queue

        stream_brain = Mock()
        stream_brain.pending_queue.return_value = {"slots": {"ambient": {"event_id": "event-1"}}}

        with patch("gamma.api.routes.get_stream_brain", return_value=stream_brain):
            result = stream_pending_queue()

        self.assertEqual(result["slots"]["ambient"]["event_id"], "event-1")
        stream_brain.pending_queue.assert_called_once_with()

    def test_stop_stream_clears_pending_queue_and_emits_clear_events(self) -> None:
        clock = _FakeClock(100.0)
        conversation = _FakeConversation()
        with tempfile.TemporaryDirectory() as temp_dir:
            brain = StreamBrain(
                conversation=conversation,  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
            )
            brain._pacer._now = clock  # type: ignore[attr-defined]
            brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana hello",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u1"),
                )
            )
            clock.value = 101.0
            brain.handle_event(
                StreamInputEvent(
                    kind="chat_message",
                    text="Shana again",
                    priority=5,
                    actor=StreamActor(source="twitch", platform_id="u2"),
                )
            )

            self.assertIn("ambient", brain.pending_queue()["slots"])
            result = brain.stop_stream(reason="test_stop")

            self.assertEqual(result.decision.reason, "stream_stop_requested")
            self.assertEqual(brain.pending_queue()["slots"], {})
            self.assertEqual([event.type for event in result.output_events], ["speech_ended", "subtitle_line", "overlay_update"])
            self.assertTrue(result.output_events[0].payload["interrupted"])
            self.assertTrue(result.output_events[1].payload["clear"])
            self.assertEqual(result.decision.metadata["cleared_pending_queue"], True)

    def test_stream_stop_route_delegates_to_brain(self) -> None:
        from gamma.api.routes import stream_stop

        input_event = StreamInputEvent(kind="system", text="stop")
        turn_result = StreamTurnResult(
            input_event=input_event,
            decision=TurnDecision(decision="ignore", reason="stream_stop_requested"),
        )
        stream_brain = Mock()
        stream_brain.stop_stream.return_value = turn_result
        live_runtime = Mock()
        live_runtime.cancel_active_turns.return_value = []

        with (
            patch("gamma.api.routes.get_stream_brain", return_value=stream_brain),
            patch("gamma.api.routes.get_live_turn_runtime", return_value=live_runtime),
        ):
            result = stream_stop(reason="test")

        self.assertEqual(result.decision.reason, "stream_stop_requested")
        live_runtime.cancel_active_turns.assert_called_once_with(reason="stream_stop:test")
        stream_brain.stop_stream.assert_called_once_with(
            reason="test",
            live_cancellations={"cancel_reason": "stream_stop:test", "cancelled_count": 0, "cancelled_turns": []},
        )


if __name__ == "__main__":
    unittest.main()
