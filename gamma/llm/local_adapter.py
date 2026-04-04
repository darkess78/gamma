from __future__ import annotations

import json
from urllib import error, request

from ..config import settings
from ..errors import ExternalServiceError
from .base import LLMAdapter, LLMReply


class LocalLLMAdapter(LLMAdapter):
    """Ollama-backed local adapter.

    Uses the local Ollama HTTP API so the conversation layer can switch
    between hosted GPT and a local model without changing its interface.
    """

    def generate_reply(self, system_prompt: str, user_text: str) -> LLMReply:
        payload = {
            "model": settings.local_llm_model,
            "system": system_prompt,
            "prompt": user_text,
            "stream": False,
        }
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
