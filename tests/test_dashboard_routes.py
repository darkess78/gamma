from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import anyio

from gamma.config import settings
from gamma.dashboard import main
from gamma.dashboard.service import DashboardService
from gamma.schemas.response import AssistantResponse, VisionAnalysis
from gamma.schemas.voice import VoiceRoundtripResponse


class _JsonRequest:
    def __init__(self, payload: dict, *, content_type: str = "application/json") -> None:
        self._payload = payload
        self.headers = {"content-type": content_type}

    async def json(self) -> dict:
        return self._payload


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, *args, **kwargs) -> None:
        self.closed = True


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes, content_type: str) -> None:
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


def _upload_file(filename: str, content: bytes, content_type: str) -> _FakeUploadFile:
    return _FakeUploadFile(filename, content, content_type)


class DashboardRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._http_auth_patcher = patch.object(main, "is_authenticated", return_value=True)
        self._ws_auth_patcher = patch.object(main, "websocket_is_authenticated", return_value=True)
        self.mock_service = Mock()
        self._service_patcher = patch.object(main, "get_dashboard_service", return_value=self.mock_service)
        self._http_auth_patcher.start()
        self._ws_auth_patcher.start()
        self._service_patcher.start()

    def tearDown(self) -> None:
        self._ws_auth_patcher.stop()
        self._http_auth_patcher.stop()
        self._service_patcher.stop()

    def test_status_routes(self) -> None:
        with patch.object(self.mock_service, "build_status", return_value={"ok": True}) as build_status:
            response = main.status()
        self.assertEqual(response, {"ok": True})
        build_status.assert_called_once_with()

        with patch.object(self.mock_service, "build_runtime_status", return_value={"runtime": "ok"}) as runtime_status:
            response = main.runtime_status()
        self.assertEqual(response, {"runtime": "ok"})
        runtime_status.assert_called_once_with()

    def test_toolbar_actions(self) -> None:
        action_map = {
            "/api/shana/start": ("start_shana", {"ok": True, "detail": "started"}),
            "/api/shana/stop": ("stop_shana", {"ok": True, "detail": "stopped"}),
            "/api/shana/restart": ("restart_shana", {"ok": True, "detail": "restarted"}),
            "/api/dashboard/stop": ("stop_dashboard", {"ok": True, "detail": "dashboard-stop-scheduled"}),
            "/api/all/stop": ("stop_all", {"ok": True, "detail": "all-stop-scheduled"}),
        }
        for path, (method_name, payload) in action_map.items():
            with self.subTest(path=path):
                with patch.object(self.mock_service, method_name, return_value=payload) as method:
                    response = getattr(main, method_name)()
                self.assertEqual(response, payload)
                method.assert_called_once_with()

    def test_twitch_worker_routes(self) -> None:
        action_map = {
            "twitch_worker_status": {"process": {"running": False}, "configured": False},
            "start_twitch_worker": {"ok": False, "auth_required": True},
            "stop_twitch_worker": {"ok": True, "detail": "not-running"},
            "twitch_eventsub_status": {"process": {"running": False}, "configured": False},
            "start_twitch_eventsub_worker": {"ok": False, "auth_required": True},
            "stop_twitch_eventsub_worker": {"ok": True, "detail": "not-running"},
        }
        for method_name, payload in action_map.items():
            with self.subTest(method_name=method_name):
                with patch.object(self.mock_service, method_name, return_value=payload) as method:
                    response = getattr(main, method_name)()
                self.assertEqual(response, payload)
                method.assert_called_once_with()

        settings_payload = {"dry_run": True, "voice_enabled": False}
        with patch.object(self.mock_service, "twitch_runtime_settings", return_value=settings_payload) as method:
            response = main.twitch_settings()
        self.assertEqual(response["settings"], settings_payload)
        method.assert_called_once_with()

        save_payload = {"dry_run": False, "voice_enabled": True}
        with patch.object(self.mock_service, "save_twitch_runtime_settings", return_value={"ok": True, "settings": save_payload}) as method:
            response = anyio.run(main.save_twitch_settings, _JsonRequest(save_payload))
        self.assertEqual(response["settings"], save_payload)
        method.assert_called_once_with(save_payload)

    def test_stream_ready_status_reports_filtered_audio_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "filtered.wav"
            audio_path.write_bytes(b"RIFF")
            with (
                patch.object(settings, "stream_filtered_audio_path", str(audio_path)),
                patch.object(DashboardService, "_probe_json", return_value={"ok": True}),
            ):
                payload = DashboardService().stream_ready_status()

        self.assertIn(payload["mode"], {"offline_replay_ready", "twitch_connect_ready", "dry_run_connected", "voice_ready", "not_ready"})
        self.assertTrue(payload["safety_gate"]["enabled"])
        self.assertTrue(payload["filtered_audio"]["exists"])
        self.assertEqual(payload["filtered_audio"]["resolved_path"], str(audio_path))
        self.assertTrue(any(check["id"] == "filtered_audio" and check["status"] == "ok" for check in payload["checks"]))

    def test_stream_ready_status_reports_blocking_preflight_issues(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", ""),
            patch.object(settings, "twitch_bot_username", ""),
            patch.object(settings, "twitch_oauth_token", ""),
            patch.object(settings, "twitch_client_id", ""),
            patch.object(settings, "twitch_broadcaster_user_id", ""),
            patch.object(settings, "api_auth_enabled", True),
            patch.object(settings, "api_bearer_token", ""),
            patch.object(DashboardService, "_probe_json", return_value={"ok": False, "detail": "unreachable"}),
        ):
            payload = service.stream_ready_status()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["mode"], "not_ready")
        self.assertGreaterEqual(payload["blocker_count"], 4)
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["api"]["status"], "block")
        self.assertEqual(checks["irc_config"]["status"], "block")
        self.assertEqual(checks["eventsub_config"]["status"], "block")
        self.assertEqual(checks["api_auth"]["status"], "block")

    def test_stream_ready_status_reports_twitch_connect_ready(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_dry_run", True),
            patch.object(DashboardService, "_probe_json", return_value={"ok": True}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": False}}),
        ):
            payload = service.stream_ready_status()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "twitch_connect_ready")
        self.assertIn("Start the IRC and EventSub workers", payload["next_step"])

    def test_stream_ready_status_marks_stale_worker_state(self) -> None:
        service = DashboardService()
        stale_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_eventsub_enabled", True),
            patch.object(DashboardService, "_probe_json", return_value={"ok": True}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": True}}),
            patch("gamma.dashboard.service.read_twitch_worker_state", return_value={"connected": True, "updated_at": stale_at, "message_count": 2}),
            patch("gamma.dashboard.service.read_twitch_eventsub_state", return_value={"connected": True, "updated_at": stale_at, "notification_count": 1}),
        ):
            payload = service.stream_ready_status()

        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["irc_runtime"]["status"], "warn")
        self.assertTrue(checks["irc_runtime"]["stale"])
        self.assertGreater(checks["irc_runtime"]["evidence"]["age_seconds"], 120)

    def test_stream_ready_status_warns_on_post_errors_and_voice_enabled(self) -> None:
        service = DashboardService()
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with (
            patch.object(settings, "twitch_channel", "shana"),
            patch.object(settings, "twitch_bot_username", "bot"),
            patch.object(settings, "twitch_oauth_token", "oauth:test"),
            patch.object(settings, "twitch_client_id", "client"),
            patch.object(settings, "twitch_broadcaster_user_id", "broadcaster"),
            patch.object(settings, "twitch_eventsub_enabled", True),
            patch.object(settings, "twitch_voice_enabled", True),
            patch("gamma.dashboard.service.load_app_file_config", return_value={"twitch_voice_enabled": True}),
            patch.object(DashboardService, "_probe_json", return_value={"ok": True}),
            patch.object(service._process_manager, "module_status", return_value={"process": {"running": True}}),
            patch(
                "gamma.dashboard.service.read_twitch_worker_state",
                return_value={"connected": True, "updated_at": updated_at, "message_count": 2, "last_post_error": "api down"},
            ),
            patch(
                "gamma.dashboard.service.read_twitch_eventsub_state",
                return_value={"connected": True, "updated_at": updated_at, "notification_count": 1, "last_post_error": "api down"},
            ),
        ):
            payload = service.stream_ready_status()

        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(checks["irc_posting"]["status"], "warn")
        self.assertEqual(checks["eventsub_posting"]["status"], "warn")
        self.assertEqual(checks["voice_disabled_for_validation"]["status"], "warn")

    def test_twitch_status_reports_missing_config(self) -> None:
        service = DashboardService()
        with (
            patch.object(settings, "twitch_channel", ""),
            patch.object(settings, "twitch_bot_username", ""),
            patch.object(settings, "twitch_oauth_token", ""),
            patch.object(settings, "twitch_client_id", ""),
            patch.object(settings, "twitch_broadcaster_user_id", ""),
        ):
            irc = service.twitch_worker_status()
            eventsub = service.twitch_eventsub_status()

        self.assertIn("twitch_channel", irc["missing_config"])
        self.assertIn("twitch_bot_username", irc["missing_config"])
        self.assertIn("twitch_client_id", eventsub["missing_config"])
        self.assertIn("twitch_broadcaster_user_id", eventsub["missing_config"])

    def test_twitch_viewer_trust_routes(self) -> None:
        trust_payload = {"items": [{"platform_user_id": "u1", "trust_level": "trusted"}], "trust_levels": ["trusted"]}
        with patch.object(self.mock_service, "twitch_viewer_trust", return_value=trust_payload) as method:
            response = main.twitch_viewer_trust(limit=12)
        self.assertEqual(response, trust_payload)
        method.assert_called_once_with(limit=12)

        save_payload = {"platform_user_id": "u1", "trust_level": "trusted"}
        with patch.object(self.mock_service, "save_twitch_viewer_trust", return_value={"ok": True, "record": save_payload}) as method:
            response = anyio.run(main.save_twitch_viewer_trust, _JsonRequest(save_payload))
        self.assertEqual(response["record"], save_payload)
        method.assert_called_once_with(save_payload)

    def test_twitch_replay_route(self) -> None:
        payload = {"jsonl": '{"kind":"chat_message","text":"Shana hi"}'}
        result = {"ok": True, "count": 1, "results": []}
        with patch.object(self.mock_service, "run_twitch_replay", return_value=result) as method:
            response = anyio.run(main.run_twitch_replay, _JsonRequest(payload))
        self.assertEqual(response, result)
        method.assert_called_once_with(payload)

        dry_run_result = {"ok": True, "scenario": "dry_run_readiness", "count": 10, "results": []}
        with patch.object(self.mock_service, "run_twitch_dry_run_replay", return_value=dry_run_result) as method:
            response = main.run_twitch_dry_run_replay()
        self.assertEqual(response, dry_run_result)
        method.assert_called_once_with()

    def test_twitch_dry_run_replay_uses_builtin_safe_settings(self) -> None:
        service = DashboardService()
        replay_results = [
            {
                "input_event": {"kind": "chat_message", "metadata": {"input_safety": {"category": "normal"}}},
                "decision": {"decision": "reply"},
            },
            {
                "input_event": {"kind": "chat_message", "metadata": {"input_safety": {"category": "spam_or_scam"}}},
                "decision": {"decision": "acknowledge"},
            },
        ]
        with patch("gamma.dashboard.service.replay_jsonl_text", return_value=replay_results) as replay:
            payload = service.run_twitch_dry_run_replay()

        self.assertEqual(payload["scenario"], "dry_run_readiness")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["summary"]["event_count"], 2)
        self.assertEqual(payload["summary"]["decisions_by_kind"]["chat_message"]["reply"], 1)
        self.assertEqual(payload["summary"]["safety_categories"]["spam_or_scam"], 1)
        self.assertEqual(service._latest_twitch_replay_summary["scenario"], "dry_run_readiness")
        _, kwargs = replay.call_args
        self.assertEqual(kwargs["session_id"], "twitch-dry-run-readiness")
        self.assertFalse(kwargs["synthesize_speech"])
        self.assertTrue(kwargs["fast_mode"])

    def test_memory_clear_routes(self) -> None:
        with patch.object(self.mock_service, "clear_recent_memory", return_value={"ok": True, "cleared_total": 1}) as method:
            response = anyio.run(main.clear_recent_memory, _JsonRequest({"minutes": 10}))
        self.assertEqual(response["cleared_total"], 1)
        method.assert_called_once_with(minutes=10)

        payload = {"ok": True, "cleared_total": 2}
        items = [{"id": 4, "kind": "episodic"}, {"id": 2, "kind": "profile_fact"}]
        with patch.object(self.mock_service, "clear_selected_memory", return_value=payload) as method:
            response = anyio.run(main.clear_selected_memory, _JsonRequest({"items": items}))
        self.assertEqual(response["cleared_total"], 2)
        method.assert_called_once_with(items)

    def test_provider_test_buttons(self) -> None:
        action_map = {
            "/api/providers/llm/test": ("test_llm", {"status": "ok"}),
            "/api/providers/stt/test": ("test_stt", {"status": "ok"}),
            "/api/providers/tts/test": ("test_tts", {"status": "ok"}),
            "/api/providers/voice/test": ("test_voice_roundtrip", {"status": "ok"}),
            "/api/providers/tts/start": ("start_tts", {"ok": True}),
            "/api/providers/tts/stop": ("stop_tts", {"ok": True}),
        }
        for path, (method_name, payload) in action_map.items():
            with self.subTest(path=path):
                with patch.object(self.mock_service, method_name, return_value=payload) as method:
                    response = getattr(main, method_name)()
                self.assertEqual(response, payload)
                method.assert_called_once_with()

    def test_assistant_settings_routes(self) -> None:
        settings_payload = {
            "speech_filter_level": "strict",
            "assistant_state_enabled": True,
            "llm_router_profile": "balanced",
            "llm_router_allow_hosted_escalation": True,
            "llm_router_chat_light_max_input_words": 32,
            "llm_router_complex_max_input_words": 140,
            "llm_router_persona_hosted_fallback_enabled": False,
            "llm_router_persona_heavy_hosted_fallback_enabled": True,
        }
        with patch.object(self.mock_service, "assistant_runtime_settings", return_value=settings_payload) as method:
            response = main.assistant_settings()
        self.assertEqual(response["settings"], settings_payload)
        method.assert_called_once_with()

        save_payload = {"ok": True, "settings": settings_payload, "detail": "saved"}
        with patch.object(self.mock_service, "save_assistant_runtime_settings", return_value=save_payload) as method:
            response = anyio.run(main.save_assistant_settings, _JsonRequest(settings_payload))
        self.assertEqual(response["settings"], settings_payload)
        method.assert_called_once_with(settings_payload)

    def test_tts_provider_profile_and_save_routes(self) -> None:
        with patch.object(self.mock_service, "set_tts_provider", return_value={"ok": True, "provider": "stub"}) as method:
            response = anyio.run(main.select_tts_provider, _JsonRequest({"provider": "stub"}))
        self.assertEqual(response["provider"], "stub")
        method.assert_called_once_with("stub")

        with patch.object(self.mock_service, "set_tts_profile", return_value={"ok": True, "profile": "test"}) as method:
            response = anyio.run(main.select_tts_profile, _JsonRequest({"profile": "test"}))
        self.assertEqual(response["profile"], "test")
        method.assert_called_once_with("test")

        payload = {"id": "new_profile", "provider": "stub", "values": {}}
        with patch.object(self.mock_service, "save_tts_profile", return_value={"ok": True, "profile": payload}) as method:
            response = anyio.run(main.save_tts_profile, _JsonRequest(payload))
        self.assertEqual(response["profile"], payload)
        method.assert_called_once_with(payload)

    def test_tts_synthesize_route(self) -> None:
        with patch.object(self.mock_service, "synthesize_text", return_value={"ok": True, "filename": "tts-test.wav"}) as method:
            response = anyio.run(
                main.tts_synthesize_file,
                _upload_file("sample.txt", b"hello from dashboard", "text/plain"),
            )
        self.assertEqual(response["filename"], "tts-test.wav")
        method.assert_called_once_with("hello from dashboard")

    def test_audio_routes_respect_extension_media_type(self) -> None:
        settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
        test_path = settings.audio_output_dir / "dashboard-test.mp3"
        test_path.write_bytes(b"fake audio")
        try:
            response = main.serve_audio(test_path.name)
            self.assertEqual(response.media_type, "audio/mpeg")

            delete_response = main.delete_audio(test_path.name)
            self.assertEqual(delete_response["ok"], True)
            self.assertFalse(test_path.exists())
        finally:
            test_path.unlink(missing_ok=True)

    def test_browser_voice_roundtrip_route(self) -> None:
        payload = VoiceRoundtripResponse(
            transcript="hello",
            reply_text="hi there",
            audio_content_type="audio/wav",
            audio_base64="dGVzdA==",
            timing_ms={"total_ms": 1.0},
        )
        roundtrip_service = Mock()
        roundtrip_service.run = AsyncMock(return_value=payload)
        with patch.object(main, "get_voice_roundtrip_service", return_value=roundtrip_service):
            response = anyio.run(
                main.dashboard_voice_roundtrip,
                _upload_file("clip.wav", b"RIFF....", "audio/wav"),
                "abc",
                True,
            )
        self.assertEqual(response.transcript, "hello")
        self.assertEqual(roundtrip_service.run.await_count, 1)

    def test_vision_routes(self) -> None:
        analysis = VisionAnalysis(summary="Image summary")
        with patch.object(self.mock_service, "analyze_remote_image", return_value=analysis) as method:
            response = anyio.run(
                main.dashboard_vision_analyze,
                _upload_file("image.png", b"png", "image/png"),
                "what is this?",
                "photo",
            )
        self.assertEqual(response.summary, "Image summary")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "photo")

        assistant_response = AssistantResponse(spoken_text="Looks good.")
        with patch.object(self.mock_service, "respond_remote_image", return_value=assistant_response) as method:
            response = anyio.run(
                main.dashboard_vision_respond,
                _upload_file("image.png", b"png", "image/png"),
                "ask gamma",
                "screen",
                "sess-1",
                False,
            )
        self.assertEqual(response.spoken_text, "Looks good.")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "screen")

    def test_live_voice_websocket(self) -> None:
        async def fake_handle(websocket) -> None:
            await websocket.accept()
            await websocket.send_json({"type": "ready"})
            await websocket.close()

        live_voice_session = Mock()
        live_voice_session.handle = AsyncMock(side_effect=fake_handle)
        with patch.object(main, "get_live_voice_session", return_value=live_voice_session):
            websocket = _FakeWebSocket()
            anyio.run(main.dashboard_live_voice, websocket)
        self.assertEqual(websocket.sent, [{"type": "ready"}])
        self.assertEqual(live_voice_session.handle.await_count, 1)

    def test_router_capability_status_includes_hosted_and_local_scopes(self) -> None:
        service = DashboardService()
        with patch.object(settings, "openai_api_key", "test-key"):
            entries = service._build_router_capability_status(
                {
                    "provider": "local",
                    "health": {"ok": True, "detail": "ready"},
                    "vision_capability": {"ok": False, "detail": "vision-disabled-in-config"},
                    "router_hosted_provider": "openai",
                }
            )
        scopes = {(entry["provider"], entry["scope"]) for entry in entries}
        self.assertIn(("local", "text"), scopes)
        self.assertIn(("local", "vision"), scopes)
        self.assertIn(("openai", "text"), scopes)

    def test_assistant_runtime_settings_reads_config_without_error(self) -> None:
        service = DashboardService()
        payload = service.assistant_runtime_settings()
        self.assertIn("speech_filter_level", payload)
        self.assertIn("llm_router_profile", payload)
        self.assertIn("assistant_state_enabled", payload)

    def test_dashboard_file_helpers_read_only_recent_entries(self) -> None:
        service = DashboardService()
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            stdout_log = runtime_dir / "shana.stdout.log"
            stdout_log.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

            timings_log = runtime_dir / "conversation.timings.jsonl"
            for index in range(6):
                payload = {"timing_ms": {"total_ms": index + 1}}
                with timings_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\n")

            routes_log = runtime_dir / "llm.routes.jsonl"
            for index in range(8):
                payload = {"status": "ok", "provider": "local", "route_family": f"family-{index}", "duration_ms": index + 0.5}
                with routes_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload) + "\n")

            with patch.object(settings, "data_dir", Path(temp_dir)):
                self.assertEqual(service._tail(stdout_log, limit=2), "three\nfour")
                timings = service._recent_timings(limit=3)
                routes = service._recent_llm_routes(limit=4)

        self.assertEqual([entry["timing_ms"]["total_ms"] for entry in timings["entries"]], [4, 5, 6])
        self.assertEqual([entry["route_family"] for entry in routes["entries"]], ["family-4", "family-5", "family-6", "family-7"])


if __name__ == "__main__":
    unittest.main()
