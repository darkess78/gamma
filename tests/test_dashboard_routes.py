from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.dashboard import main
from gamma.dashboard.service import DashboardService
from gamma.schemas.response import AssistantResponse, VisionAnalysis
from gamma.schemas.voice import VoiceRoundtripResponse


class DashboardRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._http_auth_patcher = patch.object(main, "is_authenticated", return_value=True)
        self._ws_auth_patcher = patch.object(main, "websocket_is_authenticated", return_value=True)
        self.mock_service = Mock()
        self._service_patcher = patch.object(main, "get_dashboard_service", return_value=self.mock_service)
        self._http_auth_patcher.start()
        self._ws_auth_patcher.start()
        self._service_patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        self._ws_auth_patcher.stop()
        self._http_auth_patcher.stop()
        self._service_patcher.stop()

    def test_status_routes(self) -> None:
        with patch.object(self.mock_service, "build_status", return_value={"ok": True}) as build_status:
            response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        build_status.assert_called_once_with()

        with patch.object(self.mock_service, "build_runtime_status", return_value={"runtime": "ok"}) as runtime_status:
            response = self.client.get("/api/status/runtime")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"runtime": "ok"})
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
                    response = self.client.post(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), payload)
                method.assert_called_once_with()

    def test_memory_clear_routes(self) -> None:
        with patch.object(self.mock_service, "clear_recent_memory", return_value={"ok": True, "cleared_total": 1}) as method:
            response = self.client.post("/api/memory/clear-recent", json={"minutes": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared_total"], 1)
        method.assert_called_once_with(minutes=10)

        payload = {"ok": True, "cleared_total": 2}
        items = [{"id": 4, "kind": "episodic"}, {"id": 2, "kind": "profile_fact"}]
        with patch.object(self.mock_service, "clear_selected_memory", return_value=payload) as method:
            response = self.client.post("/api/memory/clear-selected", json={"items": items})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared_total"], 2)
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
                    response = self.client.post(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), payload)
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
            response = self.client.get("/api/assistant/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"], settings_payload)
        method.assert_called_once_with()

        save_payload = {"ok": True, "settings": settings_payload, "detail": "saved"}
        with patch.object(self.mock_service, "save_assistant_runtime_settings", return_value=save_payload) as method:
            response = self.client.post("/api/assistant/settings", json=settings_payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"], settings_payload)
        method.assert_called_once_with(settings_payload)

    def test_tts_provider_profile_and_save_routes(self) -> None:
        with patch.object(self.mock_service, "set_tts_provider", return_value={"ok": True, "provider": "stub"}) as method:
            response = self.client.post("/api/providers/tts/select", json={"provider": "stub"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "stub")
        method.assert_called_once_with("stub")

        with patch.object(self.mock_service, "set_tts_profile", return_value={"ok": True, "profile": "test"}) as method:
            response = self.client.post("/api/providers/tts/profile", json={"profile": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"], "test")
        method.assert_called_once_with("test")

        payload = {"id": "new_profile", "provider": "stub", "values": {}}
        with patch.object(self.mock_service, "save_tts_profile", return_value={"ok": True, "profile": payload}) as method:
            response = self.client.post("/api/providers/tts/profile/save", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"], payload)
        method.assert_called_once_with(payload)

    def test_tts_synthesize_route(self) -> None:
        with patch.object(self.mock_service, "synthesize_text", return_value={"ok": True, "filename": "tts-test.wav"}) as method:
            response = self.client.post(
                "/api/providers/tts/synthesize",
                files={"text_file": ("sample.txt", b"hello from dashboard", "text/plain")},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["filename"], "tts-test.wav")
        method.assert_called_once_with("hello from dashboard")

    def test_audio_routes_respect_extension_media_type(self) -> None:
        settings.audio_output_dir.mkdir(parents=True, exist_ok=True)
        test_path = settings.audio_output_dir / "dashboard-test.mp3"
        test_path.write_bytes(b"fake audio")
        try:
            response = self.client.get(f"/api/audio/{test_path.name}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "audio/mpeg")

            delete_response = self.client.delete(f"/api/audio/{test_path.name}")
            self.assertEqual(delete_response.status_code, 200)
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
            response = self.client.post(
                "/api/voice/roundtrip",
                files={"audio_file": ("clip.wav", b"RIFF....", "audio/wav")},
                data={"session_id": "abc", "synthesize_speech": "true"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transcript"], "hello")
        self.assertEqual(roundtrip_service.run.await_count, 1)

    def test_vision_routes(self) -> None:
        analysis = VisionAnalysis(summary="Image summary")
        with patch.object(self.mock_service, "analyze_remote_image", return_value=analysis) as method:
            response = self.client.post(
                "/api/vision/analyze",
                files={"image_file": ("image.png", b"png", "image/png")},
                data={"user_text": "what is this?", "vision_mode": "photo"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "Image summary")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "photo")

        assistant_response = AssistantResponse(spoken_text="Looks good.")
        with patch.object(self.mock_service, "respond_remote_image", return_value=assistant_response) as method:
            response = self.client.post(
                "/api/vision/respond",
                files={"image_file": ("image.png", b"png", "image/png")},
                data={
                    "user_text": "ask gamma",
                    "vision_mode": "screen",
                    "session_id": "sess-1",
                    "synthesize_speech": "false",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["spoken_text"], "Looks good.")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "screen")

    def test_live_voice_websocket(self) -> None:
        async def fake_handle(websocket) -> None:
            await websocket.accept()
            await websocket.send_json({"type": "ready"})
            await websocket.close()

        live_voice_session = Mock()
        live_voice_session.handle = AsyncMock(side_effect=fake_handle)
        with patch.object(main, "get_live_voice_session", return_value=live_voice_session):
            with self.client.websocket_connect("/api/voice/live") as websocket:
                payload = websocket.receive_json()
        self.assertEqual(payload, {"type": "ready"})
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


if __name__ == "__main__":
    unittest.main()
