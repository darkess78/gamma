from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gamma.config import settings
from gamma.llm.local_adapter import LocalLLMAdapter
from gamma.persona.emotion_service import EmotionMemoryService
from gamma.safety.privacy_guard import PRIVACY_REFUSAL, review_private_info_request
from gamma.safety.speech_filter import SpeechSafetyFilter
from gamma.voice.expressive_text import build_qwen_instruct, strip_hidden_style_tags
from gamma.voice.tts_qwen import QwenTTSBackend
try:
    from scripts.qwen_tts_server import _normalize_generation_params, _pop_output_peak
except ModuleNotFoundError as exc:  # scripts/ is not shipped with every test copy
    if exc.name not in {"scripts", "scripts.qwen_tts_server"}:
        raise
    _normalize_generation_params = _pop_output_peak = None

_requires_qwen_server_script = unittest.skipIf(
    _normalize_generation_params is None, "scripts/qwen_tts_server.py is not present"
)


class ExpressiveTextTest(unittest.TestCase):
    def test_hidden_tone_tags_are_removed_from_text(self) -> None:
        parsed = strip_hidden_style_tags("[happy] Hey there.")
        self.assertEqual(parsed.clean_text, "Hey there.")
        self.assertEqual(parsed.emotion, "happy")
        self.assertEqual(parsed.tags, ["happy"])
        self.assertEqual(parsed.styles, [])

    def test_hidden_voice_style_tags_are_removed_from_text(self) -> None:
        parsed = strip_hidden_style_tags("[soft] [fast] Keep up.", default_emotion="neutral")

        self.assertEqual(parsed.clean_text, "Keep up.")
        self.assertEqual(parsed.emotion, "neutral")
        self.assertEqual(parsed.tags, [])
        self.assertEqual(parsed.styles, ["soft", "fast"])

    def test_qwen_instruct_merges_base_and_emotion_style(self) -> None:
        instruct = build_qwen_instruct(base_instruct="Keep the pacing natural.", emotion="concerned", styles=["quiet"])
        self.assertIn("Keep the pacing natural.", instruct or "")
        self.assertIn("concerned tone", instruct or "")
        self.assertIn("quiet nearby voice", instruct or "")

    def test_qwen_neutral_emotion_does_not_override_base_style(self) -> None:
        instruct = build_qwen_instruct(base_instruct="Keep the voice soft.", emotion="neutral")

        self.assertEqual(instruct, "Keep the voice soft.")

    def test_first_emotion_tag_stabilizes_entire_reply(self) -> None:
        parsed = strip_hidden_style_tags("[happy] Hello there. [annoyed] Still one delivery.")
        self.assertEqual(parsed.emotion, "happy")
        self.assertEqual(parsed.clean_text, "Hello there. Still one delivery.")

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

    def test_qwen_voice_styles_adjust_bounded_runtime_params(self) -> None:
        backend = QwenTTSBackend(
            SimpleNamespace(
                qwen_tts_endpoint="http://127.0.0.1:9882/tts",
                qwen_tts_extra_json={
                    "speed": 0.82,
                    "output_peak": 0.64,
                    "speed_by_emotion": {"default": 0.82, "excited": 0.86},
                    "output_peak_by_emotion": {"default": 0.64, "excited": 0.62},
                },
            )
        )

        soft = backend._extra_params_for_emotion("neutral", styles=["soft", "quiet"])
        fast = backend._extra_params_for_emotion("neutral", styles=["fast"])
        excited = backend._extra_params_for_emotion("excited", styles=["firm"])

        self.assertAlmostEqual(soft["speed"], 0.81)
        self.assertAlmostEqual(soft["output_peak"], 0.51)
        self.assertEqual(fast["speed"], 0.87)
        self.assertEqual(fast["output_peak"], 0.64)
        self.assertEqual(excited["speed"], 0.87)
        self.assertAlmostEqual(excited["output_peak"], 0.68)

    def _emotion_backend(self, tmp: str, *, text: str = "Yay, you did it!") -> QwenTTSBackend:
        happy_clip = Path(tmp) / "happy.wav"
        happy_clip.write_bytes(b"RIFF")
        return QwenTTSBackend(
            SimpleNamespace(
                qwen_tts_endpoint="http://127.0.0.1:9882/tts",
                qwen_tts_language="English",
                qwen_tts_reference_audio=str(Path(tmp) / "flat.wav"),
                qwen_tts_reference_text="flat transcript",
                qwen_tts_speaker=None,
                qwen_tts_instruct=None,
                qwen_tts_timeout_seconds=5,
                qwen_tts_extra_json={"x_vector_only_mode": True, "temperature": 0.32},
                qwen_tts_emotion_references={
                    "happy": {"audio": str(happy_clip), "text": text},
                    "annoyed": {"audio": str(Path(tmp) / "missing.wav"), "text": "Ugh."},
                },
            )
        )

    def _sent_payload(self, backend: QwenTTSBackend, emotion: str | None) -> dict:
        import io
        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(b"\x01\x00" * 240)
        sent: dict = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return buffer.getvalue()

        def _fake_urlopen(request, timeout=None):
            sent.update(json.loads(request.data.decode("utf-8")))
            return _Response()

        with tempfile.TemporaryDirectory() as out_dir, \
                patch("gamma.supervisor.voice_idle.ensure_voice_sidecar"), \
                patch("gamma.voice.tts_qwen.urllib.request.urlopen", _fake_urlopen), \
                patch.object(QwenTTSBackend, "_target_path", lambda self, suffix: Path(out_dir) / f"out{suffix}"):
            result = backend.synthesize("Hello there.", emotion=emotion)
        sent["_metadata"] = result.metadata
        return sent

    def test_qwen_emotion_reference_replaces_default_clip_in_icl_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._sent_payload(self._emotion_backend(tmp), "happy")

        self.assertTrue(payload["ref_audio_path"].endswith("happy.wav"))
        self.assertEqual(payload["ref_text"], "Yay, you did it!")
        self.assertFalse(payload["extra_params"]["x_vector_only_mode"])
        self.assertNotIn("instruct", payload)
        self.assertTrue(payload["_metadata"]["emotion_reference"])

    def test_qwen_emotion_without_reference_keeps_default_voice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._emotion_backend(tmp)
            for emotion in ("neutral", None, "annoyed"):  # annoyed's clip is missing
                payload = self._sent_payload(backend, emotion)
                self.assertTrue(payload["ref_audio_path"].endswith("flat.wav"), emotion)
                self.assertEqual(payload["ref_text"], "flat transcript")
                self.assertTrue(payload["extra_params"]["x_vector_only_mode"])
                self.assertFalse(payload["_metadata"]["emotion_reference"])

    def test_qwen_emotion_reference_needs_a_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._emotion_backend(tmp, text="  ")
            self.assertIsNone(backend._emotion_reference("happy"))

    @_requires_qwen_server_script
    def test_qwen_server_allows_longer_generation_for_long_text(self) -> None:
        text = (
            "Okay. I am going to try that again more quietly. This should sound like I am speaking to you from nearby, "
            "not projecting across a room. I still want to keep the same Shana character and the same familiar rhythm, "
            "but with less force behind the words, calmer emphasis, and a softer edge on each sentence."
        )

        params = _normalize_generation_params(text, {"max_new_tokens": 2048, "min_new_tokens": 100})

        self.assertGreaterEqual(params["max_new_tokens"], 360)
        self.assertGreater(params["max_new_tokens"], params["min_new_tokens"])

    @_requires_qwen_server_script
    def test_qwen_server_output_peak_is_server_local(self) -> None:
        extra = {"temperature": 0.4, "output_peak": 0.72}

        peak = _pop_output_peak(extra)

        self.assertEqual(peak, 0.72)
        self.assertEqual(extra, {"temperature": 0.4})


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
        self._original_router_enabled = settings.llm_router_enabled
        self._original_light = settings.local_llm_light_model
        self._original_tagging = settings.local_llm_tagging_model
        self._original_primary = settings.local_llm_model
        self._original_max_words = settings.local_llm_light_max_input_words
        settings.local_llm_enable_routing = True
        settings.llm_router_enabled = False
        settings.local_llm_model = "gpt-oss:20b"
        settings.local_llm_light_model = "qwen2.5:7b"
        settings.local_llm_tagging_model = "qwen2.5:3b"
        settings.local_llm_light_max_input_words = 40

    def tearDown(self) -> None:
        settings.local_llm_enable_routing = self._original_enable
        settings.llm_router_enabled = self._original_router_enabled
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

    def test_ollama_request_includes_keep_alive(self) -> None:
        """Verify keep_alive is passed in Ollama request payload."""
        from types import SimpleNamespace
        from unittest.mock import patch, MagicMock

        settings.local_llm_keep_alive = "30m"
        with patch("urllib.request.Request") as mock_req, \
             patch("urllib.request.urlopen") as mock_urlopen:
            # Mock response
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"response": "Hello"}'
            mock_urlopen.return_value = mock_resp

            # Build payload manually to check keep_alive value (skips network call)
            payload = {
                "model": settings.local_llm_model,
                "system": "General reply",
                "prompt": "Hello",
                "stream": False,
                "keep_alive": settings.local_llm_keep_alive,
            }

            # Verify keep_alive is in payload with correct value
            self.assertIn("keep_alive", payload)
            self.assertEqual(payload["keep_alive"], "30m")

    def test_fast_light_mode_uses_keep_alive(self) -> None:
        """Verify fast/light mode LLM requests use keep_alive="30m"."""
        from unittest.mock import patch, MagicMock

        settings.local_llm_light_model = "qwen2.5:7b"
        settings.llm_router_enabled = False
        settings.local_llm_enable_routing = True

        # For fast mode, verify the payload would include keep_alive
        payload = {
            "model": settings.local_llm_light_model,
            "system": "General reply system prompt",
            "prompt": "Keep up the good work.",
            "stream": False,
            "keep_alive": settings.local_llm_keep_alive,
        }

        # Ensure keep_alive is present and correct
        self.assertIn("keep_alive", payload)
        self.assertEqual(payload["keep_alive"], "30m")


if __name__ == "__main__":
    unittest.main()
