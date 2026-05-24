from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import anyio

from gamma.config import settings
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import performer_event_from_stream_output
from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamInputEvent, StreamOutputEvent
from gamma.stream.output import JsonlStreamOutputAdapter, PerformerBusOutputAdapter, StreamOutputDispatcher, StreamOutputLogService
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
        self.assertEqual(recent[0]["adapter_payload"], {"subtitle": "Hello.", "clear": False})
        self.assertEqual(recent[1]["adapter_payload"]["event_type"], "emotion_changed")
        self.assertEqual(recent[1]["adapter_payload"]["payload"], {"emotion": "happy"})

    def test_jsonl_adapter_marks_clear_subtitle_and_speech_stop_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = JsonlStreamOutputAdapter(Path(temp_dir) / "outputs.jsonl")
            adapter.handle(StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "", "clear": True}))
            adapter.handle(StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="speech_ended", payload={"interrupted": True}))
            recent = adapter.read_recent(limit=2)

        self.assertEqual(recent[0]["adapter_payload"], {"subtitle": "", "clear": True})
        self.assertEqual(recent[1]["adapter_payload"]["speech"], "ended")
        self.assertTrue(recent[1]["adapter_payload"]["interrupted"])

    def test_stream_output_maps_to_generic_performer_events(self) -> None:
        subtitle = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "Hello."})
        clear = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "", "clear": True})
        emotion = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="emotion_changed", payload={"emotion": "happy"})
        motion = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="avatar_motion", payload={"motion": "wave"})

        subtitle_event = performer_event_from_stream_output(subtitle)
        clear_event = performer_event_from_stream_output(clear)
        emotion_event = performer_event_from_stream_output(emotion)
        motion_event = performer_event_from_stream_output(motion)

        self.assertIsNotNone(subtitle_event)
        self.assertEqual(subtitle_event.type, "subtitle_update")
        self.assertEqual(subtitle_event.payload["text"], "Hello.")
        self.assertEqual(clear_event.type, "subtitle_clear")  # type: ignore[union-attr]
        self.assertEqual(emotion_event.type, "expression_set")  # type: ignore[union-attr]
        self.assertEqual(emotion_event.payload["expression"], "happy")  # type: ignore[union-attr]
        self.assertEqual(motion_event.type, "motion_trigger")  # type: ignore[union-attr]
        self.assertEqual(motion_event.payload["motion"], "wave")  # type: ignore[union-attr]

    def test_performer_speech_event_uses_network_safe_audio_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "tts-test.wav"
            audio_path.write_bytes(b"RIFF")
            with (
                patch.object(settings, "audio_output_dir", Path(temp_dir)),
                patch.object(settings, "shana_public_host", "192.168.1.50"),
                patch.object(settings, "shana_port", 8000),
            ):
                stream_event = StreamOutputEvent(
                    input_event_id="in-1",
                    turn_id="turn-1",
                    type="speech_started",
                    payload={"audio_path": str(audio_path), "audio_content_type": "audio/wav"},
                )
                performer_event = performer_event_from_stream_output(stream_event)

        self.assertEqual(performer_event.type, "speech_started")  # type: ignore[union-attr]
        self.assertNotIn("audio_path", performer_event.payload)  # type: ignore[union-attr]
        self.assertEqual(performer_event.payload["audio_artifact"], "tts-test.wav")  # type: ignore[union-attr]
        self.assertEqual(performer_event.payload["audio_url"], "http://192.168.1.50:8000/v1/audio/artifacts/tts-test.wav")  # type: ignore[union-attr]

    def test_performer_bus_adapter_publishes_recent_events(self) -> None:
        bus = PerformerEventBus()
        adapter = PerformerBusOutputAdapter(bus)
        stream_event = StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "Hello."})

        record = adapter.handle(stream_event)
        recent = bus.recent(limit=5)

        self.assertTrue(record.ok)
        self.assertEqual(record.metadata["performer_event_type"], "subtitle_update")
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].payload["text"], "Hello.")

    def test_performer_bus_replays_recent_events_to_subscriber(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            adapter = PerformerBusOutputAdapter(bus)
            adapter.handle(StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "One"}))
            subscriber_id, queue = await bus.subscribe(replay_recent=1)
            try:
                payload = await queue.get()
            finally:
                bus.unsubscribe(subscriber_id)
            self.assertEqual(payload["type"], "subtitle_update")
            self.assertEqual(payload["payload"]["text"], "One")

        anyio.run(run_case)

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

    def test_performer_recent_events_route_reads_bus(self) -> None:
        from gamma.api.routes import audio_artifact, performer_recent_events

        bus = PerformerEventBus()
        PerformerBusOutputAdapter(bus).handle(
            StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "Monitor this."})
        )
        with patch("gamma.api.routes.get_performer_bus", return_value=bus):
            result = performer_recent_events(limit=5)

        self.assertEqual(result["items"][0]["type"], "subtitle_update")
        self.assertEqual(result["items"][0]["payload"]["text"], "Monitor this.")
        self.assertEqual(result["stats"]["history_count"], 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "tts-test.wav"
            audio_path.write_bytes(b"RIFF")
            with patch.object(settings, "audio_output_dir", Path(temp_dir)):
                file_response = audio_artifact("tts-test.wav")

        self.assertEqual(file_response.media_type, "audio/x-wav")

    def test_stream_output_routes_delegate_to_service(self) -> None:
        from gamma.api.routes import stream_recent_outputs, stream_temp_memory, stream_temp_memory_clear
        from gamma.api.routes import stream_self_goal_approve, stream_self_goal_reject, stream_self_goals, stream_self_goals_clear
        from gamma.dashboard.main import (
            dashboard_stream_pending_queue,
            dashboard_stream_recent_outputs,
            dashboard_stream_stop,
            dashboard_stream_temp_memory,
            dashboard_stream_temp_memory_clear,
            dashboard_stream_self_goal_approve,
            dashboard_stream_self_goal_reject,
            dashboard_stream_self_goals,
            dashboard_stream_self_goals_clear,
        )

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

        dashboard_service.stream_pending_queue.return_value = {"slots": {"ambient": {"event_id": "event-1"}}}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            queue_result = dashboard_stream_pending_queue()

        self.assertEqual(queue_result["slots"]["ambient"]["event_id"], "event-1")
        dashboard_service.stream_pending_queue.assert_called_once_with()

        dashboard_service.stop_stream_speech.return_value = {"decision": {"reason": "stream_stop_requested"}}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            stop_result = dashboard_stream_stop()

        self.assertEqual(stop_result["decision"]["reason"], "stream_stop_requested")
        dashboard_service.stop_stream_speech.assert_called_once_with(reason="dashboard_stop")

        temp_store = Mock()
        temp_store.list_records.return_value = {"items": [{"bucket": "chat_mood"}]}
        temp_store.clear.return_value = {"ok": True, "deleted": 1, "bucket": None}
        with patch("gamma.api.routes.get_stream_temp_memory_store", return_value=temp_store):
            temp_result = stream_temp_memory(limit=5)
            clear_result = stream_temp_memory_clear()

        self.assertEqual(temp_result["items"][0]["bucket"], "chat_mood")
        self.assertEqual(clear_result["deleted"], 1)
        temp_store.list_records.assert_called_once_with(bucket=None, limit=5)
        temp_store.clear.assert_called_once_with(bucket=None)

        dashboard_service.stream_temp_memory.return_value = {"items": [{"bucket": "event_history"}]}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            dashboard_temp_result = dashboard_stream_temp_memory(bucket="chat_mood", limit=3)

        self.assertEqual(dashboard_temp_result["items"][0]["bucket"], "event_history")
        dashboard_service.stream_temp_memory.assert_called_once_with(bucket="chat_mood", limit=3)

        dashboard_service.clear_stream_temp_memory.return_value = {"ok": True, "deleted": 2}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            dashboard_clear_result = dashboard_stream_temp_memory_clear(bucket="chat_mood")

        self.assertEqual(dashboard_clear_result["deleted"], 2)
        dashboard_service.clear_stream_temp_memory.assert_called_once_with(bucket="chat_mood")

        goal_store = Mock()
        goal_record = Mock()
        goal_record.as_payload.return_value = {"id": 7, "status": "approved"}
        goal_store.list_goals.return_value = {"items": [{"id": 7, "status": "proposed"}]}
        goal_store.set_status.return_value = goal_record
        goal_store.clear.return_value = {"ok": True, "cleared": 1}
        with patch("gamma.api.routes.get_stream_self_goal_store", return_value=goal_store):
            goals_result = stream_self_goals(status="proposed", limit=5)
            approve_result = stream_self_goal_approve(7)
            reject_result = stream_self_goal_reject(7)
            clear_goals_result = stream_self_goals_clear()

        self.assertEqual(goals_result["items"][0]["id"], 7)
        self.assertEqual(approve_result["status"], "approved")
        self.assertEqual(reject_result["status"], "approved")
        self.assertEqual(clear_goals_result["cleared"], 1)
        goal_store.list_goals.assert_called_once_with(status="proposed", limit=5)
        self.assertEqual(goal_store.set_status.call_count, 2)
        goal_store.clear.assert_called_once_with()

        dashboard_service.stream_self_goals.return_value = {"items": [{"id": 8}]}
        dashboard_service.set_stream_self_goal_status.return_value = {"id": 8, "status": "approved"}
        dashboard_service.clear_stream_self_goals.return_value = {"ok": True, "cleared": 1}
        with patch("gamma.dashboard.main.get_dashboard_service", return_value=dashboard_service):
            dashboard_goals = dashboard_stream_self_goals(status="proposed", limit=2)
            dashboard_approve = dashboard_stream_self_goal_approve(8)
            dashboard_reject = dashboard_stream_self_goal_reject(8)
            dashboard_clear_goals = dashboard_stream_self_goals_clear()

        self.assertEqual(dashboard_goals["items"][0]["id"], 8)
        self.assertEqual(dashboard_approve["status"], "approved")
        self.assertEqual(dashboard_reject["status"], "approved")
        self.assertEqual(dashboard_clear_goals["cleared"], 1)
        dashboard_service.stream_self_goals.assert_called_once_with(status="proposed", limit=2)
        dashboard_service.set_stream_self_goal_status.assert_any_call(8, status="approve")
        dashboard_service.set_stream_self_goal_status.assert_any_call(8, status="reject")
        dashboard_service.clear_stream_self_goals.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
