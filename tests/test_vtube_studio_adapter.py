from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import anyio

from gamma.observability import configure_logging
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.performer.vtube_studio import VTubeStudioAdapter, VTubeStudioAdapterConfig, VTubeStudioClient, VTubeStudioRunner


class VTubeStudioAdapterTest(unittest.TestCase):
    def test_expression_event_maps_to_hotkey_request(self) -> None:
        adapter = VTubeStudioAdapter(
            VTubeStudioAdapterConfig(
                enabled=True,
                expression_hotkeys={"happy": "hotkey-happy"},
                motion_hotkeys={},
            )
        )
        event = PerformerOutputEvent(type="expression_set", turn_id="turn-1", payload={"expression": "happy"})

        action = adapter.handle_event(event)

        self.assertTrue(action.ok)
        self.assertEqual(action.action_type, "hotkey")
        self.assertEqual(action.request["messageType"], "HotkeyTriggerRequest")  # type: ignore[index]
        self.assertEqual(action.request["data"]["hotkeyID"], "hotkey-happy")  # type: ignore[index]
        self.assertEqual(adapter.status()["last_action"]["action_type"], "hotkey")

    def test_missing_mapping_is_safe_noop(self) -> None:
        adapter = VTubeStudioAdapter(VTubeStudioAdapterConfig(enabled=True))
        event = PerformerOutputEvent(type="motion_trigger", turn_id="turn-1", payload={"motion": "wave"})

        action = adapter.handle_event(event)

        self.assertTrue(action.ok)
        self.assertEqual(action.action_type, "no_mapping")
        self.assertIsNone(action.request)

    def test_speaking_state_tracks_speech_events(self) -> None:
        adapter = VTubeStudioAdapter(VTubeStudioAdapterConfig(enabled=True))

        adapter.handle_event(PerformerOutputEvent(type="speech_started", turn_id="turn-1"))
        self.assertTrue(adapter.status()["speaking"])

        adapter.handle_event(PerformerOutputEvent(type="speech_ended", turn_id="turn-1"))
        self.assertFalse(adapter.status()["speaking"])

    def test_client_sends_hotkey_request_over_websocket(self) -> None:
        class _Socket:
            def __init__(self) -> None:
                self.sent = []
                self.closed = False

            async def send(self, payload: str) -> None:
                self.sent.append(payload)

            async def recv(self) -> str:
                if len(self.sent) == 1:
                    return '{"messageType":"AuthenticationResponse","data":{"authenticated":true}}'
                return '{"messageType":"HotkeyTriggerResponse","data":{}}'

            async def close(self) -> None:
                self.closed = True

        async def _run() -> None:
            socket = _Socket()

            async def connect(endpoint: str):
                self.assertEqual(endpoint, "ws://stream-pc:8001")
                return socket

            client = VTubeStudioClient(
                VTubeStudioAdapterConfig(
                    enabled=True,
                    endpoint="ws://stream-pc:8001",
                    auth_token="token",
                )
            )
            with patch("gamma.performer.vtube_studio.websockets") as websockets:
                websockets.connect = connect
                result = await client.send_request(
                    {
                        "apiName": "VTubeStudioPublicAPI",
                        "apiVersion": "1.0",
                        "requestID": "req-1",
                        "messageType": "HotkeyTriggerRequest",
                        "data": {"hotkeyID": "hotkey-happy"},
                    }
                )

            self.assertTrue(result["ok"])
            self.assertEqual(client.status()["request_count"], 2)
            self.assertTrue(client.status()["authenticated"])
            self.assertIn("AuthenticationRequest", socket.sent[0])
            self.assertIn("HotkeyTriggerRequest", socket.sent[1])

        anyio.run(_run)

    def test_client_logs_request_failure_with_traceback_and_redacts_token(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "vtube.jsonl"
                logger = configure_logging(
                    f"vtube-client-failure-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                client = VTubeStudioClient(
                    VTubeStudioAdapterConfig(
                        enabled=True,
                        endpoint="ws://stream-pc:8001",
                        auth_token="private-auth-token",
                    ),
                    logger=logger,
                )
                with patch("gamma.performer.vtube_studio.websockets") as websockets:
                    websockets.connect.side_effect = OSError("connection refused")
                    result = await client.send_request(
                        {
                            "requestID": "req-failure",
                            "messageType": "HotkeyTriggerRequest",
                            "data": {"authenticationToken": "private-auth-token"},
                        }
                    )
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

            self.assertFalse(result["ok"])
            failure = next(record for record in records if record["event"] == "vtube_studio.request.failed")
            self.assertEqual(failure["request_id"], "req-failure")
            self.assertEqual(failure["error_class"], "OSError")
            self.assertIn("Traceback", failure["traceback"])
            self.assertNotIn("private-auth-token", json.dumps(records))

        anyio.run(_run)

    def test_client_logs_api_error_without_request_payload(self) -> None:
        class _Socket:
            async def send(self, payload: str) -> None:
                return None

            async def recv(self) -> str:
                return '{"messageType":"APIError","data":{"message":"hotkey unavailable"}}'

            async def close(self) -> None:
                return None

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "vtube.jsonl"
                logger = configure_logging(
                    f"vtube-api-error-{id(log_path)}",
                    log_path=log_path,
                    stderr=False,
                )
                client = VTubeStudioClient(
                    VTubeStudioAdapterConfig(enabled=True),
                    logger=logger,
                )
                client._socket = _Socket()
                result = await client._send_locked(
                    {
                        "requestID": "request-1",
                        "messageType": "HotkeyTriggerRequest",
                        "data": {"hotkeyID": "sensitive-mapping"},
                    }
                )
                for handler in logger.handlers:
                    handler.flush()
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

            self.assertFalse(result["ok"])
            failure = next(record for record in records if record["event"] == "vtube_studio.api.error")
            self.assertEqual(failure["request_id"], "request-1")
            self.assertEqual(failure["message_type"], "HotkeyTriggerRequest")
            self.assertNotIn("sensitive-mapping", json.dumps(records))

        anyio.run(_run)

    def test_runner_consumes_stream_public_events(self) -> None:
        class _Adapter:
            def __init__(self) -> None:
                self.events = []
                self.client = Mock()

                async def close() -> None:
                    return None

                self.client.close = close

            async def handle_event_async(self, event: PerformerOutputEvent):
                self.events.append(event)
                return None

        async def _run() -> None:
            bus = PerformerEventBus()
            adapter = _Adapter()
            runner = VTubeStudioRunner(bus, adapter)  # type: ignore[arg-type]
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(runner.run_until_stopped)
                await anyio.sleep(0.05)
                bus.publish(PerformerOutputEvent(type="expression_set", turn_id="turn-1", payload={"expression": "happy"}))
                await anyio.sleep(0.05)
                runner.stop()
                await anyio.sleep(0.05)
                task_group.cancel_scope.cancel()

            self.assertEqual([event.turn_id for event in adapter.events], ["turn-1"])
            self.assertEqual(runner.status()["handled_count"], 1)

        anyio.run(_run)

    def test_performer_status_includes_vtube_studio_adapter(self) -> None:
        from gamma.api.routes import performer_status

        bus = Mock()
        bus.recent.return_value = []
        bus.stats.return_value = {"subscriber_count": 0}
        bus.recent_turns.return_value = []
        adapter = VTubeStudioAdapter(VTubeStudioAdapterConfig(enabled=True, endpoint="ws://stream-pc:8001"))
        runner = VTubeStudioRunner(bus, adapter)
        discord = Mock()
        discord.status.return_value = {"enabled": False}

        with (
            patch("gamma.api.routes.get_performer_bus", return_value=bus),
            patch("gamma.api.routes.get_vtube_studio_adapter", return_value=adapter),
            patch("gamma.api.routes.get_vtube_studio_runner", return_value=runner),
            patch("gamma.api.routes.get_discord_runtime", return_value=discord),
        ):
            status = performer_status()

        self.assertTrue(status["adapters"]["vtube_studio"]["enabled"])
        self.assertEqual(status["adapters"]["vtube_studio"]["endpoint"], "ws://stream-pc:8001")
        self.assertFalse(status["adapters"]["vtube_studio"]["runner"]["running"])
        self.assertFalse(status["adapters"]["discord"]["enabled"])


if __name__ == "__main__":
    unittest.main()
