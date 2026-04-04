from __future__ import annotations

from ..config import settings
from ..errors import ConfigurationError
from .base import LLMAdapter
from .local_adapter import LocalLLMAdapter
from .mock_adapter import MockLLMAdapter
from .openai_adapter import OpenAIAdapter


def build_llm_adapter() -> LLMAdapter:
    provider = settings.llm_provider.strip().lower()
    if provider == "openai":
        return OpenAIAdapter()
    if provider == "local":
        return LocalLLMAdapter()
    if provider == "mock":
        return MockLLMAdapter()
    raise ConfigurationError(f"Unsupported RIKO_LLM_PROVIDER: {settings.llm_provider}")
