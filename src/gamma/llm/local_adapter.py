from __future__ import annotations

import base64
import json
import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable
from urllib import error, request

from ..errors import ConfigurationError
from ..config import settings
from ..llm.backend_admission import backend_slot
from ..errors import ContextOverflowError, ExternalServiceError, GammaError
from .ollama_probe import probe_ollama_model_capabilities
from .base import LLMAdapter, LLMCallContext, LLMImageInput, LLMReply
from .local_metrics import local_generation_metrics, local_cache_counts
from .local_runtime import current_local_runtime_profile, validate_keep_alive, validate_primary_temperature
from .private_prefix_cache import private_prefix, renderer_verified, priming_payload
from .capabilities import estimate_text_tokens, model_capability
from .context_budget import is_context_overflow_message


_ADMISSION_SCOPE = ContextVar('gamma_local_admission', default=(True, None))


def current_local_admission():
    """Read request-local priority/checkpoint without acquiring a backend lock."""
    return _ADMISSION_SCOPE.get()


@contextmanager
def local_inference_admission(*, foreground: bool, checkpoint: Callable[[], None] | None = None):
    """Per-call context only. The adapter still acquires its one backend slot."""
    if type(foreground) is not bool or checkpoint is not None and not callable(checkpoint):
        raise ValueError('invalid local inference admission scope')
    inherited = _ADMISSION_SCOPE.get()[1]
    token = _ADMISSION_SCOPE.set((foreground, checkpoint if checkpoint is not None else inherited))
    try:
        yield
    finally:
        _ADMISSION_SCOPE.reset(token)



def prepare_private_prefix(prefix, *, endpoint, timeout_seconds, checkpoint=None, deadline=None):
    """Optional ignored primer; caller already holds this backend's admission.

    Unsupported metadata and provider failures only skip an optimization. A
    checkpoint is always called outside failure handling, so cancellation and
    identity fences retain their exact exception object. No draft is retried.
    """
    metrics = {'verification_ms': 0.0, 'prime_ms': 0.0, 'prefix_cache_verified': 0,
               'prefix_prime_attempted': 0, 'prefix_prime_succeeded': 0}
    if endpoint != 'http://127.0.0.1:11434':
        return metrics
    try:
        primer = priming_payload(prefix)
    except ValueError:
        return metrics
    if (type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 10**9
            or not math.isfinite(timeout_seconds)):
        return metrics
    budget = min(5.0, float(timeout_seconds))
    expires = time.monotonic() + budget
    if deadline is not None:
        expires = min(expires, deadline)

    def checked():
        if checkpoint is not None:
            checkpoint()

    def fetch(route, payload=None):
        remaining = expires - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('private_prefix_preparation_deadline')
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = request.Request(endpoint + route, data=data,
            headers={'Content-Type': 'application/json'}, method='POST' if payload is not None else 'GET')
        with request.urlopen(req, timeout=min(remaining, 2.0) if route != '/api/generate' else remaining) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('private_prefix_response_bound')
        if time.monotonic() >= expires:
            raise TimeoutError('private_prefix_preparation_deadline')
        return json.loads(raw.decode('utf-8'))

    def optional_fetch(route, payload=None):
        checked()
        try:
            value = fetch(route, payload)
        except GammaError:
            raise
        except Exception:
            value = None
        checked()
        return value

    verification_started = time.perf_counter()
    version = optional_fetch('/api/version')
    show = optional_fetch('/api/show', {'model': 'qwen3.8:27b', 'verbose': False}) if version is not None else None
    tags = optional_fetch('/api/tags') if show is not None else None
    metrics['verification_ms'] = round((time.perf_counter() - verification_started) * 1000, 3)
    if not renderer_verified(version, show, tags):
        return metrics
    metrics['prefix_cache_verified'] = 1
    metrics['prefix_prime_attempted'] = 1
    prime_started = time.perf_counter()
    result = optional_fetch('/api/generate', primer)
    metrics['prime_ms'] = round((time.perf_counter() - prime_started) * 1000, 3)
    if isinstance(result, dict) and result.get('done') is True and result.get('model') == 'qwen3.8:27b':
        metrics['prefix_prime_succeeded'] = 1
        counts = local_cache_counts(result)
        if counts:
            metrics.update(prime_cached_n=counts['cached_n'], prime_processed_n=counts['processed_n'])
    return metrics


