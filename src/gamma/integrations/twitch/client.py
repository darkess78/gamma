from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from ...config import settings
from ...errors import ExternalServiceError
from ...stream.models import StreamInputEvent

_ALLOWED_STATES = frozenset({"accepted", "running", "completed", "failed", "cancelled", "uncertain"})


class GammaStreamClient:
    def __init__(self, *, base_url: str | None = None, bearer_token: str | None = None, timeout_seconds: int = 60, queued: bool = False) -> None:
        self.base_url = (base_url or settings.shana_base_url).rstrip("/")
        self.bearer_token = bearer_token if bearer_token is not None else settings.api_bearer_token
        self.timeout_seconds = timeout_seconds
        self.queued = queued

    def post_event(
        self,
        event: StreamInputEvent,
        *,
        synthesize_speech: bool = False,
        fast_mode: bool = True,
    ) -> dict[str, Any]:
        request_id = str(event.metadata.get("request_id") or uuid4().hex)
        event.metadata.setdefault("request_id", request_id)
        query = urllib.parse.urlencode(
            {
                "synthesize_speech": "true" if synthesize_speech else "false",
                "fast_mode": "true" if fast_mode else "false",
            }
        )
        if self.queued:
            body = json.dumps({**event.model_dump(mode="json"), "schema_version": 1},
                              separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        else:
            body = event.model_dump_json().encode("utf-8")
        if self.queued and len(body) > 65536:
            raise ExternalServiceError("queued event body exceeds maximum size")
        request = urllib.request.Request(
            f"{self.base_url}/v1/stream/{'jobs' if self.queued else 'events'}?{query}",
            data=body,
            headers=self._headers(request_id=request_id),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if self.queued and response.status != 202:
                    raise ExternalServiceError("queued event returned unexpected status")
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("stream response exceeds limit")
                payload = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ExternalServiceError(f"stream event post failed: http-{exc.code}") from exc
        except Exception as exc:
            raise ExternalServiceError(f"stream event post failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError("stream event post returned a non-object payload")
        if self.queued:
            self._validate_receipt(payload, event.event_id)
        return payload

    def _validate_receipt(self, payload: dict[str, Any], event_id: str) -> None:
        try:
            version = payload["schema_version"]
            job_id = payload["job_id"]
            state = payload["state"]
            duplicate = payload["duplicate"]
        except (KeyError, TypeError):
            raise ExternalServiceError("queued event receipt is malformed") from None
        if (type(version) is not int or version != 1 or job_id != event_id
                or not isinstance(state, str) or state not in _ALLOWED_STATES or type(duplicate) is not bool):
            raise ExternalServiceError("queued event receipt is malformed")

    def _headers(self, *, request_id: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if request_id:
            headers["X-Request-ID"] = request_id
        if settings.api_auth_enabled and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers
