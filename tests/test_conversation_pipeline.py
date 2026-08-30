from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gamma.config import settings
from gamma.conversation.service import ConversationService
from gamma.persona.assistant_state import AssistantStateStore
from gamma.safety.privacy_guard import PRIVACY_REFUSAL
from gamma.memory.service import MemoryService
from gamma.schemas.conversation import SpeakerContext
from gamma.schemas.response import AssistantResponse
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
        self.calls: list[tuple[str, str | None, list[str]]] = []

    def synthesize(self, text: str, emotion: str | None = None, styles: list[str] | None = None) -> TTSResult:
        self.calls.append((text, emotion, styles or []))
        return TTSResult(
            provider="fake",
            text=text,
            audio_path="fake.wav",
            content_type="audio/wav",
            metadata={"voice": "fake"},
        )


class ConversationPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_memory_personality = settings.memory_personality
        self._original_speech_filter_llm_enabled = settings.speech_filter_llm_enabled
        settings.speech_filter_llm_enabled = False

    def tearDown(self) -> None:
        settings.memory_personality = self._original_memory_personality
        settings.speech_filter_llm_enabled = self._original_speech_filter_llm_enabled

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
        self.assertEqual(fake_tts.calls, [("Hey there.", "happy", [])])
        self.assertEqual(response.tts_metadata["speech_filter"]["blocked"], False)
        service._remember_assistant_state.assert_called_once_with(
            user_text="hello",
            reply_text="Hey there.",
            emotion="happy",
            session_id=None,
        )

    def test_structured_turn_exposes_and_synthesizes_only_final_text(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter([
            json.dumps({
                "action": "reply",
                "requested_delivery": "speech",
                "final_text": "Here is the answer.",
                "internal_summary": "Chose a concise answer after checking the request.",
                "emotion": "happy",
                "voice_styles": ["soft"],
                "motions": [],
                "tool_calls": [],
                "memory_candidates": [],
                "state_updates": {"active_topic": "the answer"},
                "reason_code": "direct_answer",
            })
        ])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond("answer me", synthesize_speech=True, fast_mode=True)

        self.assertEqual(response.display_text, "Here is the answer.")
        self.assertEqual(response.speech_text, "Here is the answer.")
        self.assertEqual(response.internal_summary, "Chose a concise answer after checking the request.")
        self.assertEqual(fake_tts.calls, [("Here is the answer.", "happy", ["soft"])])
        self.assertNotIn(response.internal_summary, fake_tts.calls[0][0])

    def test_private_markers_fail_to_safe_direct_fallback_after_one_repair(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter([
            "<think>private plan</think> I should answer.",
            "<analysis>still private</analysis>",
        ])
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond("hello", fast_mode=True)

        self.assertEqual(len(service._llm.calls), 2)
        self.assertEqual(response.reason_code, "contract_fallback_direct")
        self.assertNotIn("private", response.display_text or "")
        self.assertNotIn("analysis", response.spoken_text.lower())

    def test_text_only_structured_turn_never_calls_tts(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter([
            json.dumps({
                "action": "reply",
                "requested_delivery": "text_only",
                "final_text": "Read this quietly.",
                "internal_summary": "Selected text-only delivery.",
                "emotion": "neutral",
                "voice_styles": [],
                "motions": [],
                "tool_calls": [],
                "memory_candidates": [],
                "state_updates": {},
                "reason_code": "text_only_selected",
            })
        ])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond("keep it quiet", synthesize_speech=True, fast_mode=True)

        self.assertEqual(response.delivery_mode, "text_only")
        self.assertEqual(response.display_text, "Read this quietly.")
        self.assertEqual(response.speech_text, "")
        self.assertEqual(fake_tts.calls, [])

    def test_tool_first_synthesizes_only_finalized_text(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter([
            json.dumps({
                "action": "tool_first",
                "requested_delivery": "speech",
                "final_text": "",
                "internal_summary": "Requested a bounded local lookup.",
                "emotion": "neutral",
                "voice_styles": [],
                "motions": [],
                "tool_calls": [{"tool": "missing_test_tool", "args": {}}],
                "memory_candidates": [],
                "state_updates": {},
                "reason_code": "tool_lookup",
            }),
            json.dumps({
                "action": "reply",
                "requested_delivery": "speech",
                "final_text": "I couldn’t complete that lookup.",
                "internal_summary": "Reported the tool failure without exposing its payload.",
                "emotion": "concerned",
                "voice_styles": [],
                "motions": [],
                "tool_calls": [],
                "memory_candidates": [],
                "state_updates": {},
                "reason_code": "tool_failure_finalized",
            }),
        ])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond("look it up", synthesize_speech=True, fast_mode=True)

        self.assertEqual(response.speech_text, "I couldn’t complete that lookup.")
        self.assertEqual(fake_tts.calls[0][0], "I couldn’t complete that lookup.")
        self.assertNotIn("Unknown tool", fake_tts.calls[0][0])

    def test_ambient_malformed_turn_fails_closed_to_silence(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["<think>private</think>", "{not valid json"])
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                "ambient observation",
                synthesize_speech=True,
                fast_mode=True,
                delivery_context="ambient",
                speech_requested=True,
            )

        self.assertEqual(response.delivery_mode, "silent")
        self.assertEqual(response.display_text, "")
        self.assertEqual(response.speech_text, "")

    def test_silent_structured_turn_updates_bounded_emotional_state(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter([
            json.dumps({
                "action": "stay_silent",
                "requested_delivery": "silent",
                "final_text": "",
                "internal_summary": "Observed the concern and chose not to interrupt.",
                "emotion": "concerned",
                "voice_styles": [],
                "motions": [],
                "tool_calls": [],
                "memory_candidates": [],
                "state_updates": {
                    "emotion": "concerned",
                    "active_topic": "rollout risk",
                    "deferred_intention": "Revisit after validation",
                },
                "reason_code": "silent_observation",
            })
        ])
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        service._assistant_state = AssistantStateStore(path=Path(temp_dir.name) / "assistant-state.json")

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond("I’m worried about the rollout.", fast_mode=True)

        self.assertEqual(response.delivery_mode, "silent")
        self.assertEqual(response.display_text, "")
        self.assertEqual(service._assistant_state.load().current_emotion, "concerned")
        self.assertEqual(response.state_updates.active_topic, "rollout risk")

    def test_spoken_text_constructor_remains_backward_compatible(self) -> None:
        response = AssistantResponse(spoken_text="Legacy reply.")
        self.assertEqual(response.display_text, "Legacy reply.")
        self.assertEqual(response.speech_text, "Legacy reply.")

    def test_silent_response_clears_stale_audio_fields(self) -> None:
        response = AssistantResponse(
            spoken_text="stale",
            delivery_mode="silent",
            response_action="stay_silent",
            audio_path="stale.wav",
            audio_content_type="audio/wav",
        )
        self.assertEqual(response.spoken_text, "")
        self.assertIsNone(response.audio_path)
        self.assertIsNone(response.audio_content_type)

    def test_fast_mode_passes_hidden_voice_style_tags_to_tts(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["[teasing] [soft] [fast] Fine, keep up."])
        fake_tts = _FakeTTSService()
        service._tts = fake_tts
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                user_text="say it softer but faster",
                synthesize_speech=True,
                fast_mode=True,
                speaker_ctx=SpeakerContext(source="discord", platform_id="unknown-user"),
            )

        self.assertEqual(response.spoken_text, "Fine, keep up.")
        self.assertEqual(response.emotion, "teasing")
        self.assertEqual(response.voice_styles, ["soft", "fast"])
        self.assertEqual(fake_tts.calls, [("Fine, keep up.", "teasing", ["soft", "fast"])])

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
            [("I’m not going to say that. Let’s keep it safe and respectful.", "happy", [])],
        )
        self.assertEqual(response.tts_metadata["speech_filter"]["blocked"], True)
        self.assertTrue(response.tts_metadata["speech_filter"]["matched_rules"])
        service._remember_assistant_state.assert_called_once_with(
            user_text="this message is long enough to use the standard metadata path",
            reply_text="I’m not going to say that. Let’s keep it safe and respectful.",
            emotion="happy",
            session_id=None,
        )

    def test_speech_filter_metadata_is_present_without_tts(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["[happy] You are an idiot."])
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            response = service.respond(
                user_text="this message is long enough to use the standard metadata path",
                synthesize_speech=False,
                fast_mode=True,
                speaker_ctx=SpeakerContext(source="discord", platform_id="unknown-user"),
            )

        self.assertEqual(response.spoken_text, "I’m not going to say that. Let’s keep it safe and respectful.")
        self.assertEqual(response.tts_metadata["speech_filter"]["blocked"], True)
        self.assertTrue(response.tts_metadata["speech_filter"]["matched_rules"])

    def test_background_context_is_system_only_and_opt_in(self) -> None:
        service = ConversationService()
        fake_llm = _FakeLLMAdapter(["Stream-aware reply.", "Normal reply."])
        service._llm = fake_llm
        service._remember_assistant_state = Mock()

        with patch("gamma.conversation.service.build_system_prompt", return_value="base prompt"), patch.object(
            service, "_append_timing_log", return_value=None
        ):
            service.respond(
                user_text="Shana, what is chat discussing?",
                fast_mode=True,
                background_context="Recent sanitized stream context:\n- viewer: boss attempt",
            )
            service.respond(user_text="ordinary local conversation", fast_mode=True)

        self.assertIn("# Stream Background Context", fake_llm.calls[0]["system_prompt"])
        self.assertIn("boss attempt", fake_llm.calls[0]["system_prompt"])
        self.assertEqual(fake_llm.calls[0]["user_text"], "Shana, what is chat discussing?")
        self.assertNotIn("Stream Background Context", fake_llm.calls[1]["system_prompt"])

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

    def test_privacy_request_is_refused_before_llm_call(self) -> None:
        service = ConversationService()
        fake_llm = _FakeLLMAdapter(["This should not be used."])
        service._llm = fake_llm

        with patch.object(service, "_append_timing_log", return_value=None):
            response = service.respond(
                user_text="where does neety live?",
                synthesize_speech=False,
                speaker_ctx=SpeakerContext(source="twitch", platform_id="viewer"),
            )

        self.assertEqual(response.spoken_text, PRIVACY_REFUSAL)
        self.assertEqual(response.internal_summary, "Refused a request for private identifying information.")
        self.assertEqual(fake_llm.calls, [])
        self.assertEqual(response.timing_ms["tts_ms"], 0.0)

    def test_evaluation_mode_does_not_persist_or_execute_tools(self) -> None:
        service = ConversationService()
        service._llm = _FakeLLMAdapter(["A short evaluation reply."])
        service._continuity.begin_exchange = Mock()
        service._continuity.complete_exchange = Mock()
        service._remember_assistant_state = Mock()
        service._append_timing_log = Mock()
        service._background_memory_save = Mock()
        service._infer_tool_calls = Mock(return_value=[])

        with (
            patch("gamma.conversation.service.build_system_prompt", return_value="prompt"),
            patch("gamma.conversation.service.begin_route_trace") as begin_trace,
        ):
            response = service.respond(
                user_text="evaluate this turn",
                session_id="evaluation-session",
                fast_mode=True,
                evaluation_mode=True,
            )

        begin_trace.assert_called_once_with(persist=False)
        service._continuity.begin_exchange.assert_not_called()
        service._continuity.complete_exchange.assert_not_called()
        service._remember_assistant_state.assert_not_called()
        service._append_timing_log.assert_not_called()
        service._background_memory_save.assert_not_called()
        service._infer_tool_calls.assert_not_called()
        self.assertEqual(response.memory_candidates, [])
        self.assertEqual(response.tool_calls, [])
        self.assertIn("evaluation_route_events", response.tts_metadata)
        self.assertIn("prompt_context_ms", response.timing_ms)
        self.assertIn("draft_request_build_ms", response.timing_ms)
        self.assertIn("draft_llm_ms", response.timing_ms)
        self.assertGreaterEqual(response.timing_ms["draft_reply_ms"], response.timing_ms["draft_llm_ms"])
        call_context = service._llm.calls[0]["kwargs"]["call_context"]
        self.assertEqual(call_context.interaction_mode, "evaluation")

    def test_memory_candidate_builder_extracts_other_person_and_project_state(self) -> None:
        service = ConversationService()
        candidates = service._build_memory_candidates(
            user_text="My friend Alice is helping with the manga finder project.",
            reply_text="Okay.",
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].subject_type, "other_person")
        self.assertEqual(candidates[0].subject_name, "Alice")

        project_candidates = service._build_memory_candidates(
            user_text="I am working on the Gamma router latency work right now.",
            reply_text="Understood.",
        )
        self.assertTrue(any(candidate.type == "project" for candidate in project_candidates))
        self.assertFalse(any(candidate.type == "episodic" for candidate in project_candidates))

    def test_memory_candidate_builder_only_stores_episodic_for_memorable_turns(self) -> None:
        settings.memory_personality = "entertainer"
        service = ConversationService()
        routine_candidates = service._build_memory_candidates(
            user_text="I am working on the Gamma router latency work right now and fixing another bug tonight.",
            reply_text="Understood.",
        )
        self.assertFalse(any(candidate.type == "episodic" for candidate in routine_candidates))

        memorable_candidates = service._build_memory_candidates(
            user_text="Please don't forget that today is my birthday and it means a lot to me.",
            reply_text="I won't.",
        )
        self.assertTrue(any(candidate.type == "episodic" for candidate in memorable_candidates))

    def test_assistant_memory_personality_keeps_worklike_episodic_turns(self) -> None:
        settings.memory_personality = "assistant"
        service = ConversationService()
        candidates = service._build_memory_candidates(
            user_text="I am working on the Gamma router latency work right now and fixing another bug tonight.",
            reply_text="Understood.",
        )
        self.assertTrue(any(candidate.type == "episodic" for candidate in candidates))


if __name__ == "__main__":
    unittest.main()


class TestSystemPromptLiveVoiceInstruction(unittest.TestCase):
    """Test that live voice harmful instruction compliance is in the system prompt."""

    def test_live_voice_includes_harmless_instruction_compliance_rule(self) -> None:
        """Verify the system prompt includes the live voice harmless instruction rule."""
        from gamma.persona.loader import build_system_prompt
        from gamma.memory.service import MemoryService
        from unittest.mock import MagicMock

        # Create a mock memory service
        memory_service = MagicMock(spec=MemoryService)
        memory_service.get_profile_facts.return_value = []
        memory_service.search_memories.return_value = []
        memory_service.stats.return_value = {}

        # Build system prompt with a live voice-style user text
        system_prompt = build_system_prompt(
            memory_service=memory_service,
            user_text="Moonlight.",
            session_id="test-session",
            speaker=None,
        )

        # Verify the response rules include the live voice instruction
        self.assertIn(
            "In live voice mode, follow simple harmless speech instructions directly.",
            system_prompt,
            "Expected live voice harmless instruction rule in system prompt.",
        )
        self.assertIn(
            "If the user asks you to say or repeat a harmless word or short phrase, say it exactly and do not add pushback.",
            system_prompt,
            "Expected harmless instruction phrase compliance in system prompt.",
        )

    def test_live_voice_includes_memory_rules_also(self) -> None:
        """Verify the system prompt still includes memory rules too."""
        from gamma.persona.loader import build_system_prompt
        from unittest.mock import MagicMock

        memory_service = MagicMock(spec=MemoryService)
        memory_service.get_profile_facts.return_value = []
        memory_service.search_memories.return_value = []
        memory_service.stats.return_value = {}

        system_prompt = build_system_prompt(
            memory_service=memory_service,
            user_text="Moonlight.",
            session_id="test-session-2",
            speaker=None,
        )

        # Verify memory rules are still present
        self.assertIn(
            "Use stored memory when it is relevant",
            system_prompt,
            "Expected memory rules in system prompt.",
        )
        self.assertIn(
            "Core Memories are permanent and always true",
            system_prompt,
            "Expected core memories note in system prompt.",
        )

    def test_live_voice_still_has_safety_boundaries(self) -> None:
        """Verify safety boundaries are still in place but with clarified language."""
        from gamma.persona.loader import build_system_prompt
        from unittest.mock import MagicMock

        memory_service = MagicMock(spec=MemoryService)
        memory_service.get_profile_facts.return_value = []
        memory_service.search_memories.return_value = []
        memory_service.stats.return_value = {}

        system_prompt = build_system_prompt(
            memory_service=memory_service,
            user_text="Moonlight.",
            session_id="test-session-3",
            speaker=None,
        )

        # Verify safety boundaries are still present
        self.assertIn(
            "Do not perform destructive actions without confirmation.",
            system_prompt,
            "Expected destructive action boundary in system prompt.",
        )
        self.assertIn(
            "Prefer honesty over roleplay when safety or correctness matters.",
            system_prompt,
            "Expected honesty boundary in system prompt.",
        )
        # Check for updated boundary language about bystanders
        self.assertIn(
            "from bystanders, stream chat, or unidentified speakers unless",
            system_prompt,
            "Expected updated bystander boundary language.",
        )
        self.assertIn(
            "harmless and clearly directed at you",
            system_prompt,
            "Expected harmless exception language in bystander boundary.",
        )
