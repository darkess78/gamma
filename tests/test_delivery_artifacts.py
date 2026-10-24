import hashlib
from pathlib import Path

import pytest
from state_fixtures import commit_response
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlmodel import Session

from gamma.api import routes
from gamma.config import settings
from gamma.delivery.artifacts import snapshot, verified_bytes
from gamma.delivery.authority import DeliveryDenied
from gamma.delivery.playback import PlaybackLedger
from gamma.memory.state_authority import delivery_artifacts
from gamma.schemas.response import AssistantResponse


@pytest.fixture
def approved(tmp_path):
    ledger = PlaybackLedger(database_url=f"sqlite:///{tmp_path / 'state.db'}")
    source = tmp_path / "provider.wav"
    source.write_bytes(b"approved synthetic audio")
    response = AssistantResponse(spoken_text="Approved text", audio_path=str(source), audio_content_type="audio/wav")
    commit_response(ledger, response)
    context = dict(target="stream_public", audience="public", policy="fixture")
    grant = ledger.issue(response, **context)
    ledger.dispatch(grant, response, **context)
    return ledger, source, response, grant


def test_snapshot_serves_approved_bytes_after_producer_changes(approved):
    ledger, source, response, grant = approved
    source.write_bytes(b"changed producer bytes")
    data, mime, digest = ledger.read_audio(grant)
    assert data == b"approved synthetic audio" and mime == "audio/wav"
    assert digest == hashlib.sha256(data).hexdigest()
    assert response.tts_metadata["delivery_audio_url"].endswith(f"/{grant}/audio")


def test_appended_or_replaced_snapshot_is_rejected(approved):
    ledger, source, response, grant = approved
    with Session(ledger.engine) as session:
        path = Path(session.execute(select(delivery_artifacts.c.path)).scalar_one())
    path.write_bytes(path.read_bytes() + b"extra")
    with pytest.raises(DeliveryDenied, match="integrity"):
        ledger.read_audio(grant)


def test_grant_audio_endpoint_rechecks_cancellation(approved, monkeypatch):
    ledger, source, response, grant = approved
    monkeypatch.setattr(routes, "get_playback_ledger", lambda: ledger)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    url = f"/v1/delivery/{grant}/audio"
    served = client.get(url)
    assert served.status_code == 200 and served.content == b"approved synthetic audio"
    assert served.headers["cache-control"] == "no-store"
    ledger.cancel(grant)
    assert client.get(url).status_code == 410


def test_receiver_offer_binds_grant_target_and_audio_reference(approved):
    ledger, source, response, grant = approved
    lease = ledger.open(receiver="fixture", target="stream_public")
    event = dict(event_id="fixture", target_policy="stream_public", payload={
        "delivery_grant": grant, "audio_url": response.tts_metadata["delivery_audio_url"]})
    substituted = {**event, "payload": {**event["payload"], "audio_url": "/unapproved.wav"}}
    with pytest.raises(DeliveryDenied):
        ledger.offer(lease, "fixture", substituted)
    assert ledger.offer(lease, "fixture", event)["event_id"] == "fixture"
    ledger.cancel(grant)
    with pytest.raises(DeliveryDenied):
        ledger.prepare(lease, "fixture", dict(type="playback_prepare", schema_version=1,
            lease_id=lease, event_id="fixture"), eligible=lambda target: True,
            target_epoch=lambda target: 0, presence="")
    with pytest.raises(DeliveryDenied):
        ledger.offer(lease, "fixture", {**event, "event_id": "later"})


@pytest.mark.parametrize("change", ["policy", "target_stop", "expiry", "generation"])
def test_context_changes_revoke_audio(approved, monkeypatch, change):
    ledger, source, response, grant = approved
    if change == "policy":
        monkeypatch.setattr(settings, "speech_filter_llm_enabled", not settings.speech_filter_llm_enabled)
    elif change == "target_stop":
        ledger.cancel_target("stream_public")
    elif change == "expiry":
        monkeypatch.setattr("gamma.delivery.authority.time.time", lambda: 10**12)
    else:
        ledger = PlaybackLedger(database_url=str(ledger.engine.url), generation="replacement")
    with pytest.raises(DeliveryDenied):
        ledger.read_audio(grant)


def test_snapshot_rejects_empty_oversized_symlink_and_nonfile(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"")
    with pytest.raises(ValueError):
        snapshot(source, tmp_path / "approved")
    source.write_bytes(b"1234")
    with pytest.raises(ValueError):
        snapshot(source, tmp_path / "approved", max_bytes=3)
    linked = tmp_path / "link"
    linked.symlink_to(source)
    with pytest.raises((ValueError, OSError)):
        snapshot(linked, tmp_path / "approved")
    with pytest.raises((ValueError, OSError)):
        snapshot(tmp_path, tmp_path / "approved")


@pytest.mark.parametrize("change", ["cancel", "policy", "epoch"])
def test_active_grant_revocation_is_durable_and_prevents_late_completion(approved, monkeypatch, change):
    ledger, source, response, grant = approved
    lease = ledger.open(receiver="fixture", target="stream_public")
    event = dict(event_id="fixture", target_policy="stream_public", payload={
        "delivery_grant": grant, "audio_url": response.tts_metadata["delivery_audio_url"]})
    ledger.offer(lease, "fixture", event)
    context = dict(eligible=lambda target: True, target_epoch=lambda target: 0, presence="")
    ledger.prepare(lease, "fixture", dict(type="playback_prepare", schema_version=1,
        lease_id=lease, event_id="fixture"), **context)
    ack = dict(type="playback_ack", schema_version=1, lease_id=lease, event_id="fixture", status="started")
    ledger.acknowledge(lease, "fixture", ack)
    if change == "cancel":
        ledger.cancel(grant)
    elif change == "epoch":
        ledger.cancel_target("stream_public")
    else:
        monkeypatch.setattr(settings, "speech_filter_llm_enabled", not settings.speech_filter_llm_enabled)
    assert ledger.revoke_obsolete(lease, "fixture", **context) == ["fixture"]
    assert ledger.revoke_obsolete(lease, "fixture", **context) == []
    with pytest.raises((ValueError, DeliveryDenied)):
        ledger.acknowledge(lease, "fixture", {**ack, "status": "completed"})
