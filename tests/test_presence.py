from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from gamma.identity.profile import SpeakerProfile
from gamma.presence import PresenceService, apply_presence_to_stream_event, downgrade_stale_live_state, presence_state_for_mode
from gamma.schemas.presence import AudienceSelection
from gamma.schemas.response import AssistantResponse
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


def test_unknown_audience_wake_is_text_only_and_excludes_private_memory() -> None:
    conversation = Mock()
    conversation.respond_presence_wake.return_value = AssistantResponse(spoken_text="Hello there.")
    memory = Mock()
    bus = Mock()
    bus.has_eligible_listener.return_value = False
    bus.stats.return_value = {"subscriber_count": 0}
    dispatcher = Mock()
    dispatcher.dispatch.return_value.model_dump.return_value = {"records": []}
    identity = Mock()
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ):
        service = PresenceService(
            conversation=conversation,
            memory=memory,
            bus=bus,
            dispatcher=dispatcher,
            identity=identity,
        )
        result = service.wake(audience=AudienceSelection(kind="unknown"), session_id="wake-test")

    assert result["wake"]["status"] == "text_only"
    assert result["wake"]["reason"] == "no_audio_ready_monitor_listener"
    assert conversation.respond_presence_wake.call_args.kwargs["speaker"].memory_read_allowed is False
    memory.search_memories.assert_not_called()


def test_owner_wake_receives_bounded_selected_memory_and_recent_openings() -> None:
    owner = SpeakerProfile(name="Owner", trust="owner", is_owner=True)
    identity = Mock()
    identity.resolve.return_value = owner
    conversation = Mock()
    conversation.respond_presence_wake.return_value = AssistantResponse(spoken_text="Back to it?")
    memory = Mock()
    memory.search_memories.return_value = [Mock(summary="Continue the current project.")]
    bus = Mock()
    bus.has_eligible_listener.return_value = False
    dispatcher = Mock()
    dispatcher.dispatch.return_value.model_dump.return_value = {"records": []}
    with TemporaryDirectory() as temp_dir, patch(
        "gamma.presence.presence_state_path", return_value=Path(temp_dir) / "state.json"
    ):
        service = PresenceService(
            conversation=conversation,
            memory=memory,
            bus=bus,
            dispatcher=dispatcher,
            identity=identity,
        )
        service.wake(audience=AudienceSelection(kind="owner"), session_id="wake-test")
        service.wake(audience=AudienceSelection(kind="owner"), session_id="wake-test")

    context = conversation.respond_presence_wake.call_args.kwargs["wake_context"]
    assert "Continue the current project." in context
    assert "Recent Wake openings" in context
    assert len(context) <= 12_000
