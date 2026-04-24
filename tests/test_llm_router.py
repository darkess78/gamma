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


if __name__ == "__main__":
    unittest.main()
