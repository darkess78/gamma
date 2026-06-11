from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gamma.config import settings
from gamma.llm.base import LLMCallContext
from gamma.llm.base import LLMReply
from gamma.llm.router_adapter import RouterLLMAdapter, begin_route_trace, take_route_trace


class _FakeAdapter:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls: list[dict[str, object]] = []

    @property
    def supports_vision(self) -> bool:
        return self.provider == "openai"

    def generate_reply(self, system_prompt: str, user_text: str, image_inputs=None, **kwargs) -> LLMReply:
        self.calls.append({"system_prompt": system_prompt, "user_text": user_text, "image_inputs": image_inputs, "kwargs": kwargs})
        return LLMReply(text=f"{self.provider}:{user_text}")


class _TestRouter(RouterLLMAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.fake_adapters = {
            "local": _FakeAdapter("local"),
            "openai": _FakeAdapter("openai"),
            "mock": _FakeAdapter("mock"),
        }
        self.local_health = {"ok": True, "detail": "ready"}

    def _build_provider_adapter(self, provider: str):
        return self.fake_adapters[provider]

    def _check_local_llm_health(self) -> dict[str, object]:
        return dict(self.local_health)


class RouterLLMAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        RouterLLMAdapter._provider_backoff_until_global = {}
        self._original_values = {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "openai_api_key": settings.openai_api_key,
            "llm_router_enabled": settings.llm_router_enabled,
            "llm_router_default_provider": settings.llm_router_default_provider,
            "llm_router_default_model": settings.llm_router_default_model,
            "llm_router_allow_hosted_escalation": settings.llm_router_allow_hosted_escalation,
            "llm_router_hosted_provider": settings.llm_router_hosted_provider,
            "llm_router_hosted_model": settings.llm_router_hosted_model,
            "llm_router_profile": settings.llm_router_profile,
            "llm_router_complex_max_input_words": settings.llm_router_complex_max_input_words,
            "llm_router_failure_backoff_seconds": settings.llm_router_failure_backoff_seconds,
            "llm_router_chat_light_max_input_words": settings.llm_router_chat_light_max_input_words,
            "llm_router_persona_hosted_fallback_enabled": settings.llm_router_persona_hosted_fallback_enabled,
            "llm_router_persona_heavy_hosted_fallback_enabled": settings.llm_router_persona_heavy_hosted_fallback_enabled,
            "local_llm_model": settings.local_llm_model,
            "local_llm_light_model": settings.local_llm_light_model,
            "local_llm_tagging_model": settings.local_llm_tagging_model,
            "local_llm_light_max_input_words": settings.local_llm_light_max_input_words,
            "local_llm_supports_vision": settings.local_llm_supports_vision,
            "local_llm_vision_model": settings.local_llm_vision_model,
            "data_dir": settings.data_dir,
        }
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        settings.llm_provider = "local"
        settings.llm_model = "gpt-4.1-mini"
        settings.openai_api_key = "test-openai-key"
        settings.llm_router_enabled = True
        settings.llm_router_default_provider = "local"
        settings.llm_router_default_model = "gpt-oss:20b"
        settings.llm_router_allow_hosted_escalation = False
        settings.llm_router_hosted_provider = "openai"
        settings.llm_router_hosted_model = "gpt-4.1"
        settings.llm_router_profile = "balanced"
        settings.llm_router_complex_max_input_words = 120
        settings.llm_router_failure_backoff_seconds = 45
        settings.llm_router_chat_light_max_input_words = 40
        settings.llm_router_persona_hosted_fallback_enabled = False
        settings.llm_router_persona_heavy_hosted_fallback_enabled = True
        settings.local_llm_model = "gpt-oss:20b"
        settings.local_llm_light_model = "qwen2.5:7b"
        settings.local_llm_tagging_model = "qwen2.5:3b"
        settings.local_llm_light_max_input_words = 40
        settings.local_llm_supports_vision = False
        settings.local_llm_vision_model = ""
        settings.data_dir = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        for key, value in self._original_values.items():
            setattr(settings, key, value)
        RouterLLMAdapter._provider_backoff_until_global = {}

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
        self.assertEqual(decision.route_family, "metadata")

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
        self.assertEqual(decision.route_family, "explicit_override")

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
        self.assertEqual(decision.route_family, "chat_light")

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
        self.assertEqual(decision.route_family, "chat_light")

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
        self.assertEqual(decision.route_family, "tool_finalize")

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
        self.assertEqual(decision.route_family, "voice_fast")

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
        self.assertEqual(decision.route_family, "voice_fast")

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
        self.assertEqual(decision.route_family, "reasoning_heavy")

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
        self.assertEqual(decision.route_family, "chat_light")

    def test_local_only_profile_never_uses_hosted_route(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        settings.llm_router_profile = "local_only"
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="Please analyze this architecture and compare the tradeoffs step by step for a refactor plan.",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation_draft"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "local")
        self.assertEqual(decision.route_family, "reasoning_heavy")

    def test_high_quality_profile_prefers_hosted_route_for_conversation(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        settings.llm_router_profile = "high_quality"
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="hello there",
            image_inputs=None,
            call_context=LLMCallContext(purpose="conversation_draft"),
            model_override=None,
        )
        self.assertEqual(decision.provider, "openai")
        self.assertEqual(decision.reason, "high-quality-hosted-default")
        self.assertEqual(decision.route_family, "chat_light")

    def test_persona_sensitive_chat_does_not_force_hosted_in_high_quality(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        settings.llm_router_profile = "high_quality"
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="Tell me something sweet and playful.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                interaction_mode="chat",
            ),
            model_override=None,
        )
        self.assertEqual(decision.route_family, "chat_persona")
        self.assertEqual(decision.provider, "local")

    def test_persona_sensitive_heavy_turn_uses_persona_heavy_family(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="Please analyze this relationship dynamic and compare the tradeoffs step by step.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                reasoning_depth="heavy",
                interaction_mode="chat",
            ),
            model_override=None,
        )
        self.assertEqual(decision.route_family, "reasoning_heavy_persona")
        self.assertEqual(decision.provider, "local")

    def test_unhealthy_local_light_route_falls_back_to_hosted(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = _TestRouter()
        router.local_health = {"ok": False, "detail": "ollama-down"}
        begin_route_trace()
        reply = router.generate_reply(
            system_prompt="conversation prompt",
            user_text="tell me a quick joke",
            call_context=LLMCallContext(purpose="conversation_draft", fast_mode=True),
        )
        events = take_route_trace()
        self.assertEqual(reply.text, "openai:tell me a quick joke")
        self.assertEqual(events[0]["status"], "skipped")
        self.assertEqual(events[-1]["provider"], "openai")
        self.assertEqual(events[-1]["status"], "ok")

    def test_provider_failure_sets_backoff(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = _TestRouter()

        def fail_local(*args, **kwargs):
            raise RuntimeError("local-fail")

        router.fake_adapters["local"].generate_reply = fail_local  # type: ignore[method-assign]
        reply = router.generate_reply(
            system_prompt="conversation prompt",
            user_text="tell me a quick joke",
            call_context=LLMCallContext(purpose="conversation_draft", fast_mode=True),
        )
        self.assertEqual(reply.text, "openai:tell me a quick joke")
        backoff = router.provider_backoff_state()
        self.assertIn("local:chat_light", backoff)

    def test_vision_backoff_does_not_block_text_routes(self) -> None:
        router = _TestRouter()
        router._mark_provider_failure("local", has_images=True, route_family="vision")
        vision_availability = router._provider_availability(
            provider="local",
            model="gpt-oss:20b",
            has_images=True,
            route_family="vision",
        )
        text_availability = router._provider_availability(
            provider="local",
            model="gpt-oss:20b",
            has_images=False,
            route_family="chat_light",
        )
        self.assertFalse(bool(vision_availability["ok"]))
        self.assertTrue(bool(text_availability["ok"]))

    def test_generate_reply_records_route_log_and_trace(self) -> None:
        router = _TestRouter()
        begin_route_trace()
        reply = router.generate_reply(
            system_prompt="metadata prompt",
            user_text="hello",
            call_context=LLMCallContext(purpose="metadata_extraction"),
        )
        events = take_route_trace()
        self.assertEqual(reply.text, "local:hello")
        self.assertTrue(events)
        self.assertEqual(events[-1]["provider"], "local")
        self.assertEqual(events[-1]["status"], "ok")
        self.assertEqual(events[-1]["route_family"], "metadata")
        log_path = settings.data_dir / "runtime" / "llm.routes.jsonl"
        self.assertTrue(log_path.exists())

    def test_persona_sensitive_chat_chain_avoids_hosted_fallback(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        router = RouterLLMAdapter()
        primary = router._route_request(
            system_prompt="conversation prompt",
            user_text="Tell me something sweet and playful.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                interaction_mode="chat",
            ),
            model_override=None,
        )
        chain = router._build_route_chain(primary=primary, route_family=primary.route_family, has_images=False)
        self.assertFalse(any(decision.provider == "openai" for decision in chain))

    def test_persona_heavy_chain_can_include_hosted_when_enabled(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        settings.llm_router_persona_heavy_hosted_fallback_enabled = True
        router = RouterLLMAdapter()
        primary = router._route_request(
            system_prompt="conversation prompt",
            user_text="Please analyze this relationship dynamic and compare the tradeoffs step by step.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                reasoning_depth="heavy",
                interaction_mode="chat",
            ),
            model_override=None,
        )
        chain = router._build_route_chain(primary=primary, route_family=primary.route_family, has_images=False)
        self.assertTrue(any(decision.provider == "openai" for decision in chain))

    def test_persona_heavy_chain_omits_hosted_when_disabled(self) -> None:
        settings.llm_router_allow_hosted_escalation = True
        settings.llm_router_persona_heavy_hosted_fallback_enabled = False
        router = RouterLLMAdapter()
        primary = router._route_request(
            system_prompt="conversation prompt",
            user_text="Please analyze this relationship dynamic and compare the tradeoffs step by step.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                reasoning_depth="heavy",
                interaction_mode="chat",
            ),
            model_override=None,
        )
        chain = router._build_route_chain(primary=primary, route_family=primary.route_family, has_images=False)
        self.assertFalse(any(decision.provider == "openai" for decision in chain))

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
        self.assertEqual(decision.route_family, "vision")

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
        self.assertEqual(decision.route_family, "vision")

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
        self.assertEqual(decision.route_family, "vision")

    def test_persona_sensitive_chat_classifies_as_chat_persona(self) -> None:
        router = RouterLLMAdapter()
        decision = router._route_request(
            system_prompt="conversation prompt",
            user_text="Tell me something sweet and playful.",
            image_inputs=None,
            call_context=LLMCallContext(
                purpose="conversation_draft",
                persona_sensitive=True,
                interaction_mode="chat",
            ),
            model_override=None,
        )
        self.assertEqual(decision.route_family, "chat_persona")


if __name__ == "__main__":
    unittest.main()
