"""The owner's dashboard is the one channel that opens Shana's private memory."""
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from gamma import main
from gamma.api import deps
from gamma.config import settings
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.stream.brain import StreamBrain
from gamma.system import owner_channel
from gamma.voice import live_jobs
from tests.test_conversation_recipient import FACT, PRIVATE_WORK, Dispatcher, Reviewer, fixture_state, prompt_layers  # noqa: F401


@pytest.fixture
def api(fixture_state, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    brain = StreamBrain(conversation=fixture_state.service, output_dispatcher=Dispatcher(), safety_reviewer=Reviewer())
    monkeypatch.setattr(deps, "get_stream_brain", lambda: brain)
    monkeypatch.setattr(deps, "get_conversation_service", lambda: fixture_state.service)
    monkeypatch.setattr("gamma.api.routes_stream.load_presence_state", lambda **kwargs: {})
    monkeypatch.setattr("gamma.api.routes_stream.apply_presence_to_stream_event",
                        lambda event, **kwargs: (event, kwargs["synthesize_speech"]))
    return TestClient(main.app), fixture_state.service._llm


def owner_header():
    return {owner_channel.HEADER: owner_channel.owner_channel_secret()}


def monitor_event(kind="mic_transcript", source="local_monitor", target="dashboard_monitor"):
    return {"kind": kind, "text": "Shana, fixture topic", "session_id": "mixed",
            "actor": {"source": source, "platform_id": "owner", "roles": ["owner"]},
            "metadata": {"output_target_policy": target}}


def sees_private(llm):
    layers = prompt_layers(llm)
    return any(FACT in layer for layer in layers), any(PRIVATE_WORK in layer for layer in layers)


def test_owner_monitor_text_gets_memory_and_history(api):
    client, llm = api
    assert client.post("/v1/stream/events", json=monitor_event(), headers=owner_header()).status_code == 200
    assert sees_private(llm) == (True, True)


@pytest.mark.parametrize("headers", [{}, {owner_channel.HEADER: "x" * 43}])
def test_monitor_claims_without_the_owner_secret_stay_closed(api, headers):
    client, llm = api
    assert client.post("/v1/stream/events", json=monitor_event(), headers=headers).status_code == 200
    assert sees_private(llm) == (False, False)


@pytest.mark.parametrize("event", [
    monitor_event(target="stream_public"),   # owner speaking on stream: public audience
    monitor_event(kind="chat_message"),      # Monitor rehearsing public chat
    monitor_event(source="twitch"),          # someone else's channel, even with the secret
])
def test_public_audiences_never_get_private_memory(api, event):
    client, llm = api
    assert client.post("/v1/stream/events", json=event, headers=owner_header()).status_code == 200
    assert sees_private(llm) == (False, False)


def test_direct_conversation_route_needs_the_owner_secret(api):
    client, llm = api
    body = {"user_text": "fixture topic", "session_id": "mixed"}
    assert client.post("/v1/conversation/respond", json=body).status_code == 200
    assert sees_private(llm) == (False, False)
    llm.calls.clear()
    assert client.post("/v1/conversation/respond", json=body, headers=owner_header()).status_code == 200
    assert sees_private(llm) == (True, True)


def test_dashboard_sends_the_owner_secret(fixture_state):
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request.headers) or httpx.Response(200, json={}))
    ShanaApiClient(base_url="http://shana", transport=transport).get("/health")
    assert seen[0][owner_channel.HEADER] == owner_channel.owner_channel_secret()
    assert (settings.data_dir / "runtime" / "owner-channel.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("verified", [True, False])
def test_live_voice_worker_is_told_only_when_verified(fixture_state, tmp_path, verified):
    manager = live_jobs.LiveVoiceJobManager.__new__(live_jobs.LiveVoiceJobManager)
    manager._foreground_python = lambda: "python"
    with patch.object(live_jobs.subprocess, "Popen") as popen:
        manager._spawn_worker(turn_id="t", input_path=tmp_path / "a.wav", output_path=tmp_path / "o",
                              status_path=tmp_path / "s", session_id="mixed", synthesize_speech=False,
                              response_mode="simple_chunked", stdout_log=tmp_path / "out",
                              stderr_log=tmp_path / "err", owner_verified=verified)
    assert ("--owner-verified" in popen.call_args.args[0]) is verified
