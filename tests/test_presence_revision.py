from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import Mock, patch

import pytest

from gamma.presence import (PresenceService, StaleRevision, load_presence_state,
    save_presence_state, presence_state_for_mode)
from gamma.schemas.presence import AudienceSelection
from gamma.schemas.response import AssistantResponse


def test_concurrent_snapshots_cannot_overwrite_each_other(tmp_path):
    path = tmp_path / "state.json"
    initial = save_presence_state(presence_state_for_mode("wake"), path=path)
    barrier = threading.Barrier(2)
    def update(mode):
        state = load_presence_state(path=path)
        state["mode"] = mode
        barrier.wait(timeout=3)
        try:
            return save_presence_state(state, path=path)
        except StaleRevision:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["sleep", "break"]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert load_presence_state(path=path) == winners[0]
    assert winners[0]["revision"] == initial["revision"] + 1


def _service():
    conversation = Mock()
    conversation.respond_presence_wake.return_value = AssistantResponse(spoken_text="Hello")
    return PresenceService(conversation=conversation, memory=Mock(), bus=Mock(), dispatcher=Mock(), identity=Mock())


def test_status_reads_do_not_write_or_invalidate_snapshot(tmp_path):
    with patch("gamma.presence.presence_state_path", return_value=tmp_path / "state.json"):
        service = _service()
        state = service.transition("sleep")["state"]
        before = load_presence_state()
        assert service.status()["state"]["revision"] == state["revision"]
        assert load_presence_state() == before
        assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("after_generation", [False, True])
def test_new_sleep_supersedes_prepared_or_running_wake(tmp_path, after_generation):
    with patch("gamma.presence.presence_state_path", return_value=tmp_path / "state.json"):
        service = _service()
        audience = AudienceSelection(kind="unknown")
        prepared = service.prepare_wake(audience=audience)
        if after_generation:
            def respond(**kwargs):
                service.transition("sleep")
                return AssistantResponse(spoken_text="stale")
            service._conversation.respond_presence_wake.side_effect = respond
        else:
            service.transition("sleep")
        result = service.wake(audience=audience, prepared_state=prepared)
        assert result["wake"]["reason"] == "presence_changed"
        assert load_presence_state()["mode"] == "sleep"
        service._dispatcher.dispatch.assert_not_called()
        service._conversation.record_durable_output.assert_not_called()
        if not after_generation:
            service._conversation.respond_presence_wake.assert_not_called()


def test_ordinary_interaction_update_does_not_supersede_wake(tmp_path):
    with patch("gamma.presence.presence_state_path", return_value=tmp_path / "state.json"):
        service = _service()
        service._bus.has_eligible_listener.return_value = False
        def respond(**kwargs):
            state = load_presence_state()
            state["activity"]["session_id"] = "current"
            save_presence_state(state)
            return AssistantResponse(spoken_text="Hello")
        service._conversation.respond_presence_wake.side_effect = respond
        result = service.wake(audience=AudienceSelection(kind="unknown"))
        assert result["wake"]["status"] == "text_only"
        assert load_presence_state()["mode"] == "wake"
        service._dispatcher.dispatch.assert_called_once()


def test_wake_route_preserves_new_sleep_while_waiting_for_brain(tmp_path):
    from gamma.api import routes
    from gamma.brain.queue import BrainQueue
    from gamma.schemas.presence import PresenceWakeRequest
    entered, queued, release = (threading.Event() for _ in range(3))
    class ObservedQueue(BrainQueue):
        def submit(self, function):
            future = super().submit(function)
            if self.status()["pending"]:
                queued.set()
            return future
    queue = ObservedQueue()
    service = _service()
    with patch("gamma.presence.presence_state_path", return_value=tmp_path / "state.json"), patch.object(
        routes, "get_presence_service", return_value=service
    ), patch.object(routes, "get_brain_queue", return_value=queue), ThreadPoolExecutor(max_workers=1) as callers:
        try:
            queue.submit(lambda: (entered.set(), release.wait(5)))
            assert entered.wait(3)
            queued.clear()
            response = callers.submit(routes.shana_presence_wake, PresenceWakeRequest(audience=AudienceSelection(kind="unknown")))
            assert queued.wait(3)
            assert load_presence_state()["mode"] == "wake"
            service.transition("sleep")
            release.set()
            assert response.result(3)["wake"]["reason"] == "presence_changed"
            assert load_presence_state()["mode"] == "sleep"
            service._conversation.respond_presence_wake.assert_not_called()
        finally:
            release.set()
            queue.close(timeout=3)
