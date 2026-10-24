from __future__ import annotations

import argparse
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conversation.service import ConversationService
from .config import settings
from .schemas.response import AssistantResponse
from .stream.actions import ActionPlanner
from .stream.brain import StreamBrain
from .stream.models import ActionPlan, StreamActor, StreamInputEvent, StreamOutputEvent, StreamTurnResult, output_context_from_input, output_events_from_response
from .schemas.voice import VoiceInputContext
from .voice.audio_understanding import AudioUnderstandingService, build_audio_prompt_context
from .voice.reply_chunking import split_reply_text
from .voice.reply_interruptibility import build_interruptibility
from .voice.reply_planner import ReplyPlanner
from .voice.reply_state import AssistantTurnState
from .voice.sentence_generator import SentenceGenerator
from .voice.stt import STTService
from .voice.tts import TTSService


def _utc_now() -> str:
    """Get UTC timestamp as ISO format string.
    
    Returns:
        str: UTC timestamp in ISO format.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(path: Path, payload: dict) -> None:
    """Write status payload to file.
    
    Args:
        path: Output path.
        payload: Status payload.
    """
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_output(path: Path, payload: dict) -> None:
    """Write output payload to file.
    
    Args:
        path: Output path.
        payload: Output payload.
    """
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_live_tts_audio_bytes(tts_result: Any) -> bytes:
    """Read TTS audio bytes.
    
    Args:
        tts_result: TTS result with audio_path.
    
    Returns:
        bytes: Audio bytes.
    """
    audio_path = Path(tts_result.audio_path)
    audio_bytes = audio_path.read_bytes()
    _cleanup_live_tts_artifact(audio_path)
    return audio_bytes


def _cleanup_live_tts_artifact(audio_path: Path) -> None:
    """Clean up live TTS artifact.
    
    Args:
        audio_path: Audio path to clean up.
    """
    try:
        resolved_audio_path = audio_path.resolve()
        resolved_output_dir = settings.audio_output_dir.resolve()
    except Exception:
        return
    if resolved_audio_path.parent != resolved_output_dir:
        return
    if not resolved_audio_path.name.startswith("tts-"):
        return
    try:
        resolved_audio_path.unlink(missing_ok=True)
    except Exception:
        pass


def _serialize_turn_state(turn_state: AssistantTurnState) -> dict[str, Any]:
    """Serialize turn state.
    
    Args:
        turn_state: Assistant turn state.
    
    Returns:
        dict[str, Any]: Serialized state.
    """
    return {
        "assistant_reply_so_far": turn_state.assistant_reply_so_far,
        "sentences": [
            {
                "sentence_index": sentence.sentence_index,
                "text": sentence.text,
                "status": sentence.status,
                "is_final": sentence.is_final,
                "timing_ms": sentence.timing_ms,
            }
            for sentence in turn_state.sentences
        ],
    }


def _normalize_estimated_sentence_count(planner_state: dict[str, Any]) -> int:
    """Normalize estimated sentence count.
    
    Args:
        planner_state: Planner state dict.
    
    Returns:
        int: Normalized sentence count (1-4).
    """
    try:
        estimated = int(planner_state.get("estimated_sentence_count", 2) or 2)
    except (TypeError, ValueError):
        estimated = 2
    return max(1, min(4, estimated))


def _should_use_brief_mode(transcript: str) -> bool:
    """Determine if brief mode should be used.
    
    Args:
        transcript: User transcript.
    
    Returns:
        bool: Whether to use brief mode.
    """
    words = transcript.split()
    if len(words) <= 8:
        return True
    lowered = transcript.lower()
    return any(
        phrase in lowered
        for phrase in [
            "how are you",
            "hello",
            "hi",
            "hey",
            "what's up",
            "whats up",
            "wait a minute",
            "give me a minute",
            "hold on",
        ]
    )


def _should_use_micro_mode(transcript: str) -> bool:
    """Determine if micro mode should be used.
    
    Args:
        transcript: User transcript.
    
    Returns:
        bool: Whether to use micro mode.
    """
    lowered = transcript.lower().strip()
    words = transcript.split()
    if len(words) <= 5:
        return True
    return any(
        phrase in lowered
        for phrase in [
            "how are you",
            "hello",
            "hi",
            "hey",
            "what's up",
            "whats up",
            "draw out the",
            "are you being sarcastic",
        ]
    )


def _live_chunk_budget(reply_text: str) -> int:
    """Get live chunk budget.
    
    Args:
        reply_text: Reply text.
    
    Returns:
        int: Number of chunks.
    """
    words = len(reply_text.split())
    if words >= 130:
        return 5
    if words >= 70:
        return 4
    return 3


def _serialize_stream_turn_result(stream_result: StreamTurnResult) -> dict[str, Any]:
    return {
        "trace_id": stream_result.trace_id,
        "input_event": stream_result.input_event.model_dump(),
        "decision": stream_result.decision.model_dump(),
        "action_plan": stream_result.action_plan.model_dump(),
        "safety_decision": stream_result.safety_decision,
        "assistant_response": stream_result.assistant_response.model_dump() if stream_result.assistant_response else None,
        "output_events": [event.model_dump() for event in stream_result.output_events],
        "timing_ms": stream_result.timing_ms,
    }


def _live_voice_stream_event(
    *,
    turn_id: str,
    transcript: str,
    session_id: str | None,
    response_mode: str,
    audio_context: dict[str, Any] | None = None,
) -> StreamInputEvent:
    metadata: dict[str, Any] = {"runtime": "live_voice_worker", "response_mode": response_mode}
    if audio_context:
        metadata["audio_context"] = audio_context
        metadata["voice_affect"] = audio_context
    event_kind = "mic_transcript"
    event_text = transcript
    if not transcript.strip() and audio_context:
        event_labels = [
            str(event.get("label") or "").strip()
            for event in audio_context.get("events", [])
            if isinstance(event, dict) and str(event.get("label") or "").strip()
        ]
        if event_labels:
            event_kind = "audio_event"
            event_text = "Detected audio event: " + ", ".join(event_labels[:5])
    return StreamInputEvent(
        event_id=turn_id,
        kind=event_kind,
        text=event_text,
        session_id=session_id,
        actor=StreamActor(source="local", platform_id="live_voice", display_name="Live Voice", roles=["voice"]),
        metadata=metadata,
    )


def _audio_prompt_context(audio_context: dict[str, Any] | None) -> str | None:
    if not audio_context:
        return None
    try:
        return build_audio_prompt_context(VoiceInputContext.model_validate(audio_context))
    except Exception:
        return None


def _stream_result_from_live_payload(
    *,
    stream_event: StreamInputEvent,
    stream_brain: StreamBrain,
    payload: dict[str, Any],
    response_emotion: str = "neutral",
    decision=None,
) -> StreamTurnResult:
    """Create stream result from live payload.
    
    Args:
        stream_event: Stream input event.
        stream_brain: Stream brain.
        payload: Live payload.
        response_emotion: Response emotion.
        decision: Decision (optional).
    
    Returns:
        StreamTurnResult: Stream turn result.
    """
    response = AssistantResponse(
        spoken_text=str(payload.get("reply_text", "") or ""),
        display_text=str(payload.get("display_text", payload.get("reply_text", "")) or ""),
        speech_text=str(payload.get("speech_text", payload.get("reply_text", "")) or ""),
        delivery_mode=str(payload.get("delivery_mode", "speech") or "speech"),
        emotion=response_emotion if response_emotion in {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"} else "neutral",
        voice_styles=payload.get("voice_styles", []) if isinstance(payload.get("voice_styles"), list) else [],
        motions=[],
        tool_calls=[],
        tool_results=[],
        memory_candidates=[],
        audio_content_type=payload.get("audio_content_type"),
        timing_ms=payload.get("timing_ms", {}) if isinstance(payload.get("timing_ms"), dict) else {},
    )
    decision = decision or stream_brain.decide(stream_event)
    output_events = output_events_from_response(input_event=stream_event, turn_id=str(payload.get("turn_id") or stream_event.event_id), response=response)
    output_context = output_context_from_input(stream_event)
    for chunk in payload.get("reply_chunks", []) or []:
        if not isinstance(chunk, dict):
            continue
        output_events.append(
            StreamOutputEvent(
                input_event_id=stream_event.event_id,
                turn_id=str(payload.get("turn_id") or stream_event.event_id),
                type="speech_chunk",
                payload={
                    "chunk_index": chunk.get("chunk_index"),
                    "text": chunk.get("text"),
                    "audio_content_type": chunk.get("audio_content_type"),
                    "timing_ms": chunk.get("timing_ms", {}),
                    "interruptible": chunk.get("interruptible", True),
                    "protect_ms": chunk.get("protect_ms", 0),
                    "is_final": chunk.get("is_final", False),
                    **output_context,
                },
            )
        )
    action_plan = ActionPlanner().plan_from_response(response)
    return StreamTurnResult(
        input_event=stream_event,
        decision=decision,
        action_plan=action_plan,
        assistant_response=response,
        output_events=output_events,
        timing_ms={"stream_brain_ms": 0.0},
    )


def _run_simple_chunked(
    *,
    started_at: float,
    args: argparse.Namespace,
    transcript: str,
    synthesize_speech: bool,
    conversation: ConversationService,
    stream_brain: StreamBrain,
    output_path: Path,
    status_path: Path,
    status_payload: dict[str, Any],
    response_mode: str,
    planner_state: dict[str, Any],
    turn_state: AssistantTurnState,
    audio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raise RuntimeError("Legacy live subprocess output is retired; use the core live API")


def _run_incremental_experimental(
    *,
    started_at: float,
    args: argparse.Namespace,
    transcript: str,
    synthesize_speech: bool,
    conversation: ConversationService,
    stream_brain: StreamBrain,
    sentence_generator: SentenceGenerator,
    output_path: Path,
    status_path: Path,
    status_payload: dict[str, Any],
    response_mode: str,
    planner_state: dict[str, Any],
    turn_state: AssistantTurnState,
    audio_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Sentence previews previously escaped before an owned decision committed.
    # Preserve the entry point while using the committed conversation pipeline.
    return _run_simple_chunked(
        started_at=started_at, args=args, transcript=transcript,
        synthesize_speech=synthesize_speech, conversation=conversation,
        stream_brain=stream_brain, audio_context=audio_context,
        output_path=output_path, status_path=status_path,
        status_payload=status_payload, response_mode="simple_chunked",
        planner_state=planner_state, turn_state=turn_state,
    )

def main() -> int:
    raise RuntimeError("Legacy live subprocess output is retired; use the core live API")
    parser = argparse.ArgumentParser()
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--audio-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--synthesize-speech", choices={"true", "false"}, default="true")
    parser.add_argument("--response-mode", choices={"simple_chunked", "incremental_experimental"})
    args = parser.parse_args()

    audio_path = Path(args.audio_path)
    output_path = Path(args.output_path)
    status_path = Path(args.status_path)
    synthesize_speech = args.synthesize_speech == "true"
    response_mode = args.response_mode or str(settings.live_voice_response_mode or "simple_chunked").strip().lower()
    if response_mode not in {"simple_chunked", "incremental_experimental"}:
        response_mode = "simple_chunked"

    stt = STTService()
    audio_understanding = AudioUnderstandingService()
    conversation = ConversationService()
    stream_brain = StreamBrain(conversation=conversation)
    planner = ReplyPlanner(conversation) if response_mode == "incremental_experimental" else None
    sentence_generator = SentenceGenerator(conversation) if response_mode == "incremental_experimental" else None
    started_at = time.perf_counter()
    status_payload = {
        "turn_id": args.turn_id,
        "status": "running",
        "session_id": args.session_id,
        "synthesize_speech": synthesize_speech,
        "response_mode": response_mode,
        "started_at": _utc_now(),
    }
    _write_status(status_path, status_payload)

    try:
        stt_started = time.perf_counter()
        transcript = stt.transcribe_audio(str(audio_path)).strip()
        stt_ms = round((time.perf_counter() - stt_started) * 1000, 1)
        audio_context = audio_understanding.analyze_path(audio_path, transcript=transcript)
        audio_context_payload = audio_context.model_dump()
        if not transcript and not audio_context.events:
            raise ValueError("transcription came back empty and no audio events were detected")

        status_payload["status"] = "running"
        status_payload["transcript"] = transcript
        status_payload["audio_context"] = audio_context_payload
        status_payload["voice_affect"] = audio_context_payload
        _write_status(status_path, status_payload)
        turn_state = AssistantTurnState(
            turn_id=args.turn_id,
            session_id=args.session_id,
            user_text=transcript,
            response_mode=response_mode,
            status="planned",
        )
        if response_mode == "incremental_experimental" and planner is not None and sentence_generator is not None:
            planner_state = planner.plan(user_text=transcript, session_id=args.session_id)
        else:
            planner_state = {
                "intent": "answer the user directly",
                "tone": "concise spoken response",
                "key_points": [transcript[:120]],
                "estimated_sentence_count": 2,
                "stop_condition": "stop when the answer is complete",
                "planner_ms": 0.0,
            }
        turn_state.planner_state = planner_state
        turn_state.status = "generating"
        if response_mode == "incremental_experimental" and sentence_generator is not None:
            payload = _run_incremental_experimental(
                started_at=started_at,
                args=args,
                transcript=transcript,
                synthesize_speech=synthesize_speech,
                conversation=conversation,
                stream_brain=stream_brain,
                sentence_generator=sentence_generator,
                audio_context=audio_context_payload,
                output_path=output_path,
                status_path=status_path,
                status_payload=status_payload,
                response_mode=response_mode,
                planner_state=planner_state,
                turn_state=turn_state,
            )
        else:
            payload = _run_simple_chunked(
                started_at=started_at,
                args=args,
                transcript=transcript,
                synthesize_speech=synthesize_speech,
                conversation=conversation,
                stream_brain=stream_brain,
                audio_context=audio_context_payload,
                output_path=output_path,
                status_path=status_path,
                status_payload=status_payload,
                response_mode=response_mode,
                planner_state=planner_state,
                turn_state=turn_state,
            )

        payload.setdefault("timing_ms", {})
        payload["timing_ms"]["stt_ms"] = stt_ms
        payload["timing_ms"]["audio_understanding_ms"] = audio_context.timing_ms.get("total_ms", 0.0)
        payload["timing_ms"]["voice_affect_ms"] = audio_context.timing_ms.get("prosody_ms", 0.0)
        payload["timing_ms"]["planner_ms"] = float(planner_state.get("planner_ms", 0.0) or 0.0)

        payload["status"] = "completed"
        payload["timing_ms"]["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        _write_output(output_path, payload)
        status_payload.update(
            {
                "status": "completed",
                "completed_at": _utc_now(),
            }
        )
        _write_status(status_path, status_payload)
        return 0
    except KeyboardInterrupt:
        status_payload.update({"status": "cancelled", "cancelled_at": _utc_now(), "cancel_reason": "worker-interrupted"})
        _write_status(status_path, status_payload)
        return 130
    except Exception as exc:
        status_payload.update({"status": "failed", "completed_at": _utc_now(), "error": str(exc)})
        _write_status(status_path, status_payload)
        output_path.write_text(
            json.dumps({"turn_id": args.turn_id, "status": "failed", "error": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
