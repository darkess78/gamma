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


if __name__ == "__main__":
    unittest.main()
