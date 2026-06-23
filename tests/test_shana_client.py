from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from gamma.config import settings
from gamma.dashboard.shana_client import ShanaApiClient, ShanaClientError


def test_client_forwards_api_auth_and_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content) == {"hello": "Shana"}
        return httpx.Response(200, json={"ok": True})

    with (
        patch.object(settings, "api_auth_enabled", True),
        patch.object(settings, "api_bearer_token", "secret"),
    ):
        client = ShanaApiClient(base_url="http://shana.test", transport=httpx.MockTransport(handler))
    try:
        assert client.post("/v1/test", {"hello": "Shana"}) == {"ok": True}
    finally:
        client.close()


def test_client_preserves_safe_upstream_status() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(404, json={"detail": "missing"}))
    client = ShanaApiClient(base_url="http://shana.test", transport=transport)
    try:
        with pytest.raises(ShanaClientError) as caught:
            client.get("/v1/missing")
    finally:
        client.close()
    assert caught.value.status_code == 404
    assert caught.value.detail == "missing"


def test_client_maps_timeout_to_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = ShanaApiClient(base_url="http://shana.test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ShanaClientError) as caught:
            client.get("/health")
    finally:
        client.close()
    assert caught.value.status_code == 504


def test_client_builds_multipart_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content_type = request.headers.get("content-type", "")
        assert content_type.startswith("multipart/form-data; boundary=")
        assert b'name="session_id"' in request.content
        assert b'name="audio_file"; filename="clip.wav"' in request.content
        assert b"RIFF" in request.content
        return httpx.Response(200, json={"transcript": "hello"})

    client = ShanaApiClient(base_url="http://shana.test", transport=httpx.MockTransport(handler))
    try:
        payload = client.post_multipart(
            "/v1/voice/roundtrip",
            data={"session_id": "talk"},
            field_name="audio_file",
            filename="clip.wav",
            content=b"RIFF",
            content_type="audio/wav",
        )
    finally:
        client.close()
    assert payload["transcript"] == "hello"
