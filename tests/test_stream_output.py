from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamInputEvent, StreamOutputEvent
from gamma.stream.output import JsonlStreamOutputAdapter, StreamOutputDispatcher, StreamOutputLogService
from gamma.stream.trace import StreamTraceStore


class _FakeConversation:
    def respond(self, **_kwargs):
        return AssistantResponse(
            spoken_text="Hello.",
            emotion="happy",
            motions=["wave"],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )


class StreamOutputTest(unittest.TestCase):
    def test_jsonl_adapter_persists_subtitle_and_avatar_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = JsonlStreamOutputAdapter(Path(temp_dir) / "outputs.jsonl")
            subtitle = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "Hello."})
            emotion = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="emotion_changed", payload={"emotion": "happy"})

            subtitle_record = adapter.handle(subtitle)
            emotion_record = adapter.handle(emotion)
            recent = adapter.read_recent(limit=10)

        self.assertTrue(subtitle_record.ok)
        self.assertTrue(emotion_record.ok)
        self.assertEqual(recent[0]["adapter_payload"], {"subtitle": "Hello."})
        self.assertEqual(recent[1]["adapter_payload"]["event_type"], "emotion_changed")
        self.assertEqual(recent[1]["adapter_payload"]["payload"], {"emotion": "happy"})

    def test_stream_brain_dispatches_output_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = JsonlStreamOutputAdapter(Path(temp_dir) / "outputs.jsonl")
            brain = StreamBrain(
                conversation=_FakeConversation(),  # type: ignore[arg-type]
                trace_store=StreamTraceStore(Path(temp_dir) / "trace.jsonl"),
                output_dispatcher=StreamOutputDispatcher([adapter]),
            )
            result = brain.handle_event(StreamInputEvent(kind="mic_transcript", text="hello"))
            recent = adapter.read_recent(limit=10)

        self.assertEqual([event.type for event in result.output_events], ["emotion_changed", "subtitle_line", "avatar_motion"])
        self.assertEqual(len(result.output_dispatch["records"]), 3)
        self.assertEqual(len(recent), 3)

    def test_output_log_service_reads_recent_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = JsonlStreamOutputAdapter(Path(temp_dir) / "outputs.jsonl")
            adapter.handle(StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "One"}))
            adapter.handle(StreamOutputEvent(input_event_id="in-2", turn_id="turn-2", type="subtitle_line", payload={"text": "Two"}))
            service = StreamOutputLogService(adapter)

            recent = service.recent_outputs(limit=1)

        self.assertEqual(recent[0]["output_event"]["payload"]["text"], "Two")

    def test_stream_output_routes_delegate_to_service(self) -> None:
        from gamma.api.routes import stream_recent_outputs
        from gamma.dashboard.main import dashboard_stream_recent_outputs

        api_service = Mock()
        api_service.recent_outputs.return_value = [{"output_event": {"type": "subtitle_line"}}]
        with patch("gamma.api.routes.get_stream_output_log_service", return_value=api_service):
            api_result = stream_recent_outputs(limit=4)

        dashboard_service = Mock()
        dashboard_service.stream_recent_outputs.return_value = {"items": [{"output_event": {"type": "subtitle_line"}}]}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            dashboard_result = dashboard_stream_recent_outputs(limit=4)

        self.assertEqual(api_result["items"][0]["output_event"]["type"], "subtitle_line")
        self.assertEqual(dashboard_result["items"][0]["output_event"]["type"], "subtitle_line")
        api_service.recent_outputs.assert_called_once_with(limit=4)
        dashboard_service.stream_recent_outputs.assert_called_once_with(limit=4)


if __name__ == "__main__":
    unittest.main()
