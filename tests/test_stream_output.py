from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import anyio

from gamma.config import settings
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent, performer_event_from_stream_output
from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent, StreamOutputEvent, output_events_from_response
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
                patch.object(settings, "shana_public_scheme", "http"),
                patch.object(settings, "shana_public_port", 8000),
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

    def test_filtered_system_audio_is_embedded_for_remote_performers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "filtered.wav"
            audio_path.write_bytes(b"RIFF-filtered")
            with patch.object(settings, "stream_filtered_audio_path", str(audio_path)):
                stream_event = StreamOutputEvent(
                    input_event_id="in-1",
                    turn_id="turn-1",
                    type="speech_started",
                    payload={"audio_path": str(audio_path), "audio_content_type": "audio/wav"},
                )
                performer_event = performer_event_from_stream_output(stream_event)

        self.assertNotIn("audio_path", performer_event.payload)  # type: ignore[union-attr]
        self.assertEqual(performer_event.payload["audio_content_type"], "audio/wav")  # type: ignore[union-attr]
        self.assertTrue(performer_event.payload["audio_base64"])  # type: ignore[union-attr]

    def test_stream_output_events_include_input_actor_context(self) -> None:
        input_event = StreamInputEvent(
            kind="chat_message",
            text="Shana hello",
            session_id="stream-session",
            actor=StreamActor(source="twitch", platform_id="user-123", display_name="Viewer", roles=["subscriber"]),
        )
        response = AssistantResponse(
            spoken_text="Hey Viewer.",
            emotion="happy",
            motions=["wave"],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
        )

        events = output_events_from_response(input_event=input_event, turn_id="turn-context", response=response)
        performer_event = performer_event_from_stream_output(events[1])

        self.assertEqual(events[1].payload["input"]["kind"], "chat_message")
        self.assertEqual(events[1].payload["input"]["session_id"], "stream-session")
        self.assertEqual(events[1].payload["actor"]["source"], "twitch")
        self.assertEqual(events[1].payload["actor"]["platform_id"], "user-123")
        self.assertEqual(events[1].payload["actor"]["roles"], ["subscriber"])
        self.assertEqual(performer_event.payload["actor"]["display_name"], "Viewer")  # type: ignore[union-attr]

    def test_performer_event_honors_stream_output_target_policy(self) -> None:
        stream_event = StreamOutputEvent(
            input_event_id="in-1",
            turn_id="turn-1",
            type="subtitle_line",
            payload={"text": "Private monitor output.", "target_policy": "dashboard_monitor"},
        )

        performer_event = performer_event_from_stream_output(stream_event)

        self.assertEqual(performer_event.target_policy, "dashboard_monitor")  # type: ignore[union-attr]
        self.assertNotIn("target_policy", performer_event.payload)  # type: ignore[union-attr]

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
            self.assertEqual(payload["sequence"], 1)

        anyio.run(run_case)

    def test_performer_bus_assigns_monotonic_sequences(self) -> None:
        bus = PerformerEventBus()
        first = PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "One"})
        second = PerformerOutputEvent(type="subtitle_update", turn_id="turn-2", payload={"text": "Two"})

        bus.publish(first)
        bus.publish(second)
        recent = bus.recent(limit=2)

        self.assertEqual([event.sequence for event in recent], [1, 2])
        self.assertEqual(bus.stats()["last_sequence"], 2)

    def test_performer_bus_updates_spoken_turns(self) -> None:
        bus = PerformerEventBus()
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "Hello."}))
        bus.publish(PerformerOutputEvent(type="speech_started", turn_id="turn-1"))
        bus.publish(PerformerOutputEvent(type="speech_ended", turn_id="turn-1"))

        turns = bus.recent_turns(limit=5)

        self.assertEqual(turns[0]["turn_id"], "turn-1")
        self.assertEqual(turns[0]["status"], "completed")
        self.assertEqual(turns[0]["subtitle"], "Hello.")

    def test_performer_bus_replays_after_sequence(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "One"}))
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-2", payload={"text": "Two"}))

            subscriber_id, queue = await bus.subscribe(replay_recent=10, after_sequence=1)
            try:
                payload = await queue.get()
                recent = bus.recent(limit=10, after_sequence=1)
            finally:
                bus.unsubscribe(subscriber_id)

            self.assertEqual(payload["turn_id"], "turn-2")
            self.assertEqual(payload["sequence"], 2)
            self.assertTrue(queue.empty())
            self.assertEqual([event.turn_id for event in recent], ["turn-2"])

        anyio.run(run_case)

    def test_performer_bus_treats_negative_after_sequence_as_zero(self) -> None:
        bus = PerformerEventBus()
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "One"}))

        recent = bus.recent(limit=10, after_sequence=-10)

        self.assertEqual([event.turn_id for event in recent], ["turn-1"])

    def test_performer_bus_reports_replay_window_gaps(self) -> None:
        bus = PerformerEventBus(history_limit=2)
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-1", payload={"text": "One"}))
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-2", payload={"text": "Two"}))
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="turn-3", payload={"text": "Three"}))

        self.assertEqual(bus.replay_window(), {"first_sequence": 2, "last_sequence": 3})
        self.assertTrue(bus.replay_gap_after(0))
        self.assertFalse(bus.replay_gap_after(1))

    def test_performer_bus_filters_by_target_policy(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="public-1", target_policy="stream_public", payload={"text": "Public"}))
            bus.publish(
                PerformerOutputEvent(
                    type="subtitle_update",
                    turn_id="monitor-1",
                    target_policy="dashboard_monitor",
                    payload={"text": "Monitor only"},
                )
            )

            stream_id, stream_queue = await bus.subscribe(replay_recent=10, target_policy="stream_public")
            monitor_id, monitor_queue = await bus.subscribe(
                replay_recent=10,
                target_policy="dashboard_monitor",
                client_name="gaming_pc_monitor",
                client_host="10.78.78.29",
            )
            try:
                stream_payload = await stream_queue.get()
                monitor_public_payload = await monitor_queue.get()
                monitor_private_payload = await monitor_queue.get()
                stats = bus.stats()
            finally:
                bus.unsubscribe(stream_id)
                bus.unsubscribe(monitor_id)

            self.assertEqual(stream_payload["turn_id"], "public-1")
            self.assertTrue(stream_queue.empty())
            self.assertEqual(monitor_public_payload["turn_id"], "public-1")
            self.assertEqual(monitor_private_payload["turn_id"], "monitor-1")
            self.assertEqual(stats["subscribers_by_target"]["dashboard_monitor"], 1)
            self.assertIn("discord_call", stats["target_policies"])
            self.assertTrue(any(item["client_name"] == "gaming_pc_monitor" and item["client_host"] == "10.78.78.29" for item in stats["subscribers"]))

        anyio.run(run_case)

    def test_performer_bus_keeps_discord_target_separate(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="discord-1", target_policy="discord_call", payload={"text": "Discord only"}))

            stream_id, stream_queue = await bus.subscribe(replay_recent=10, target_policy="stream_public")
            monitor_id, monitor_queue = await bus.subscribe(replay_recent=10, target_policy="dashboard_monitor")
            discord_id, discord_queue = await bus.subscribe(replay_recent=10, target_policy="discord_call")
            try:
                discord_payload = await discord_queue.get()
            finally:
                bus.unsubscribe(stream_id)
                bus.unsubscribe(monitor_id)
                bus.unsubscribe(discord_id)

            self.assertTrue(stream_queue.empty())
            self.assertTrue(monitor_queue.empty())
            self.assertEqual(discord_payload["turn_id"], "discord-1")

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
        from gamma.api.routes import audio_artifact, performer_recent_events, performer_status, performer_target_clear, performer_target_mute, performer_target_unmute

        bus = PerformerEventBus()
        PerformerBusOutputAdapter(bus).handle(
            StreamOutputEvent(input_event_id="in-1", turn_id="turn-1", type="subtitle_line", payload={"text": "Monitor this."})
        )
        bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="monitor-1", target_policy="dashboard_monitor", payload={"text": "Private"}))
        with patch("gamma.api.routes.get_performer_bus", return_value=bus):
            result = performer_recent_events(limit=5)
            after_result = performer_recent_events(limit=5, after_sequence=1)
            status = performer_status()

        self.assertEqual(result["items"][0]["type"], "subtitle_update")
        self.assertEqual(result["items"][0]["payload"]["text"], "Monitor this.")
        self.assertEqual(result["stats"]["history_count"], 2)
        self.assertTrue(status["ok"])
        self.assertEqual(status["recent_event"]["turn_id"], "monitor-1")
        self.assertEqual(status["recent_by_target"]["stream_public"]["turn_id"], "turn-1")
        self.assertEqual(status["recent_by_target"]["dashboard_monitor"]["turn_id"], "monitor-1")
        self.assertIsNone(status["recent_by_target"]["discord_call"])
        self.assertIn("vtube_studio", status["adapters"])
        self.assertEqual(status["stats"]["history_count"], 2)
        self.assertIn("discord_call", status["stats"]["target_policies"])
        self.assertEqual([item["turn_id"] for item in after_result["items"]], ["monitor-1"])
        self.assertFalse(after_result["replay"]["gap"])

        with patch("gamma.api.routes.get_performer_bus", return_value=bus):
            mute_result = performer_target_mute("stream_public", reason="test")
            unmute_result = performer_target_unmute("stream_public", reason="test")
            clear_result = performer_target_clear("stream_public", reason="test")
        self.assertTrue(mute_result["muted"])
        self.assertFalse(unmute_result["muted"])
        self.assertTrue(clear_result["cleared"])

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "tts-test.wav"
            audio_path.write_bytes(b"RIFF")
            with patch.object(settings, "audio_output_dir", Path(temp_dir)):
                file_response = audio_artifact("tts-test.wav")

        self.assertEqual(file_response.media_type, "audio/x-wav")

    def test_performer_bus_muted_target_filters_non_control_events(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            subscriber_id, queue = await bus.subscribe(replay_recent=0, target_policy="stream_public")
            try:
                bus.set_target_muted("stream_public", True, reason="test")
                clear_event = await queue.get()
                bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="muted-1", target_policy="stream_public", payload={"text": "Hidden"}))
                bus.set_target_muted("stream_public", False, reason="test")
                unmute_event = await queue.get()
            finally:
                bus.unsubscribe(subscriber_id)

            self.assertEqual(clear_event["type"], "output_cleared")
            self.assertEqual(clear_event["payload"]["muted"], True)
            self.assertTrue(queue.empty())
            self.assertEqual(unmute_event["type"], "target_mute_changed")
            self.assertEqual(unmute_event["payload"]["muted"], False)

        anyio.run(run_case)

    def test_performer_bus_clear_target_does_not_mute(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            subscriber_id, queue = await bus.subscribe(replay_recent=0, target_policy="stream_public")
            try:
                result = bus.clear_target("stream_public", reason="test")
                clear_event = await queue.get()
            finally:
                bus.unsubscribe(subscriber_id)

            self.assertTrue(result["cleared"])
            self.assertEqual(result["stats"]["muted_targets"], [])
            self.assertEqual(clear_event["type"], "output_cleared")
            self.assertEqual(clear_event["payload"]["reason"], "test")

        anyio.run(run_case)

    def test_performer_bus_muted_target_filters_replay(self) -> None:
        async def run_case() -> None:
            bus = PerformerEventBus()
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="before-mute", target_policy="stream_public", payload={"text": "Old"}))
            bus.set_target_muted("stream_public", True, reason="test")
            bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="during-mute", target_policy="stream_public", payload={"text": "Hidden"}))

            subscriber_id, queue = await bus.subscribe(replay_recent=10, target_policy="stream_public")
            try:
                replay = await queue.get()
                recent = bus.recent(limit=10, target_policy="stream_public")
            finally:
                bus.unsubscribe(subscriber_id)

            self.assertEqual(replay["type"], "output_cleared")
            self.assertTrue(queue.empty())
            self.assertEqual([event.type for event in recent], ["output_cleared"])

        anyio.run(run_case)

    def test_performer_bus_persists_muted_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "performer-state.json"
            bus = PerformerEventBus(state_path=state_path)
            result = bus.set_target_muted("stream_public", True, reason="test")
            reloaded = PerformerEventBus(state_path=state_path)

            self.assertTrue(result["muted"])
            self.assertEqual(reloaded.stats()["muted_targets"], ["stream_public"])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["muted_targets"], ["stream_public"])

            reloaded.set_target_muted("stream_public", False, reason="test")
            self.assertEqual(PerformerEventBus(state_path=state_path).stats()["muted_targets"], [])

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
