"""Audit repros: owner-private output reaching people other than the owner.

Each test states the safe behaviour and is marked strict xfail while the leak
exists. A test that starts passing means its fix landed; remove the marker then.
All data here is synthetic (``*_CANARY`` strings); no model, GPU or network use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gamma.api import deps
from gamma.config import settings
from gamma.dashboard.stream_admin import StreamAdminMixin
from gamma.main import app
from gamma.performer.bus import PerformerEventBus
from gamma.performer.models import PerformerOutputEvent
from gamma.presence import presence_state_for_mode
from gamma.safety.llm_reviewer import LLMReviewDecision
from gamma.schemas.response import AssistantResponse
from gamma.stream.brain import StreamBrain
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.output import PerformerBusOutputAdapter, StreamOutputDispatcher
from gamma.stream.temp_memory import StreamTempMemoryStore
from gamma.stream.trace import StreamTraceStore
from gamma.system import owner_channel

REPO = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-audit-api-token"
OWNER_SECRET = "synthetic-owner-channel-secret-0123456789abcdef"
REPLY_CANARY = "PRIVATE_REPLY_CANARY"


class _Conversation:
    def respond(self, **_kwargs):
        return AssistantResponse(spoken_text=f"{REPLY_CANARY} about your note.", emotion="neutral",
                                 motions=[], tool_calls=[], tool_results=[], memory_candidates=[])


class _AllowReviewer:
    def review(self, _text):
        return LLMReviewDecision(action="allow", reason="synthetic", confidence=1.0)


def _brain(tmp_path, bus):
    return StreamBrain(
        conversation=_Conversation(),  # type: ignore[arg-type]
        trace_store=StreamTraceStore(tmp_path / "trace.jsonl"),
        output_dispatcher=StreamOutputDispatcher([PerformerBusOutputAdapter(bus)]),
        safety_reviewer=_AllowReviewer(),
        temp_memory_store=StreamTempMemoryStore(database_url=f"sqlite:///{tmp_path / 'temp.db'}"),
    )


def _public_canary_events(bus):
    return [e for e in bus.recent(limit=200, target_policy="stream_public") if REPLY_CANARY in str(e.payload)]


class _MonitorCapture(StreamAdminMixin):
    """The dashboard's real Monitor event builder, with the HTTP call captured."""

    def __init__(self):
        self.posted = []

    def twitch_runtime_settings(self):
        return {}

    def _post_remote_json(self, path, payload, timeout=None):
        self.posted.append((path, payload))
        return {"input_event": payload}


@pytest.fixture(autouse=True)
def _no_llm_review(monkeypatch):
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", False)


@pytest.mark.xfail(strict=True, reason="AUDIT: go_live rewrites Monitor context/chat notes to stream_public")
@pytest.mark.parametrize("input_mode", ["context", "chat"])
def test_monitor_note_marked_monitor_only_is_not_broadcast_when_live(tmp_path, monkeypatch, input_mode):
    """The Monitor page says "monitor-only output" and sends output_target_policy=dashboard_monitor.

    In go_live, presence.apply_presence_to_stream_event sees kind=chat_message (the
    dashboard maps "Context note" and "Simulated chat" to it), treats it as Twitch
    chat and overwrites the target with stream_public, so Shana's reply to the
    owner's private note goes to the OBS overlay and stream voice.
    """
    capture = _MonitorCapture()
    capture.submit_monitor_stream_input({
        "input_mode": input_mode, "text": "Shana, NOTE_CANARY my private context",
        "output_target_policy": "dashboard_monitor", "synthesize_speech": False,
    })
    _path, event = capture.posted[0]
    assert event["metadata"]["output_target_policy"] == "dashboard_monitor"

    bus = PerformerEventBus()
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(deps, "get_stream_brain", lambda: _brain(tmp_path, bus))
    monkeypatch.setattr("gamma.api.routes_stream.load_presence_state",
                        lambda **_kwargs: presence_state_for_mode("go_live"))
    monkeypatch.setattr(owner_channel, "is_owner_channel", lambda value: value == OWNER_SECRET)
    client = TestClient(app)
    try:
        response = client.post("/v1/stream/events?synthesize_speech=false&fast_mode=true", json=event,
                               headers={owner_channel.HEADER: OWNER_SECRET})
    finally:
        client.close()
    assert response.status_code == 200
    assert response.json()["decision"]["decision"] == "reply"
    assert _public_canary_events(bus) == []


