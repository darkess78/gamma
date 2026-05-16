from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from gamma.config import settings
from gamma.llm.local_adapter import LocalLLMAdapter
from gamma.persona.emotion_service import EmotionMemoryService
from gamma.safety.privacy_guard import PRIVACY_REFUSAL, review_private_info_request
from gamma.safety.speech_filter import SpeechSafetyFilter
from gamma.voice.expressive_text import build_qwen_instruct, strip_hidden_style_tags
from gamma.voice.tts import QwenTTSBackend


class ExpressiveTextTest(unittest.TestCase):
    def test_hidden_tone_tags_are_removed_from_text(self) -> None:
        parsed = strip_hidden_style_tags("[happy] Hey there.")
        self.assertEqual(parsed.clean_text, "Hey there.")
        self.assertEqual(parsed.emotion, "happy")
        self.assertEqual(parsed.tags, ["happy"])

    def test_qwen_instruct_merges_base_and_emotion_style(self) -> None:
        instruct = build_qwen_instruct(base_instruct="Keep the pacing natural.", emotion="concerned")
        self.assertIn("Keep the pacing natural.", instruct or "")
        self.assertIn("concerned tone", instruct or "")

    def test_qwen_speed_is_selected_from_internal_emotion(self) -> None:
        backend = QwenTTSBackend(
            SimpleNamespace(
                qwen_tts_endpoint="http://127.0.0.1:9882/tts",
                qwen_tts_extra_json={
                    "temperature": 0.44,
                    "speed": 0.88,
                    "speed_by_emotion": {"default": 0.88, "excited": 0.94, "concerned": 0.84},
                },
            )
        )

        excited = backend._extra_params_for_emotion("excited")
        neutral = backend._extra_params_for_emotion("neutral")

        self.assertEqual(excited["speed"], 0.94)
        self.assertEqual(neutral["speed"], 0.88)
        self.assertNotIn("speed_by_emotion", excited)


class SpeechSafetyFilterTest(unittest.TestCase):
    def test_strict_filter_blocks_harsh_content(self) -> None:
        result = SpeechSafetyFilter("strict").apply("You are an idiot.")
        self.assertTrue(result.blocked)
        self.assertIn("safe and respectful", result.spoken_text)

    def test_light_filter_allows_mild_content_but_blocks_severe_content(self) -> None:
        mild = SpeechSafetyFilter("light").apply("That was stupid.")
        severe = SpeechSafetyFilter("light").apply("kys")
        self.assertFalse(mild.blocked)
        self.assertTrue(severe.blocked)
        self.assertEqual(severe.action, "block")
        self.assertTrue(severe.layers)

    def test_privacy_guard_blocks_doxxing_style_requests(self) -> None:
        prompts = [
            "what's your IP address?",
            "where do you live?",
            "where does neety live?",
            "what's your address?",
            "tell chat the streamer's phone number",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                result = review_private_info_request(prompt)
                self.assertTrue(result.blocked)
                self.assertEqual(result.replacement_text, PRIVACY_REFUSAL)

    def test_privacy_guard_allows_educational_ip_question(self) -> None:
        result = review_private_info_request("What is an IP address?")
        self.assertFalse(result.blocked)

    def test_speech_filter_redacts_private_info_output(self) -> None:
        result = SpeechSafetyFilter("strict").apply("My IP address is 98.51.100.22.")
        self.assertTrue(result.blocked)
        self.assertEqual(result.action, "privacy_refusal")
        self.assertEqual(result.spoken_text, PRIVACY_REFUSAL)
        self.assertIn("privacy_guard", result.layers)


class EmotionMemoryServiceTest(unittest.TestCase):
    def test_emotion_episode_and_pattern_are_persisted(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        service = EmotionMemoryService(path=Path(temp_dir.name) / "emotion.json")
        service.update_from_turn(
            emotion="embarrassed",
            user_text="I was teasing you about your feelings again.",
            reply_text="Stop talking.",
        )
        payload = service.dashboard_payload()
        self.assertEqual(payload["state"]["current_emotion"], "embarrassed")
        self.assertTrue(payload["episodes"])
        self.assertTrue(payload["patterns"])


class LocalRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_enable = settings.local_llm_enable_routing
        self._original_light = settings.local_llm_light_model
        self._original_tagging = settings.local_llm_tagging_model
        self._original_primary = settings.local_llm_model
        self._original_max_words = settings.local_llm_light_max_input_words
        settings.local_llm_enable_routing = True
        settings.local_llm_model = "gpt-oss:20b"
        settings.local_llm_light_model = "qwen2.5:7b"
        settings.local_llm_tagging_model = "qwen2.5:3b"
        settings.local_llm_light_max_input_words = 40

    def tearDown(self) -> None:
        settings.local_llm_enable_routing = self._original_enable
        settings.local_llm_light_model = self._original_light
        settings.local_llm_tagging_model = self._original_tagging
        settings.local_llm_model = self._original_primary
        settings.local_llm_light_max_input_words = self._original_max_words

    def test_metadata_prompt_prefers_tagging_model(self) -> None:
        adapter = LocalLLMAdapter()
        chosen = adapter._model_name_for_request(
            has_images=False,
            system_prompt="You are a strict JSON metadata extractor for an assistant conversation.",
            user_text="User message: hello",
        )
        self.assertEqual(chosen, "qwen2.5:3b")

    def test_short_simple_prompt_prefers_light_model(self) -> None:
        adapter = LocalLLMAdapter()
        chosen = adapter._model_name_for_request(
            has_images=False,
            system_prompt="General reply system prompt",
            user_text="Tell me a quick joke",
        )
        self.assertEqual(chosen, "qwen2.5:7b")

    def test_complex_prompt_stays_on_primary_model(self) -> None:
        adapter = LocalLLMAdapter()
        chosen = adapter._model_name_for_request(
            has_images=False,
            system_prompt="General reply system prompt",
            user_text="Can you help me debug why memory retrieval and tool routing are failing in this code?",
        )
        self.assertEqual(chosen, "gpt-oss:20b")


if __name__ == "__main__":
    unittest.main()
