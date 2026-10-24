from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamma.dashboard import main, playback_proxy as proxy


def test_proxy_rejects_unauthenticated_http_without_upstream():
    with patch.object(main, "is_authenticated", return_value=False), patch.object(proxy.httpx, "AsyncClient") as client:
        response = TestClient(main.app).get("/api/playback/audio/" + "a" * 32)
        assert response.status_code == 401
        client.assert_not_called()


@pytest.mark.parametrize("origin,authenticated,code", [
    ("http://foreign", True, 1008), ("http://testserver", False, 4401), (None, True, 1008)])
def test_proxy_rejects_cross_origin_or_unauthenticated_socket(origin, authenticated, code):
    with patch.object(proxy, "websocket_is_authenticated", return_value=authenticated), patch.object(proxy, "_connect") as connect:
        with pytest.raises(WebSocketDisconnect) as error:
            with TestClient(main.app).websocket_connect("/api/playback/events", headers={"origin": origin} if origin else {}):
                pass
        assert error.value.code == code
        connect.assert_not_called()


@pytest.mark.parametrize("status", [200, 410, 302])
def test_audio_proxy_keeps_credentials_server_side_and_never_follows_redirect(status):
    observed = []
    grant = "a" * 32
    def upstream(request):
        observed.append(request)
        return httpx.Response(status, content=b"approved", headers={"X-Content-SHA256": "digest", "Location": "https://untrusted.invalid"})
    real_client = httpx.AsyncClient
    def factory(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(upstream))
    with patch.object(main, "is_authenticated", return_value=True), patch.object(proxy.settings, "api_auth_enabled", True), patch.object(
        proxy.settings, "api_bearer_token", "synthetic-fixture"
    ), patch.object(proxy.httpx, "AsyncClient", side_effect=factory):
        response = TestClient(main.app).get(f"/api/playback/audio/{grant}")
    assert len(observed) == 1
    assert observed[0].url.path == f"/v1/delivery/{grant}/audio"
    assert observed[0].headers["authorization"] == "Bearer synthetic-fixture"
    assert "synthetic-fixture" not in response.text
    assert response.status_code == (502 if status == 302 else status)
    if status == 200:
        assert response.content == b"approved"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-sha256"] == "digest"


def test_audio_proxy_enforces_download_bound():
    real_client = httpx.AsyncClient
    def factory(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"oversize")))
    with patch.object(main, "is_authenticated", return_value=True), patch.object(proxy, "MAX_AUDIO_BYTES", 4), patch.object(
        proxy.httpx, "AsyncClient", side_effect=factory
    ):
        response = TestClient(main.app).get("/api/playback/audio/" + "a" * 32)
    assert response.status_code == 502
