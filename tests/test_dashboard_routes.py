from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.dashboard import main
from gamma.schemas.response import AssistantResponse, VisionAnalysis
from gamma.schemas.voice import VoiceRoundtripResponse


class DashboardRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._http_auth_patcher = patch.object(main, "is_authenticated", return_value=True)
        self._ws_auth_patcher = patch.object(main, "websocket_is_authenticated", return_value=True)
        self._http_auth_patcher.start()
        self._ws_auth_patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        self._ws_auth_patcher.stop()
        self._http_auth_patcher.stop()

    def test_status_routes(self) -> None:
        with patch.object(main.service, "build_status", return_value={"ok": True}) as build_status:
            response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        build_status.assert_called_once_with()

        with patch.object(main.service, "build_runtime_status", return_value={"runtime": "ok"}) as runtime_status:
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
                with patch.object(main.service, method_name, return_value=payload) as method:
                    response = self.client.post(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), payload)
                method.assert_called_once_with()

    def test_memory_clear_routes(self) -> None:
        with patch.object(main.service, "clear_recent_memory", return_value={"ok": True, "cleared_total": 1}) as method:
            response = self.client.post("/api/memory/clear-recent", json={"minutes": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleared_total"], 1)
        method.assert_called_once_with(minutes=10)

        payload = {"ok": True, "cleared_total": 2}
        items = [{"id": 4, "kind": "episodic"}, {"id": 2, "kind": "profile_fact"}]
        with patch.object(main.service, "clear_selected_memory", return_value=payload) as method:
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
                with patch.object(main.service, method_name, return_value=payload) as method:
                    response = self.client.post(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), payload)
                method.assert_called_once_with()

    def test_tts_provider_profile_and_save_routes(self) -> None:
        with patch.object(main.service, "set_tts_provider", return_value={"ok": True, "provider": "stub"}) as method:
            response = self.client.post("/api/providers/tts/select", json={"provider": "stub"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "stub")
        method.assert_called_once_with("stub")

        with patch.object(main.service, "set_tts_profile", return_value={"ok": True, "profile": "test"}) as method:
            response = self.client.post("/api/providers/tts/profile", json={"profile": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"], "test")
        method.assert_called_once_with("test")

        payload = {"id": "new_profile", "provider": "stub", "values": {}}
        with patch.object(main.service, "save_tts_profile", return_value={"ok": True, "profile": payload}) as method:
            response = self.client.post("/api/providers/tts/profile/save", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"], payload)
        method.assert_called_once_with(payload)

    def test_tts_synthesize_route(self) -> None:
        with patch.object(main.service, "synthesize_text", return_value={"ok": True, "filename": "tts-test.wav"}) as method:
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
        with patch.object(main.voice_roundtrip_service, "run", new=AsyncMock(return_value=payload)) as method:
            response = self.client.post(
                "/api/voice/roundtrip",
                files={"audio_file": ("clip.wav", b"RIFF....", "audio/wav")},
                data={"session_id": "abc", "synthesize_speech": "true"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transcript"], "hello")
        self.assertEqual(method.await_count, 1)

    def test_vision_routes(self) -> None:
        analysis = VisionAnalysis(summary="Image summary")
        with patch.object(main.service, "analyze_remote_image", return_value=analysis) as method:
            response = self.client.post(
                "/api/vision/analyze",
                files={"image_file": ("image.png", b"png", "image/png")},
                data={"user_text": "what is this?", "vision_mode": "photo"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "Image summary")
        self.assertEqual(method.call_args.kwargs["vision_mode"], "photo")

        assistant_response = AssistantResponse(spoken_text="Looks good.")
        with patch.object(main.service, "respond_remote_image", return_value=assistant_response) as method:
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

        with patch.object(main.live_voice_session, "handle", side_effect=fake_handle) as method:
            with self.client.websocket_connect("/api/voice/live") as websocket:
                payload = websocket.receive_json()
        self.assertEqual(payload, {"type": "ready"})
        self.assertEqual(method.call_count, 1)


if __name__ == "__main__":
    unittest.main()
