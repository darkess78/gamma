from __future__ import annotations

import base64
import json
from urllib import error, request

from ..errors import ConfigurationError
from ..config import settings
from ..errors import ExternalServiceError
from .ollama_probe import probe_ollama_model_capabilities
from .base import LLMAdapter, LLMImageInput, LLMReply


class LocalLLMAdapter(LLMAdapter):
    """Ollama-backed local adapter.

    Uses the local Ollama HTTP API so the conversation layer can switch
    between hosted GPT and a local model without changing its interface.
    """

    @property
    def supports_vision(self) -> bool:
        return settings.local_llm_supports_vision

    def generate_reply(
        self,
        system_prompt: str,
        user_text: str,
        image_inputs: list[LLMImageInput] | None = None,
    ) -> LLMReply:
        if image_inputs:
            if not self.supports_vision:
                raise ConfigurationError(
                    "Local vision is disabled. Set SHANA_LOCAL_LLM_SUPPORTS_VISION=true and configure a multimodal Ollama model."
                )
            target_model = self._model_name_for_request(has_images=True)
            capability = probe_ollama_model_capabilities(
                endpoint=settings.local_llm_endpoint,
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
        payload = {
            "model": self._model_name_for_request(
                has_images=bool(image_inputs),
                system_prompt=system_prompt,
                user_text=user_text,
            ),
            "system": system_prompt,
            "prompt": user_text,
            "stream": False,
        }
        if image_inputs:
            payload["images"] = [base64.b64encode(item.data).decode("ascii") for item in image_inputs]
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            settings.local_llm_endpoint.rstrip("/") + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=settings.local_llm_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise ExternalServiceError(f"Local LLM request failed: HTTP {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise ExternalServiceError(f"Local LLM request failed: {exc}") from exc
        except Exception as exc:
            raise ExternalServiceError(f"Local LLM request failed: {exc}") from exc
        text = (data.get("response") or "").strip()
        if not text:
            raise ExternalServiceError("Local model returned an empty response.")
        return LLMReply(text=text)

    def _model_name_for_request(self, *, has_images: bool, system_prompt: str = "", user_text: str = "") -> str:
        if has_images:
            configured = (settings.local_llm_vision_model or "").strip()
            if configured:
                return configured
        if settings.local_llm_enable_routing:
            routed = self._routed_model_name(system_prompt=system_prompt, user_text=user_text)
            if routed:
                return routed
        return settings.local_llm_model

    def _routed_model_name(self, *, system_prompt: str, user_text: str) -> str | None:
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
