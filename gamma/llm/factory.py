from __future__ import annotations

from ..config import settings
from ..errors import ConfigurationError
from .base import LLMAdapter


def build_llm_adapter() -> LLMAdapter:
    if settings.llm_router_enabled:
        from .router_adapter import RouterLLMAdapter

        return RouterLLMAdapter()
    provider = settings.llm_provider.strip().lower()
    if provider == "openai":
        from .openai_adapter import OpenAIAdapter

        return OpenAIAdapter()
    if provider in {"local", "ollama"}:
        from .local_adapter import LocalLLMAdapter

        return LocalLLMAdapter()
    if provider == "mock":
        from .mock_adapter import MockLLMAdapter

        return MockLLMAdapter()
    raise ConfigurationError(f"Unsupported SHANA_LLM_PROVIDER: {settings.llm_provider}")
