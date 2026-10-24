"""Validate approved events at each external consumer, then claim the attempt."""
from __future__ import annotations

from .authority import DeliveryDenied
from .digest import fingerprint
from .playback import get_playback_ledger

CONTROL_TYPES = {"subtitle_clear", "output_cleared", "target_mute_changed"}
CONTENT_TYPES = {"subtitle_update", "expression_set", "motion_trigger", "speech_chunk_ready", "speech_started", "speech_ended", "mouth_level"}


def is_output(event):
    return event.type in CONTENT_TYPES


def validate_event(event, *, ledger=None, bus=None, replay=False):
    if not is_output(event):
        return None
    if replay:
        raise DeliveryDenied("output_replay_forbidden")
    ledger = ledger or get_playback_ledger()
    grant = event.payload.get("delivery_grant")
    if not isinstance(grant, str) or not grant:
        raise DeliveryDenied("output_grant_required")
    response, context = ledger.response_for_grant(grant)
    if event.target_policy != context["target"]:
        raise DeliveryDenied("output_target_mismatch")
    ledger.check(grant, response, **context)
    payload = event.payload
    text = payload.get("text")
    if text and text not in {response.display_text, response.speech_text}:
        raise DeliveryDenied("output_text_mismatch")
    if event.type == "expression_set" and payload.get("expression") != response.emotion:
        raise DeliveryDenied("output_expression_mismatch")
    if event.type == "motion_trigger" and payload.get("motion") not in response.motions:
        raise DeliveryDenied("output_motion_mismatch")
    if event.type == "mouth_level":
        raise DeliveryDenied("mouth_transport_not_authorized")
    if any(payload.get(key) for key in ("audio_base64", "audio_path")):
        raise DeliveryDenied("unbound_audio_reference")
    if payload.get("audio_url"):
        if payload["audio_url"] != response.tts_metadata.get("delivery_audio_url"):
            raise DeliveryDenied("output_audio_mismatch")
        ledger.read_audio(grant, target=event.target_policy)
    from ..presence import load_presence_state
    state = load_presence_state(downgrade_stale_live=True)
    outputs = state.get("outputs") or {}
    if state.get("mode") not in {"wake", "go_live"}:
        raise DeliveryDenied("presence_output_suppressed")
    if event.target_policy == "stream_public":
        if state.get("mode") != "go_live" or state.get("requires_confirmation") or not outputs.get("stream_public"):
            raise DeliveryDenied("presence_public_output_suppressed")
    elif event.target_policy == "dashboard_monitor":
        if not outputs.get("dashboard_monitor"):
            raise DeliveryDenied("presence_monitor_output_suppressed")
    else:
        raise DeliveryDenied("output_target_has_no_execution_adapter")
    if payload.get("audio_url") and not outputs.get("voice"):
        raise DeliveryDenied("presence_voice_suppressed")
    if bus is None:
        from ..performer.bus import get_performer_event_bus
        bus = get_performer_event_bus()
    if event.target_policy in bus.stats()["muted_targets"]:
        raise DeliveryDenied("output_target_muted")
    return ledger, grant, response, context


def claim_event(event, consumer, *, ledger=None, bus=None, replay=False):
    checked = validate_event(event, ledger=ledger, bus=bus, replay=replay)
    if checked is None:
        return None
    ledger, grant, response, context = checked
    # UUIDs change when a producer reconstructs its events after an uncertain send.
    # The semantic key identifies the one approved effect for this consumer.
    # These grants authorize one whole response, not caller-defined chunks.
    # Irrelevant payload fields must never create another claim for the same
    # external effect (for example chunk_index on an expression hotkey).
    effect = None
    if event.type == "expression_set":
        effect = response.emotion
    elif event.type == "motion_trigger":
        effect = event.payload["motion"]
    elif event.type == "subtitle_update":
        effect = event.payload.get("text") or ""
    semantic = fingerprint({"type": event.type, "effect": effect})
    attempt = ledger.claim_dispatch(grant, response, consumer=f"{consumer}:{semantic}", **context)
    return ledger, attempt


def finish_event(claim, *, succeeded):
    if claim is not None:
        claim[0].finish_dispatch(claim[1], succeeded=succeeded)
