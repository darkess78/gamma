from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...config import settings
from ...errors import ExternalServiceError
from ...stream.models import StreamInputEvent


class GammaStreamClient:
    def __init__(self, *, base_url: str | None = None, bearer_token: str | None = None, timeout_seconds: int = 60) -> None:
        self.base_url = (base_url or settings.shana_base_url).rstrip("/")
        self.bearer_token = bearer_token if bearer_token is not None else settings.api_bearer_token
        self.timeout_seconds = timeout_seconds

    def post_event(
        self,
        event: StreamInputEvent,
        *,
        synthesize_speech: bool = False,
        fast_mode: bool = True,
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "synthesize_speech": "true" if synthesize_speech else "false",
                "fast_mode": "true" if fast_mode else "false",
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/v1/stream/events?{query}",
            data=event.model_dump_json().encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExternalServiceError(f"stream event post failed: http-{exc.code} {detail}") from exc
        except Exception as exc:
            raise ExternalServiceError(f"stream event post failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError("stream event post returned a non-object payload")
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.api_auth_enabled and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

