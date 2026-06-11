from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import gamma.system.status as status_module
import gamma.voice.live_jobs as live_jobs_module
import gamma.voice.roundtrip as roundtrip_module
from gamma.errors import ConfigurationError, ConversationError, ExternalServiceError
from gamma.schemas.conversation import SpeakerContext
from gamma.schemas.response import AssistantResponse

with patch.object(status_module, "SystemStatusService", autospec=True), patch.object(
    roundtrip_module, "VoiceRoundtripService", autospec=True
), patch.object(live_jobs_module, "LiveVoiceJobManager", autospec=True):
    from gamma.api import routes
    from gamma.main import app as api_app


class ApiRoutesTest(unittest.TestCase):
    def test_conversation_respond_serializes_filtered_voice_metadata(self) -> None:
        assistant_response = AssistantResponse(
            spoken_text="Hey there.",
            emotion="happy",
            motions=[],
            tool_calls=[],
            tool_results=[],
            memory_candidates=[],
            audio_path="fake.wav",
            audio_content_type="audio/wav",
            timing_ms={"tts_ms": 12.3},
            tts_metadata={
                "speech_filter": {
                    "level": "strict",
                    "blocked": False,
                    "matched_rules": [],
                }
            },
        )
        conversation_service = Mock()
        conversation_service.respond.return_value = assistant_response
        with patch("gamma.api.routes.get_conversation_service", return_value=conversation_service):
            response = routes.conversation_respond(
                routes.ConversationRequest(
                    user_text="hello",
                    session_id="sess-1",
                    synthesize_speech=True,
                    fast_mode=True,
                    speaker=SpeakerContext(source="discord", platform_id="12345"),
                )
            )

        payload = response.model_dump()
        self.assertEqual(payload["spoken_text"], "Hey there.")
        self.assertEqual(payload["emotion"], "happy")
        self.assertEqual(payload["audio_content_type"], "audio/wav")
        self.assertEqual(payload["tts_metadata"]["speech_filter"]["level"], "strict")
        self.assertEqual(payload["tts_metadata"]["speech_filter"]["blocked"], False)
        conversation_service.respond.assert_called_once()
        call = conversation_service.respond.call_args.kwargs
        self.assertEqual(call["user_text"], "hello")
        self.assertEqual(call["session_id"], "sess-1")
        self.assertEqual(call["synthesize_speech"], True)
        self.assertEqual(call["fast_mode"], True)
        self.assertEqual(call["speaker_ctx"].source, "discord")
        self.assertEqual(call["speaker_ctx"].platform_id, "12345")

    def test_conversation_respond_maps_conversation_error_to_400(self) -> None:
        conversation_service = Mock()
        conversation_service.respond.side_effect = ConversationError("bad request")
        with patch("gamma.api.routes.get_conversation_service", return_value=conversation_service):
            with self.assertRaises(HTTPException) as ctx:
                routes.conversation_respond(routes.ConversationRequest(user_text="hello"))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "bad request")

    def test_conversation_respond_maps_external_service_error_to_502(self) -> None:
        conversation_service = Mock()
        conversation_service.respond.side_effect = ExternalServiceError("upstream unavailable")
        with patch("gamma.api.routes.get_conversation_service", return_value=conversation_service):
            with self.assertRaises(HTTPException) as ctx:
                routes.conversation_respond(routes.ConversationRequest(user_text="hello"))

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail, "upstream unavailable")

    def test_conversation_respond_maps_configuration_error_to_500(self) -> None:
        conversation_service = Mock()
        conversation_service.respond.side_effect = ConfigurationError("misconfigured")
        with patch("gamma.api.routes.get_conversation_service", return_value=conversation_service):
            with self.assertRaises(HTTPException) as ctx:
                routes.conversation_respond(routes.ConversationRequest(user_text="hello"))

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "misconfigured")

    def test_performer_page_and_default_image_routes(self) -> None:
        page = routes.performer_page()
        image = routes.performer_default_image()
        monitor = routes.monitor_page()
        overlay = routes.subtitle_overlay_page()
        favicon = routes.favicon()

        self.assertEqual(page.media_type, "text/html")
        self.assertIn("window.GAMMA_SHANA_BASE_URL", page.body.decode("utf-8"))
        self.assertIn("window.GAMMA_DASHBOARD_BASE_URL", page.body.decode("utf-8"))
        self.assertIn("data-dashboard-link", page.body.decode("utf-8"))
        self.assertEqual(image.media_type, "image/png")
        self.assertTrue(str(image.path).endswith("jacket shana mouth closed eyes open.png"))
        self.assertEqual(monitor.media_type, "text/html")
        self.assertIn("window.GAMMA_SHANA_BASE_URL", monitor.body.decode("utf-8"))
        self.assertIn("window.GAMMA_DASHBOARD_BASE_URL", monitor.body.decode("utf-8"))
        self.assertEqual(overlay.media_type, "text/html")
        self.assertIn("window.GAMMA_SHANA_BASE_URL", overlay.body.decode("utf-8"))
        self.assertIn("window.GAMMA_DASHBOARD_BASE_URL", overlay.body.decode("utf-8"))
        self.assertEqual(favicon.media_type, "image/svg+xml")
        self.assertEqual(str(favicon.path), str(routes.DASHBOARD_STATIC_DIR / "favicon.svg"))

    def test_dashboard_subpage_routes_redirect_to_dashboard_app(self) -> None:
        with patch.object(routes.settings, "dashboard_public_host", "10.78.78.29"):
            dashboard = routes.dashboard()
            response = routes.dashboard_page_redirect("live")
            twitch = routes.dashboard_page_redirect("twitch")

        self.assertEqual(dashboard.status_code, 307)
        self.assertIn("/dashboard", dashboard.headers["location"])
        self.assertEqual(response.status_code, 307)
        self.assertIn("/dashboard/live", response.headers["location"])
        self.assertEqual(twitch.status_code, 307)
        self.assertIn("/dashboard/stream", twitch.headers["location"])

    def test_public_dashboard_routes_are_registered_on_api_app(self) -> None:
        registered_paths = {getattr(route, "path", "") for route in api_app.routes}
        self.assertIn("/dashboard", registered_paths)
        self.assertIn("/dashboard/{page_name}", registered_paths)

    def test_public_dashboard_routes_on_api_app_do_not_json_404(self) -> None:
        page_paths = [
            ("dashboard", routes.dashboard),
            ("live", lambda: routes.dashboard_page_redirect("live")),
            ("monitor", lambda: routes.dashboard_page_redirect("monitor")),
            ("status", lambda: routes.dashboard_page_redirect("status")),
            ("stream", lambda: routes.dashboard_page_redirect("stream")),
            ("memory", lambda: routes.dashboard_page_redirect("memory")),
            ("settings", lambda: routes.dashboard_page_redirect("settings")),
        ]
        with (
            patch.object(routes.settings, "dashboard_public_scheme", "https"),
            patch.object(routes.settings, "dashboard_public_host", "gamma.neety.me"),
            patch.object(routes.settings, "dashboard_public_port", 443),
        ):
            for page_name, route_call in page_paths:
                with self.subTest(page_name=page_name):
                    response = route_call()
                    self.assertNotEqual(response.status_code, 404)
                    self.assertEqual(response.status_code, 307)
                    self.assertTrue(response.headers["location"].startswith("https://gamma.neety.me/dashboard"))


if __name__ == "__main__":
    unittest.main()
