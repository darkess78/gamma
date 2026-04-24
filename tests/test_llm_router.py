from __future__ import annotations

import unittest

from gamma.config import settings
from gamma.llm.base import LLMCallContext
from gamma.llm.router_adapter import RouterLLMAdapter


class RouterLLMAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_values = {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_router_enabled": settings.llm_router_enabled,
            "llm_router_default_provider": settings.llm_router_default_provider,
            "llm_router_default_model": settings.llm_router_default_model,
            "llm_router_allow_hosted_escalation": settings.llm_router_allow_hosted_escalation,
            "llm_router_hosted_provider": settings.llm_router_hosted_provider,
            "llm_router_hosted_model": settings.llm_router_hosted_model,
            "llm_router_complex_max_input_words": settings.llm_router_complex_max_input_words,
            "local_llm_model": settings.local_llm_model,
            "local_llm_light_model": settings.local_llm_light_model,
            "local_llm_tagging_model": settings.local_llm_tagging_model,
            "local_llm_light_max_input_words": settings.local_llm_light_max_input_words,
            "local_llm_supports_vision": settings.local_llm_supports_vision,
            "local_llm_vision_model": settings.local_llm_vision_model,
        }
        settings.llm_provider = "local"
        settings.llm_model = "gpt-4.1-mini"
        settings.llm_router_enabled = True
        settings.llm_router_default_provider = "local"
        settings.llm_router_default_model = "gpt-oss:20b"
        settings.llm_router_allow_hosted_escalation = False
        settings.llm_router_hosted_provider = "openai"
        settings.llm_router_hosted_model = "gpt-4.1"
        settings.llm_router_complex_max_input_words = 120
        settings.local_llm_model = "gpt-oss:20b"
        settings.local_llm_light_model = "qwen2.5:7b"
        settings.local_llm_tagging_model = "qwen2.5:3b"
        settings.local_llm_light_max_input_words = 40
        settings.local_llm_supports_vision = False
        settings.local_llm_vision_model = ""

    def tearDown(self) -> None:
        for key, value in self._original_values.items():
            setattr(settings, key, value)

    def test_metadata_route_prefers_local_tagging_model(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="metadata prompt",
            user_text="hello",
            image_inputs=None,
            call_context=LLMCallContext(purpose="metadata_extraction"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:3b")

    def test_model_override_uses_default_provider(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="hello",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation"),
            model_override="custom-model",
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "custom-model")
        self.assertEqual(decision.reason, "explicit-model-override")

    def test_fast_short_turn_prefers_local_light_model(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="tell me a quick joke",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation_draft", fast_mode=True),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:7b")

    def test_lightweight_turn_prefers_local_light_model_without_fast_flag(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="hi there",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:7b")
        self.assertEqual(decision.reason, "lightweight-turn")

    def test_tool_finalizer_prefers_light_model(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="tool prompt",
            user_text="summarize tool results",
            image_inputs=None,
            call_context=LLMCallContext(purpose="tool_finalizer"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:7b")
        self.assertEqual(decision.reason, "tool-finalizer-light-model")

    def test_voice_helper_prefers_tagging_model(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="voice helper prompt",
            user_text="plan the next sentence",
            image_inputs=None,
            call_context=LLMCallContext(purpose="voice_reply_planner"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:3b")
        self.assertEqual(decision.reason, "voice-helper-local-worker")

    def test_voice_helper_falls_back_to_light_model_when_tagging_missing(self) -> None:
        settings.local_llm_tagging_model = ""
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="voice helper prompt",
            user_text="say the next sentence",
            image_inputs=None,
            call_context=LLMCallContext(purpose="voice_sentence_generator"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "qwen2.5:7b")
        self.assertEqual(decision.reason, "voice-helper-local-worker")

    def test_heavy_turn_can_escalate_to_hosted_when_enabled(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="Please analyze this architecture and compare the tradeoffs step by step for a refactor plan.",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation_draft"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "openai")
        self.assertEqual(decision.model, "gpt-4.1")

    def test_default_route_uses_default_provider_and_model(self) -> None:
        settings.local_llm_light_model = ""
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="This is a detailed response that avoids the lightweight keywords but stays local.",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "gpt-oss:20b")
        self.assertEqual(decision.reason, "default-route")

    def test_vision_route_falls_back_to_hosted_provider_when_local_has_no_vision(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="vision prompt",
            user_text="what is in this image?",
            image_inputs=[object()],
            call_context=LLMCallContext(purpose="vision_analysis"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "openai")
        self.assertEqual(decision.model, "gpt-4.1")

    def test_vision_route_uses_default_local_route_when_local_supports_vision(self) -> None:
        settings.llm_router_allow_hosted_escalation = False
        settings.local_llm_supports_vision = True
        settings.local_llm_vision_model = "llama3.2-vision"
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="vision prompt",
            user_text="what is in this image?",
            image_inputs=[object()],
            call_context=LLMCallContext(purpose="vision_analysis"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "gpt-oss:20b")
        self.assertEqual(decision.reason, "default-vision-route")

    def test_vision_route_can_use_explicit_local_vision_fallback(self) -> None:
        settings.llm_router_allow_hosted_escalation = False
        settings.llm_router_default_provider = "mock"
        settings.llm_router_default_model = ""
        settings.local_llm_supports_vision = True
        settings.local_llm_vision_model = "llama3.2-vision"
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="vision prompt",
            user_text="what is in this image?",
            image_inputs=[object()],
            call_context=LLMCallContext(purpose="vision_analysis"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.model, "llama3.2-vision")
        self.assertEqual(decision.reason, "local-vision-route")


if __name__ == "__main__":
    unittest.main()
