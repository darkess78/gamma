from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import anyio

from gamma.config import settings
from gamma import main as shana_main
from gamma.presence import presence_state_for_mode, save_presence_state
from gamma.proactive import ProactiveScheduler


def _eligible_bus(*, muted: bool = False) -> Mock:
    bus = Mock()
    bus.stats.return_value = {"muted_targets": ["dashboard_monitor"] if muted else []}
    bus.has_eligible_listener.return_value = True
    bus.recent_turns.return_value = []
    return bus


def test_scheduler_is_disabled_by_default_before_any_model_call() -> None:
    brain = Mock()
    bus = _eligible_bus()
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ), patch.object(settings, "proactive_idle_enabled", False):
        save_presence_state(presence_state_for_mode("wake"))
        result = ProactiveScheduler(stream_brain=brain, bus=bus).evaluate_once()

    assert result["reason"] == "proactive_idle_disabled"
    brain.handle_event.assert_not_called()


def test_shana_lifespan_starts_and_stops_scheduler() -> None:
    from unittest.mock import AsyncMock

    scheduler = Mock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()

    async def run() -> None:
        with patch("gamma.main.get_proactive_scheduler", return_value=scheduler):
            async with shana_main.lifespan(shana_main.app):
                pass

    anyio.run(run)
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()


def test_scheduler_emits_local_event_without_live_voice_websocket() -> None:
    brain = Mock()
    brain.handle_event.return_value = Mock(trace_id="trace-local")
    bus = _eligible_bus()
    now = datetime.now(timezone.utc)
    state = presence_state_for_mode("wake")
    state["lifecycle"]["last_interaction_at"] = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ), patch.object(settings, "proactive_idle_enabled", True), patch.object(
        settings, "proactive_idle_min_silence_seconds", 5
    ), patch.object(settings, "proactive_idle_target_silence_seconds", 5), patch.object(
        settings, "proactive_idle_speech_enabled", False
    ):
        save_presence_state(state)
        result = ProactiveScheduler(stream_brain=brain, bus=bus).evaluate_once(now=now)

    assert result["status"] == "emitted"
    event = brain.handle_event.call_args.args[0]
    assert event.metadata["output_target_policy"] == "dashboard_monitor"
    assert event.metadata["privacy_scope"] == "local_generic"
    assert event.metadata["tools_allowed"] is False
    assert brain.handle_event.call_args.kwargs["fast_mode"] is False


def test_scheduler_sleep_break_and_operator_mute_suppress_output() -> None:
    now = datetime.now(timezone.utc)
    for mode, expected in (("sleep", "presence_sleep"), ("break", "presence_break")):
        brain = Mock()
        with TemporaryDirectory() as temp_dir, patch(
            "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
        ), patch.object(settings, "proactive_idle_enabled", True):
            save_presence_state(presence_state_for_mode(mode))
            result = ProactiveScheduler(stream_brain=brain, bus=_eligible_bus()).evaluate_once(now=now)
        assert result["reason"] == expected
        brain.handle_event.assert_not_called()

    brain = Mock()
    state = presence_state_for_mode("wake")
    state["lifecycle"]["last_interaction_at"] = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ), patch.object(settings, "proactive_idle_enabled", True):
        save_presence_state(state)
        result = ProactiveScheduler(stream_brain=brain, bus=_eligible_bus(muted=True)).evaluate_once(now=now)
    assert result["reason"] == "operator_target_muted"
    brain.handle_event.assert_not_called()


def test_go_live_scheduler_marks_event_public_and_uses_stream_target() -> None:
    brain = Mock()
    brain.handle_event.return_value = Mock(trace_id="trace-public")
    bus = _eligible_bus()
    bus.stats.return_value = {"muted_targets": []}
    now = datetime.now(timezone.utc)
    state = presence_state_for_mode("go_live", confirmed_live_at=now.isoformat().replace("+00:00", "Z"))
    state["lifecycle"]["last_interaction_at"] = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ), patch.object(settings, "proactive_idle_enabled", True), patch.object(
        settings, "proactive_idle_min_silence_seconds", 5
    ), patch.object(settings, "proactive_idle_target_silence_seconds", 5):
        save_presence_state(state)
        result = ProactiveScheduler(stream_brain=brain, bus=bus).evaluate_once(now=now)

    assert result["target"] == "stream_public"
    event = brain.handle_event.call_args.args[0]
    assert event.metadata["public_output"] is True
    assert event.metadata["privacy_scope"] == "public"
