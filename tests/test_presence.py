from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gamma.presence import apply_presence_to_stream_event, downgrade_stale_live_state, presence_state_for_mode
from gamma.stream.models import StreamActor, StreamInputEvent


def test_stale_go_live_presence_downgrades_after_backend_restart() -> None:
    confirmed_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    state = presence_state_for_mode("go_live", confirmed_live_at=confirmed_at)

    downgraded = downgrade_stale_live_state(state, booted_at=datetime.now(timezone.utc))

    assert downgraded["mode"] == "wake"
    assert downgraded["desired_mode"] == "go_live"
    assert downgraded["requires_confirmation"] is True
    assert downgraded["outputs"]["stream_public"] is False
    assert downgraded["outputs"]["voice"] is False


def test_fresh_go_live_presence_enables_public_stream_event_output() -> None:
    state = presence_state_for_mode("go_live", confirmed_live_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    event = StreamInputEvent(
        kind="chat_message",
        text="Shana hello",
        actor=StreamActor(source="twitch", platform_id="u1", display_name="Viewer"),
    )

    updated, synthesize_speech = apply_presence_to_stream_event(event, synthesize_speech=False, state=state)

    assert synthesize_speech is True
    assert updated.metadata["presence_mode"] == "go_live"
    assert updated.metadata["output_target_policy"] == "stream_public"
    assert updated.metadata["twitch_controls"]["dry_run"] is False
    assert updated.metadata["twitch_controls"]["voice_enabled"] is True


def test_sleep_presence_suppresses_public_stream_event_output() -> None:
    state = presence_state_for_mode("sleep")
    event = StreamInputEvent(
        kind="chat_message",
        text="Shana hello",
        actor=StreamActor(source="twitch", platform_id="u1", display_name="Viewer"),
    )

    updated, synthesize_speech = apply_presence_to_stream_event(event, synthesize_speech=True, state=state)

    assert synthesize_speech is False
    assert updated.metadata["presence_mode"] == "sleep"
    assert updated.metadata["presence_suppressed"] is True
    assert updated.metadata["twitch_controls"]["dry_run"] is True
    assert updated.metadata["twitch_controls"]["voice_enabled"] is False
