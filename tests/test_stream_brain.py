from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_stream_route_delegates_to_brain(self) -> None:
        from gamma.api.routes import stream_event

        input_event = StreamInputEvent(kind="chat_message", text="Gamma, hello", session_id="stream-1")
        turn_result = StreamTurnResult(
            input_event=input_event,
            decision=TurnDecision(
                decision="reply",
                reason="chat_message_addresses_gamma_or_has_priority",
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


if __name__ == "__main__":
    unittest.main()
