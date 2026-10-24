import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from gamma.api import routes
from gamma.brain.queue import BrainQueue
from gamma.presence import PresenceService
from gamma.schemas.presence import AudienceSelection, PresenceWakeRequest
from gamma.schemas.response import AssistantResponse


def test_slow_wake_delivery_releases_brain_and_new_sleep_suppresses_output(tmp_path, monkeypatch):
    monkeypatch.setattr("gamma.presence.presence_state_path", lambda: tmp_path / "presence.json")
    conversation, bus, dispatcher = Mock(), Mock(), Mock()
    response = AssistantResponse(spoken_text="Fictional greeting", tts_metadata={"delivery_grant": "fixture-grant"})
    conversation.respond_presence_wake.return_value = response
    bus.has_eligible_listener.return_value = True
    service = PresenceService(conversation=conversation, memory=Mock(), bus=bus, dispatcher=dispatcher, identity=Mock())
    queue = BrainQueue()
    monkeypatch.setattr(routes, "get_brain_queue", lambda: queue)
    monkeypatch.setattr(routes, "get_presence_service", lambda: service)
    entered, release = threading.Event(), threading.Event()
    def slow_delivery(*args, **kwargs):
        entered.set()
        assert release.wait(5)
    conversation.deliver_response.side_effect = slow_delivery
    try:
        with ThreadPoolExecutor(1) as requests:
            request = requests.submit(routes.shana_presence_wake, PresenceWakeRequest(audience=AudienceSelection(kind="unknown")))
            try:
                assert entered.wait(3)
                assert queue.submit(lambda: "next decision").result(2) == "next decision"
                service.transition("sleep")
            finally:
                release.set()
            result = request.result(3)
        assert result["wake"]["reason"] == "presence_changed"
        assert conversation.respond_presence_wake.call_args.kwargs["defer_delivery"] is True
        conversation.cancel_delivery.assert_called_once_with("fixture-grant")
        dispatcher.dispatch.assert_not_called()
        conversation.record_durable_output.assert_not_called()
    finally:
        release.set()
        queue.close()


def test_generated_wake_audio_is_ready_not_played(tmp_path, monkeypatch):
    monkeypatch.setattr("gamma.presence.presence_state_path", lambda: tmp_path / "presence.json")
    conversation, bus = Mock(), Mock()
    conversation.respond_presence_wake.return_value = AssistantResponse(spoken_text="Fixture", audio_path="fixture.wav")
    bus.has_eligible_listener.return_value = True
    service = PresenceService(conversation=conversation, memory=Mock(), bus=bus, dispatcher=Mock(), identity=Mock())
    result = service.wake(audience=AudienceSelection(kind="unknown"))
    assert result["wake"]["status"] == "audio_ready"
    assert conversation.record_durable_output.call_args.kwargs["status"] == "ready"
    assert conversation.record_durable_output.call_args.kwargs["spoken"] is False
