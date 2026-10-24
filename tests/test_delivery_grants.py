import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from state_fixtures import commit_response

from gamma.delivery.authority import DeliveryAuthority, DeliveryDenied
from gamma.delivery.synthesis import synthesize_response
from gamma.schemas.response import AssistantResponse


@pytest.fixture
def delivery(tmp_path):
    authority = DeliveryAuthority(database_url=f"sqlite:///{tmp_path / 'grants.db'}")
    response = AssistantResponse(spoken_text="Fictional approved speech")
    commit_response(authority, response)
    context = dict(target="dashboard_monitor", audience="private", policy="policy-one")
    grant = authority.issue(response, **context)
    return authority, response, context, grant


@pytest.mark.parametrize("changed", ["text", "target", "audience", "policy", "emotion", "styles"])
def test_approval_binds_exact_content_and_context(delivery, changed):
    authority, response, context, grant = delivery
    if changed == "text":
        response.speech_text += " changed"
    elif changed == "emotion":
        response.emotion = "happy"
    elif changed == "styles":
        response.voice_styles.append("soft")
    else:
        context[changed] = "different"
    with pytest.raises(DeliveryDenied):
        authority.check(grant, response, **context)


def test_cancel_during_synthesis_discards_late_audio(delivery):
    authority, response, context, grant = delivery
    started, release = threading.Event(), threading.Event()
    def synthesize(**kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(audio_path="late.wav", content_type="audio/wav", metadata={})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(synthesize_response, response, SimpleNamespace(synthesize=synthesize),
            authority=authority, grant_id=grant, context=lambda: context)
        try:
            assert started.wait(2)
            assert authority.cancel(grant)
        finally:
            release.set()
        with pytest.raises(DeliveryDenied):
            future.result(timeout=3)
    assert response.audio_path is None and response.audio_content_type is None


def test_expiry_restart_and_target_cancellation_invalidate_grants(delivery, monkeypatch):
    authority, response, context, grant = delivery
    restarted = DeliveryAuthority(database_url=str(authority.engine.url), generation="restarted")
    with pytest.raises(DeliveryDenied):
        restarted.check(grant, response, **context)
    assert authority.cancel_target(context["target"]) == 1
    with pytest.raises(DeliveryDenied):
        authority.check(grant, response, **context)
    fresh = authority.issue(response, **context)
    monkeypatch.setattr("gamma.delivery.authority.time.time", lambda: 10**12)
    with pytest.raises(DeliveryDenied):
        authority.check(fresh, response, **context)


def test_policy_change_during_provider_work_rejects_artifact(delivery):
    authority, response, context, grant = delivery
    def synthesize(**kwargs):
        context["policy"] = "policy-two"
        return SimpleNamespace(audio_path="obsolete.wav", content_type="audio/wav", metadata={})
    with pytest.raises(DeliveryDenied):
        synthesize_response(response, SimpleNamespace(synthesize=synthesize), authority=authority, grant_id=grant, context=lambda: context)
    assert response.audio_path is None


def test_successful_synthesis_is_not_playback_or_repeat_dispatch(delivery, tmp_path):
    authority, response, context, grant = delivery
    audio = tmp_path / "fixture.wav"
    audio.write_bytes(b"synthetic audio fixture")
    synthesizer = SimpleNamespace(synthesize=lambda **kwargs: SimpleNamespace(audio_path=str(audio), content_type="audio/wav",
        metadata={"delivery_grant": "forged", "playback_approved": True}))
    synthesize_response(response, synthesizer, authority=authority, grant_id=grant, context=lambda: context)
    assert authority.check(grant, response, **context)["status"] == "synthesized"
    assert response.tts_metadata["delivery_grant"] == grant
    assert "playback_approved" not in response.tts_metadata
    authority.dispatch(grant, response, **context)
    with pytest.raises(DeliveryDenied):
        authority.dispatch(grant, response, **context)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork inheritance")
def test_inherited_authority_cannot_release_parent_grant(delivery):
    authority, response, context, grant = delivery
    process_context = multiprocessing.get_context("fork")
    receiver, sender = process_context.Pipe(duplex=False)

    def child():
        outcomes = []
        for operation in (lambda: authority.check(grant, response, **context),
                          lambda: authority.issue(response, **context)):
            try:
                operation()
                outcomes.append("accepted")
            except DeliveryDenied:
                outcomes.append("denied")
        sender.send(outcomes)

    process = process_context.Process(target=child)
    process.start()
    try:
        assert receiver.poll(5)
        assert receiver.recv() == ["denied", "denied"]
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiver.close()
        sender.close()
    assert authority.check(grant, response, **context)["status"] == "authorized"
