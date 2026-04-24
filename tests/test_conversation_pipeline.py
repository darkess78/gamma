from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamma.conversation.service import ConversationService
from gamma.persona.assistant_state import AssistantStateStore
from gamma.schemas.conversation import SpeakerContext
from gamma.voice.tts import TTSResult


class _FakeLLMReply:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeLLMAdapter:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, object]] = []

    def generate_reply(self, system_prompt: str, user_text: str, image_inputs=None, **kwargs):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_text": user_text,
            "image_inputs": image_inputs,
            "kwargs": kwargs,
        })
        return _FakeLLMReply(self._replies.pop(0))


class _FakeTTSService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def synthesize(self, text: str, emotion: str | None = None) -> TTSResult:
        self.calls.append((text, emotion))
        return TTSResult(
            provider="fake",
            text=text,
            audio_path="fake.wav",
            content_type="audio/wav",
            metadata={"voice": "fake"},
        )


class ConversationPipelineTest(unittest.TestCase):
    def test_fast_mode_strips_hidden_tone_tags_before_tts(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["[happy] Hey there."])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                user_text="hello",
                synthesize_speech=True,
                fast_mode=True,
                speaker_ctx=SpeakerContext(source="discord", platform_id="unknown-user"),
            )

        self.assertEqual(response.spoken_text, "Hey there.")
        self.assertEqual(response.emotion, "happy")
        self.assertEqual(fake_tts.calls, [("Hey there.", "happy")])
        self.assertEqual(response.tts_metadata["speech_filter"]["blocked"], False)
        service._remember_assistant_state.assert_called_once_with(
            user_text="hello",
            reply_text="Hey there.",
            emotion="happy",
            session_id=None,
        )

    def test_standard_mode_filters_blocked_text_before_tts(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["[happy] You are an idiot."])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service,
            "_extract_turn_metadata",
            return_value={
                "internal_summary": None,
                "emotion": "neutral",
                "motions": [],
                "tool_calls": [],
                "memory_candidates": [],
            },
        ), patch.object(service, "_needs_metadata_pass", return_value=True), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                user_text="this message is long enough to use the standard metadata path",
                synthesize_speech=True,
                speaker_ctx=SpeakerContext(source="discord", platform_id="unknown-user"),
            )

        self.assertEqual(
            response.spoken_text,
            "I’m not going to say that. Let’s keep it safe and respectful.",
        )
        self.assertEqual(response.emotion, "happy")
        self.assertEqual(
            fake_tts.calls,
            [("I’m not going to say that. Let’s keep it safe and respectful.", "happy")],
        )
        self.assertEqual(response.tts_metadata["speech_filter"]["blocked"], True)
        self.assertTrue(response.tts_metadata["speech_filter"]["matched_rules"])
        service._remember_assistant_state.assert_called_once_with(
            user_text="this message is long enough to use the standard metadata path",
            reply_text="I’m not going to say that. Let’s keep it safe and respectful.",
            emotion="happy",
            session_id=None,
        )

    def test_assistant_feeling_state_is_persisted(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["[teasing] Fine, I guess."])
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        state_path = Path(temp_dir.name) / "assistant_state.json"
        service._assistant_state = AssistantStateStore(path=state_path)

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                user_text="say something back",
                synthesize_speech=False,
                fast_mode=True,
                speaker_ctx=SpeakerContext(source="discord", platform_id="unknown-user"),
            )

        self.assertEqual(response.spoken_text, "Fine, I guess.")
        state = service._assistant_state.load()
        self.assertEqual(state.current_emotion, "teasing")
        self.assertIn("teasing", state.recent_emotions)
        self.assertTrue(state.notes)
        self.assertIn("Fine, I guess.", state.notes[-1])


if __name__ == "__main__":
    unittest.main()