@pytest.mark.xfail(strict=True, reason="AUDIT: untagged owner-private output defaults to stream_public")
def test_owner_private_turn_without_target_never_reaches_stream_public(tmp_path):
    """An owner-channel Monitor turn with no output_target_policy is owner_private, yet
    _apply_output_target_policy leaves its events untagged and performer_event_from_stream_output
    fills the gap with DEFAULT_TARGET_POLICY = stream_public (fail open)."""
    bus = PerformerEventBus()
    brain = _brain(tmp_path, bus)
    result = brain.handle_event(
        StreamInputEvent(kind="mic_transcript", text="Shana, what is on my calendar?",
                         actor=StreamActor(source="local_monitor", platform_id="owner", roles=["owner"]),
                         metadata={}),
        owner_channel=True,
    )
    assert result.decision.decision == "reply"
    assert result.output_events
    assert _public_canary_events(bus) == []


@pytest.mark.xfail(strict=True, reason="AUDIT: overlay URL token can subscribe to dashboard_monitor")
def test_stream_overlay_query_token_cannot_read_private_monitor_target(monkeypatch):
    """OBS overlay/performer pages must carry the API token as ?token= (browsers can't send
    headers on WebSockets), and the page picks its own target_policy. The same token
    therefore lets anyone holding an overlay URL subscribe to dashboard_monitor and
    receive the owner's private replies (text and audio) live."""
    bus = PerformerEventBus()
    bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id="private-turn",
                                     target_policy="dashboard_monitor", payload={"text": REPLY_CANARY}))
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", TOKEN)
    monkeypatch.setattr(deps, "get_performer_bus", lambda: bus)
    client = TestClient(app)
    url = f"/v1/performer/events?token={TOKEN}&target_policy=dashboard_monitor&replay_recent=10"
    try:
        with client.websocket_connect(url) as ws:
            assert ws.receive_json()["type"] == "ready"
            leaked = ws.receive_json()
    except WebSocketDisconnect:
        return
    finally:
        client.close()
    assert REPLY_CANARY not in str(leaked)


@pytest.mark.xfail(strict=True, reason="AUDIT: nginx access log stores ?token= and ?key= secrets")
def test_repo_nginx_access_log_does_not_record_query_secrets():
    """deploy/nginx/nginx.conf uses the default "combined" access log, whose $request
    includes the query string, so every overlay/monitor WebSocket URL writes the API
    bearer token (and the games ?key=) to data/runtime/nginx.access.log."""
    path = REPO / "deploy" / "nginx" / "nginx.conf"
    if not path.exists():
        pytest.skip("deploy/ is not in this checkout")
    conf = path.read_text(encoding="utf-8")
    assert "log_format" in conf and "$request " not in conf and "$request_uri" not in conf


@pytest.mark.xfail(strict=True, reason="AUDIT: TTS files named by second collide across targets")
def test_two_tts_artifacts_in_the_same_second_get_distinct_files(tmp_path, monkeypatch):
    """Every file TTS backend names its output tts-YYYYmmddTHHMMSSZ.wav. Two syntheses in
    the same second (an owner Monitor reply and a Discord-call reply, say) write the
    same path; the later one overwrites the earlier, so the Discord voice session,
    which plays the file at audio_path afterwards, can play the owner's private reply."""
    from gamma.voice import tts_base
    from gamma.voice.tts_stub import StubTTSBackend

    class _FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(settings, "audio_output_dir", tmp_path)
    monkeypatch.setattr(tts_base, "datetime", _FrozenClock)
    backend = StubTTSBackend()
    private = backend.synthesize("owner private reply")
    public = backend.synthesize("public discord reply")
    assert private.audio_path != public.audio_path


@pytest.mark.xfail(strict=True, reason="AUDIT: audio artifact route serves mic captures and sidecars")
@pytest.mark.parametrize("name", ["mic-input-20261114T120000Z.wav", "tts-20261114T120000Z.txt"])
def test_audio_artifact_route_serves_only_tts_audio(tmp_path, monkeypatch, name):
    """/v1/audio/artifacts/{filename} serves any file in data/audio with a guessable
    timestamp name: raw owner mic captures (voice.controller) and the stub TTS's
    plaintext reply sidecar, not only synthesized speech."""
    (tmp_path / name).write_bytes(b"OWNER_AUDIO_CANARY")
    monkeypatch.setattr(settings, "audio_output_dir", tmp_path)
    monkeypatch.setattr(settings, "api_auth_enabled", True)
    monkeypatch.setattr(settings, "api_bearer_token", TOKEN)
    client = TestClient(app)
    try:
        response = client.get(f"/v1/audio/artifacts/{name}", headers={"Authorization": f"Bearer {TOKEN}"})
    finally:
        client.close()
    assert response.status_code == 404
