"""Filtering stream output events by target policy and timing subtitles."""
from __future__ import annotations

from .event_rules import _estimate_speech_seconds, _is_public_stream_event
from .models import StreamInputEvent, StreamOutputEvent


DEFAULT_SUBTITLE_HOLD_MS = 1200


def _filter_stream_output_events(event: StreamInputEvent, output_events: list[StreamOutputEvent]) -> list[StreamOutputEvent]:
    if event.actor.source != "twitch":
        return output_events
    controls = event.metadata.get("twitch_controls")
    if not isinstance(controls, dict):
        controls = {}
    subtitles_enabled = bool(controls.get("subtitles_enabled", True))
    voice_enabled = bool(controls.get("voice_enabled", False))
    filtered: list[StreamOutputEvent] = []
    for output_event in output_events:
        if output_event.type == "subtitle_line" and not subtitles_enabled:
            continue
        if output_event.type in {"speech_started", "speech_chunk", "speech_ended"} and not voice_enabled:
            continue
        if voice_enabled:
            filtered.append(output_event)
            continue
        payload = {
            key: value
            for key, value in output_event.payload.items()
            if key not in {"audio", "audio_base64", "audio_content_type", "audio_path", "audio_url"}
        }
        filtered.append(output_event.model_copy(update={"payload": payload}))
    return filtered


def _apply_output_target_policy(event: StreamInputEvent, output_events: list[StreamOutputEvent]) -> list[StreamOutputEvent]:
    target_policy = str(event.metadata.get("output_target_policy") or "").strip().lower()
    if target_policy not in {"stream_public", "dashboard_monitor", "discord_call"}:
        return output_events
    targeted: list[StreamOutputEvent] = []
    for output_event in output_events:
        payload = dict(output_event.payload)
        payload["target_policy"] = target_policy
        targeted.append(output_event.model_copy(update={"payload": payload}))
    return targeted


def _mark_filtered_output_events(output_events: list[StreamOutputEvent], safety_decision: dict) -> list[StreamOutputEvent]:
    marked: list[StreamOutputEvent] = []
    for output_event in output_events:
        payload = dict(output_event.payload)
        payload["filtered"] = True
        payload["safety_action"] = safety_decision.get("action")
        marked.append(output_event.model_copy(update={"payload": payload}))
    return marked


def _apply_estimated_subtitle_timing(event: StreamInputEvent, output_events: list[StreamOutputEvent]) -> list[StreamOutputEvent]:
    if not _is_public_stream_event(event):
        return output_events
    if not any(output_event.type == "speech_started" for output_event in output_events):
        return output_events
    timed_events: list[StreamOutputEvent] = []
    for output_event in output_events:
        if output_event.type != "subtitle_line" or output_event.payload.get("filtered"):
            timed_events.append(output_event)
            continue
        timed_events.extend(_timed_subtitle_events(output_event))
    return timed_events


def _timed_subtitle_events(output_event: StreamOutputEvent) -> list[StreamOutputEvent]:
    text = str(output_event.payload.get("text") or "").strip()
    words = text.split()
    if len(words) <= 4:
        payload = {
            **dict(output_event.payload),
            "timing": "estimated",
            "subtitle_sequence": 0,
            "estimated_start_ms": 0,
            "estimated_end_ms": int(_estimate_speech_seconds(text) * 1000),
            "hold_ms": DEFAULT_SUBTITLE_HOLD_MS,
            "is_final": True,
        }
        return [output_event.model_copy(update={"payload": payload})]
    total_ms = max(1, int(_estimate_speech_seconds(text) * 1000))
    chunk_size = 4
    chunks = [words[index:index + chunk_size] for index in range(0, len(words), chunk_size)]
    events: list[StreamOutputEvent] = []
    for index, chunk in enumerate(chunks):
        end_word = min((index + 1) * chunk_size, len(words))
        start_word = index * chunk_size
        is_final = index == len(chunks) - 1
        payload = {
            **dict(output_event.payload),
            "text": " ".join(words[:end_word]),
            "timing": "estimated",
            "subtitle_sequence": index,
            "estimated_start_ms": int(total_ms * start_word / len(words)),
            "estimated_end_ms": int(total_ms * end_word / len(words)),
            "hold_ms": DEFAULT_SUBTITLE_HOLD_MS if is_final else 0,
            "is_final": is_final,
        }
        events.append(
            StreamOutputEvent(
                input_event_id=output_event.input_event_id,
                turn_id=output_event.turn_id,
                type="subtitle_line",
                payload=payload,
            )
        )
    return events