class LocalLLMAdapter(LLMAdapter):
    """Ollama-backed local adapter.

    Uses the local Ollama HTTP API so the conversation layer can switch
    between hosted GPT and a local model without changing its interface.

    Attributes:
        supports_vision: Whether configured local model supports vision.

    Methods:
        generate_reply: Generate text responses via Ollama.
        _model_name_for_request: Resolve model name for request.
        _routed_model_name: Select model based on routing rules.
    """

    def __init__(self, *, think: bool | None = None, maximum_output_tokens: int | None = None,
                 json_output: bool = False, maximum_context_tokens: int | None = None,
                 json_schema: dict | None = None, keep_alive: str | int | None = None,
                 temperature: float | int | None = None):
        if maximum_output_tokens is not None and (type(maximum_output_tokens) is not int or not 256 <= maximum_output_tokens <= 4096):
            raise ValueError('invalid local output budget')
        self.think = think
        self.maximum_output_tokens = maximum_output_tokens
        self.json_output = json_output
        self.json_schema = json_schema
        if maximum_context_tokens is not None and (type(maximum_context_tokens) is not int or not 4096 <= maximum_context_tokens <= 32768):
            raise ValueError('invalid local context budget')
        self.maximum_context_tokens = maximum_context_tokens
        self.keep_alive = validate_keep_alive(keep_alive) if keep_alive is not None else None
        self.temperature = validate_primary_temperature(temperature) if temperature is not None else None

    @property
    def supports_vision(self) -> bool:
        """Check if local model supports vision inputs."""
        return settings.local_llm_supports_vision

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
        *,
        call_context: LLMCallContext | None = None,
        model_override: str | None = None,
        endpoint_override: str | None = None,
        admission_foreground: bool | None = None,
        admission_checkpoint: Callable[[], None] | None = None,
    ) -> LLMReply:
        """Generate text response via local Ollama endpoint.
        
        Args:
            system_prompt: System prompt for the model.
            user_text: User message text.
            image_inputs: Optional list of image inputs.
            call_context: Rebuilds the prompt when an explicit context cap is set.
            model_override: Optional model name override.
            endpoint_override: Optional Ollama endpoint override.
            
        Returns:
            LLMReply with generated text.
            
        Raises:
            ConfigurationError: If vision enabled but model doesn't support it
            or if model capability check fails.
            ExternalServiceError: If request fails or returns empty response.
        """
        scoped_priority, scoped_checkpoint = _ADMISSION_SCOPE.get()
        priority = scoped_priority if admission_foreground is None else admission_foreground
        checkpoint = admission_checkpoint if admission_checkpoint is not None else scoped_checkpoint
        if type(priority) is not bool or checkpoint is not None and not callable(checkpoint):
            raise ValueError('invalid local inference admission')
        if checkpoint is not None:
            checkpoint()
        endpoint = (endpoint_override or settings.local_llm_endpoint).rstrip("/")
        if image_inputs:
            if not self.supports_vision:
                raise ConfigurationError(
                    "Local vision is disabled. Set SHANA_LOCAL_LLM_SUPPORTS_VISION=true and configure a multimodal Ollama model."
                )
            target_model = self._model_name_for_request(has_images=True, model_override=model_override)
            capability = probe_ollama_model_capabilities(
                endpoint=endpoint,
                model=target_model,
                timeout_seconds=min(settings.local_llm_timeout_seconds, 15),
            )
            if not capability.get("ok"):
                raise ExternalServiceError(
                    f"Local vision capability check failed for model {target_model}: {capability.get('detail', 'unknown error')}"
                )
            if not capability.get("supports_vision"):
                raise ConfigurationError(
                    f"Configured local vision model {target_model} does not report vision capability from Ollama /api/show."
                )
        target_model = self._model_name_for_request(
            has_images=bool(image_inputs),
            system_prompt=system_prompt,
            user_text=user_text,
            model_override=model_override,
        )
        if self.maximum_context_tokens is not None:
            usable = self.maximum_context_tokens - (self.maximum_output_tokens or 1024) - 1024
            if call_context and call_context.prompt_builder:
                system_prompt, user_text, _metadata = call_context.prompt_builder(usable)
            if estimate_text_tokens(system_prompt, user_text) > usable:
                raise ContextOverflowError('isolated local prompt exceeds its bounded context')
        runtime_profile = current_local_runtime_profile()
        keep_alive = runtime_profile.get('primary_keep_alive', self.keep_alive)
        if keep_alive is None:
            keep_alive = settings.local_llm_keep_alive
        payload = {
            "model": target_model,
            "system": system_prompt,
            "prompt": user_text,
            "stream": False,
            "keep_alive": keep_alive,
        }
        runtime_context_tokens = self.maximum_context_tokens or model_capability("local", target_model).runtime_context_tokens
        if runtime_context_tokens is not None:
            payload["options"] = {"num_ctx": runtime_context_tokens}
        if self.think is not None:
            payload['think'] = self.think
        if self.json_schema is not None:
            payload['format'] = self.json_schema
        elif self.json_output:
            payload['format'] = 'json'
        if self.maximum_output_tokens is not None:
            payload.setdefault('options', {}).setdefault('num_ctx', 8192)
            payload['options']['num_predict'] = self.maximum_output_tokens
        temperature = runtime_profile.get('primary_temperature', self.temperature)
        if temperature is not None:
            payload.setdefault('options', {})['temperature'] = temperature
        if image_inputs:
            payload["images"] = [base64.b64encode(item.data).decode("ascii") for item in image_inputs]
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            endpoint + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        checkpoint_error = []
        def checked_admission():
            try:
                checkpoint()
            except BaseException as exc:
                checkpoint_error.append(exc)
                raise
        admission = {'foreground': priority, 'timeout': settings.local_llm_timeout_seconds}
        if checkpoint is not None:
            admission['checkpoint'] = checked_admission
        admission_started = time.perf_counter()
        with_timing = {}
        try:
            with backend_slot(endpoint, **admission):
                with_timing['admission_ms'] = round((time.perf_counter() - admission_started) * 1000, 3)
                if (runtime_profile.get('primary_prefix_cache') is True and self.think is False
                        and self.json_schema is not None and self.maximum_output_tokens == 1024
                        and runtime_context_tokens == 8192 and keep_alive == '30m'):
                    prefix = private_prefix(system_prompt, model=target_model, endpoint=endpoint,
                                            has_images=bool(image_inputs))
                    if prefix is not None:
                        with_timing.update(prepare_private_prefix(prefix, endpoint=endpoint,
                            timeout_seconds=settings.local_llm_timeout_seconds,
                            checkpoint=checked_admission if checkpoint is not None else None))
                http_started = time.perf_counter()
                with request.urlopen(req, timeout=settings.local_llm_timeout_seconds) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                with_timing['http_ms'] = round((time.perf_counter() - http_started) * 1000, 3)
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            if is_context_overflow_message(details):
                raise ContextOverflowError(f"Local LLM context overflow: {details}") from exc
            raise ExternalServiceError(f"Local LLM request failed: HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise ExternalServiceError(f"Local LLM request failed: {exc}") from exc
        except GammaError:
            raise
        except Exception as exc:
            if checkpoint_error and checkpoint_error[-1] is exc:
                raise
            if is_context_overflow_message(str(exc)):
                raise ContextOverflowError(f"Local LLM context overflow: {exc}") from exc
            raise ExternalServiceError(f"Local LLM request failed: {exc}") from exc
        text = (data.get("response") or "").strip()
        if not text:
            raise ExternalServiceError("Local model returned an empty response.")
        return LLMReply(text=text, metadata={'local_timing': {**local_generation_metrics(data), **local_cache_counts(data), **with_timing}})

    def _model_name_for_request(
        self,
        *,
        has_images: bool,
        system_prompt: str = "",
        user_text: str = "",
        model_override: str | None = None,
    ) -> str:
        """Resolve model name for request.
        
        Args:
            has_images: Whether request has image inputs.
            system_prompt: System prompt for routing decision.
            user_text: User text for routing decision.
            model_override: Optional model name override.
            
        Returns:
            str: Model name to use.
        """
        if model_override:
            return model_override
        if has_images:
            configured = (settings.local_llm_vision_model or "").strip()
            if configured:
                return configured
        if settings.local_llm_enable_routing and not settings.llm_router_enabled:
            routed = self._routed_model_name(system_prompt=system_prompt, user_text=user_text)
            if routed:
                return routed
        return settings.local_llm_model

    def _routed_model_name(self, *, system_prompt: str, user_text: str) -> str | None:
        """Select model based on routing rules.
        
        Args:
            system_prompt: System prompt context.
            user_text: User text for routing.
            
        Returns:
            str | None: Selected model or None if no routing applies.
        """
        tagging_model = (settings.local_llm_tagging_model or "").strip()
        light_model = (settings.local_llm_light_model or "").strip()
        system_lower = system_prompt.lower()
        prompt_words = len((user_text or "").split())

        if "strict json metadata extractor" in system_lower and tagging_model:
            return tagging_model
        if "reply planner for a spoken assistant" in system_lower and (tagging_model or light_model):
            return tagging_model or light_model
        if "generating the next sentence for a spoken assistant reply" in system_lower and (tagging_model or light_model):
            return tagging_model or light_model
        if "revising a draft reply after safe local tool calls were executed" in system_lower and light_model:
            return light_model
        if not light_model:
            return None
        if prompt_words > settings.local_llm_light_max_input_words:
            return None
        complex_markers = (
            "why ",
            "how ",
            "compare ",
            "plan ",
            "debug ",
            "error ",
            "remember ",
            "tool ",
            "provider ",
            "memory ",
            "code ",
        )
        lowered = (user_text or "").lower()
        if any(marker in lowered for marker in complex_markers):
            return None
        return light_model
