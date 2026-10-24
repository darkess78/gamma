from __future__ import annotations

from ..config import settings
from ..errors import ConfigurationError
from .base import LLMAdapter


def build_llm_adapter() -> LLMAdapter:
    """Build an LLM adapter based on settings.

    Returns:
        LLMAdapter: Appropriate adapter for current configuration.

    Raises:
        ConfigurationError: If unsupported LLM provider or runtime configured.
    """
    runtime = settings.brain_model_runtime
    if runtime not in ("inprocess", "worker"):
        raise ConfigurationError("Unsupported brain model runtime")

    if settings.llm_router_enabled:
        from .router_adapter import RouterLLMAdapter

        return RouterLLMAdapter()

    if runtime == "worker":
        from ..brain.model_runtime import get_model_worker

        return get_model_worker()

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
