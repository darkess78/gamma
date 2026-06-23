from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings


@dataclass(slots=True)
class ShanaClientError(RuntimeError):
    detail: str
    status_code: int = 502

    def __str__(self) -> str:
        return self.detail


class ShanaApiClient:
    """Synchronous internal client for the separately running Shana API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if settings.api_auth_enabled and settings.api_bearer_token:
            headers["Authorization"] = f"Bearer {settings.api_bearer_token}"
        self._client = httpx.Client(
            base_url=(base_url or settings.shana_internal_base_url).rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_payload,
                data=data,
                files=files,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise ShanaClientError("Shana API request timed out.", 504) from exc
        except httpx.RequestError as exc:
            raise ShanaClientError("Shana API is unavailable.", 502) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ShanaClientError("Shana API returned an invalid JSON response.", 502) from exc
        if not isinstance(payload, dict):
            raise ShanaClientError("Shana API returned a non-object response.", 502)
        if response.is_error:
            detail = str(payload.get("detail") or f"Shana API returned HTTP {response.status_code}.")
            status_code = response.status_code if response.status_code in {400, 401, 403, 404, 409, 422} else 502
            raise ShanaClientError(detail, status_code)
        return payload

    def safe_get(self, path: str, *, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        try:
            return self.request_json("GET", path, params=params, timeout=timeout)
        except ShanaClientError as exc:
            return {"ok": False, "detail": exc.detail, "status_code": exc.status_code}

    def get(self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        return self.request_json("GET", path, params=params, timeout=timeout)

    def post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self.request_json("POST", path, params=params, json_payload=payload or {}, timeout=timeout)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json("PUT", path, json_payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request_json("PATCH", path, json_payload=payload)

    def delete(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request_json("DELETE", path, params=params)

    def post_multipart(
        self,
        path: str,
        *,
        data: dict[str, Any],
        field_name: str,
        filename: str,
        content: bytes,
        content_type: str,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        return self.request_json(
            "POST",
            path,
            data=data,
            files={field_name: (filename, content, content_type)},
            timeout=timeout,
        )
