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
from .stream.models import ActionPlan, StreamActor, StreamInputEvent, StreamOutputEvent, StreamTurnResult, output_events_from_response
from .voice.affect import VoiceAffectAnalyzer
from .voice.reply_chunking import split_reply_text
from .voice.reply_interruptibility import build_interruptibility
from .voice.reply_planner import ReplyPlanner
from .voice.reply_state import AssistantTurnState, SentenceState
from .voice.sentence_generator import SentenceGenerator
from .voice.stt import STTService
from .voice.expressive_text import strip_hidden_style_tags
from .voice.tts import TTSService
from .safety.speech_filter import SpeechSafetyFilter


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_status(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_output(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_live_tts_audio_bytes(tts_result: Any) -> bytes:
    audio_path = Path(tts_result.audio_path)
    audio_bytes = audio_path.read_bytes()
    _cleanup_live_tts_artifact(audio_path)
    return audio_bytes


def _cleanup_live_tts_artifact(audio_path: Path) -> None:
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
    try:
        estimated = int(planner_state.get("estimated_sentence_count", 2) or 2)
    except (TypeError, ValueError):
        estimated = 2
    return max(1, min(4, estimated))


def _append_memory_fast_path(conversation: ConversationService, *, user_text: str, reply_text: str, session_id: str | None) -> None:
    try:
        candidates = conversation._build_memory_candidates(user_text=user_text, reply_text=reply_text)
        if candidates:
            conversation._memory.persist_candidates(candidates, session_id=session_id)
    except Exception:
        pass


def _should_use_brief_mode(transcript: str) -> bool:
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
    voice_affect: dict[str, Any] | None = None,
) -> StreamInputEvent:
    metadata: dict[str, Any] = {"runtime": "live_voice_worker", "response_mode": response_mode}
    if voice_affect:
        metadata["voice_affect"] = voice_affect
    return StreamInputEvent(
        event_id=turn_id,
        kind="mic_transcript",
        text=transcript,
        session_id=session_id,
        actor=StreamActor(source="local", platform_id="live_voice", display_name="Live Voice", roles=["voice"]),
        metadata=metadata,
    )


def _stream_result_from_live_payload(
    *,
    stream_event: StreamInputEvent,
    stream_brain: StreamBrain,
    payload: dict[str, Any],
    response_emotion: str = "neutral",
    decision=None,
) -> StreamTurnResult:
    response = AssistantResponse(
        spoken_text=str(payload.get("reply_text", "") or ""),
        emotion=response_emotion if response_emotion in {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"} else "neutral",
        motions=[],
        tool_calls=[],
        tool_results=[],
        memory_candidates=[],
        audio_content_type=payload.get("audio_content_type"),
        timing_ms=payload.get("timing_ms", {}) if isinstance(payload.get("timing_ms"), dict) else {},
    )
    decision = decision or stream_brain.decide(stream_event)
    output_events = output_events_from_response(input_event=stream_event, turn_id=str(payload.get("turn_id") or stream_event.event_id), response=response)
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
    voice_affect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversation_started = time.perf_counter()
    stream_event = _live_voice_stream_event(
        turn_id=args.turn_id,
        transcript=transcript,
        session_id=args.session_id,
        response_mode=response_mode,
        voice_affect=voice_affect,
    )
    stream_decision = stream_brain.decide(stream_event)
    if stream_decision.should_call_conversation:
        response = conversation.respond(
            transcript,
            session_id=args.session_id,
            synthesize_speech=False,
            fast_mode=True,
            brief_mode=_should_use_brief_mode(transcript),
            micro_mode=_should_use_micro_mode(transcript),
        )
    else:
        response = None
    conversation_ms = round((time.perf_counter() - conversation_started) * 1000, 1)
    if response is None:
        stream_result = StreamTurnResult(
            input_event=stream_event,
            decision=stream_decision,
            action_plan=ActionPlan(),
            assistant_response=None,
            output_events=[],
            safety_decision={},
            timing_ms={"stream_brain_ms": 0.0},
        )
        stream_brain.record_result(stream_result)
        payload = {
            "turn_id": args.turn_id,
            "status": "completed",
            "transcript": transcript,
            "reply_text": "",
            "reply_chunks": [],
            "response_mode": response_mode,
            "voice_affect": voice_affect or {},
            "stream": _serialize_stream_turn_result(stream_result),
            "planner_state": planner_state,
            "incremental_preview": _serialize_turn_state(turn_state),
            "audio_content_type": None,
            "audio_base64": None,
            "timing_ms": {
                "conversation_ms": conversation_ms,
                "chunk_count": 0,
                "tts_ms": 0.0,
            },
        }
        _write_output(output_path, payload)
        return payload

    # Keep the new stream-brain trace around the old direct live conversation call.
    # This preserves the pre-router live latency profile while the control-plane
    # event boundary continues to collect replayable decisions and outputs.
    stream_result = StreamTurnResult(
        input_event=stream_event,
        decision=stream_decision,
        action_plan=ActionPlanner().plan_from_response(response),
        assistant_response=response,
        output_events=output_events_from_response(input_event=stream_event, turn_id=args.turn_id, response=response),
        safety_decision={},
        timing_ms={"stream_brain_ms": 0.0},
    )
    stream_brain.record_result(stream_result)

    payload: dict[str, Any] = {
        "turn_id": args.turn_id,
        "status": "running",
        "transcript": transcript,
        "reply_text": response.spoken_text,
        "reply_chunks": [],
        "response_mode": response_mode,
        "voice_affect": voice_affect or {},
        "stream": _serialize_stream_turn_result(stream_result),
        "planner_state": planner_state,
        "incremental_preview": _serialize_turn_state(turn_state),
        "audio_content_type": response.audio_content_type,
        "audio_base64": None,
        "timing_ms": {
            "conversation_ms": conversation_ms,
            **response.timing_ms,
        },
    }

    if synthesize_speech:
        chunks = split_reply_text(response.spoken_text, max_chunks=_live_chunk_budget(response.spoken_text))
        chunk_policies = build_interruptibility(chunks)
        payload["timing_ms"]["chunk_count"] = len(chunks)
        if chunks:
            tts = TTSService()
            status_payload["status"] = "speaking"
            _write_status(status_path, status_payload)
            chunk_timings: list[float] = []
            for index, chunk_text in enumerate(chunks, start=1):
                policy = chunk_policies[index - 1] if index - 1 < len(chunk_policies) else {}
                chunk_started = time.perf_counter()
                tts_result = tts.synthesize(chunk_text, emotion=response.emotion)
                chunk_tts_ms = round((time.perf_counter() - chunk_started) * 1000, 1)
                chunk_timings.append(chunk_tts_ms)
                audio_bytes = _read_live_tts_audio_bytes(tts_result)
                chunk_payload = {
                    "chunk_index": index,
                    "text": chunk_text,
                    "audio_content_type": tts_result.content_type,
                    "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                    "timing_ms": {
                        "tts_ms": chunk_tts_ms,
                        "audio_bytes": len(audio_bytes),
                        "text_chars": len(chunk_text),
                        "text_words": len(chunk_text.split()),
                    },
                    "interruptible": bool(policy.get("interruptible", True)),
                    "protect_ms": int(policy.get("protect_ms", 0) or 0),
                    "is_final": index == len(chunks),
                }
                payload["reply_chunks"].append(chunk_payload)
                if index == 1:
                    payload["audio_content_type"] = tts_result.content_type
                    payload["audio_base64"] = chunk_payload["audio_base64"]
                    payload["timing_ms"]["tts_ms"] = chunk_tts_ms
                    payload["timing_ms"]["time_to_first_chunk_audio_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
                payload["timing_ms"][f"chunk_{index}_tts_ms"] = chunk_tts_ms
                payload["timing_ms"][f"chunk_{index}_audio_bytes"] = len(audio_bytes)
                payload["timing_ms"][f"chunk_{index}_text_chars"] = len(chunk_text)
                payload["timing_ms"][f"chunk_{index}_text_words"] = len(chunk_text.split())
                payload["status"] = "speaking" if index < len(chunks) else "completed"
                _write_output(output_path, payload)
            payload["timing_ms"]["tts_ms"] = round(sum(chunk_timings), 1)
        else:
            payload["timing_ms"]["chunk_count"] = 0
            payload["timing_ms"]["tts_ms"] = 0.0
    else:
        payload["timing_ms"]["chunk_count"] = 0
        payload["timing_ms"]["tts_ms"] = 0.0

    return payload


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
    voice_affect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    speech_filter = SpeechSafetyFilter(settings.speech_filter_level)
    stream_event = _live_voice_stream_event(
        turn_id=args.turn_id,
        transcript=transcript,
        session_id=args.session_id,
        response_mode=response_mode,
        voice_affect=voice_affect,
    )
    stream_decision = stream_brain.decide(stream_event)
    payload: dict[str, Any] = {
        "turn_id": args.turn_id,
        "status": "running",
        "transcript": transcript,
        "reply_text": "",
        "reply_chunks": [],
        "response_mode": response_mode,
        "voice_affect": voice_affect or {},
        "stream": {
            "input_event": stream_event.model_dump(),
            "decision": stream_decision.model_dump(),
            "action_plan": ActionPlan().model_dump(),
            "safety_decision": {},
            "output_events": [],
            "timing_ms": {"stream_brain_ms": 0.0},
        },
        "planner_state": planner_state,
        "incremental_preview": _serialize_turn_state(turn_state),
        "audio_content_type": None,
        "audio_base64": None,
        "timing_ms": {
            "conversation_ms": 0.0,
            "tts_ms": 0.0,
            "chunk_count": 0,
            "planner_ms": float(planner_state.get("planner_ms", 0.0) or 0.0),
        },
    }
    if not stream_decision.should_call_conversation:
        stream_result = StreamTurnResult(
            input_event=stream_event,
            decision=stream_decision,
            action_plan=ActionPlan(),
            assistant_response=None,
            output_events=[],
            timing_ms={"stream_brain_ms": 0.0},
        )
        stream_brain.record_result(stream_result)
        payload["stream"] = _serialize_stream_turn_result(stream_result)
        payload["status"] = "completed"
        return payload

    max_sentences = _normalize_estimated_sentence_count(planner_state)
    total_generation_ms = 0.0
    total_tts_ms = 0.0
    response_emotion = "neutral"
    tts = TTSService() if synthesize_speech else None

    for sentence_index in range(1, max_sentences + 1):
        turn_state.status = "generating"
        next_sentence = sentence_generator.generate_next_sentence(
            user_text=transcript,
            session_id=args.session_id,
            planner_state=planner_state,
            assistant_reply_so_far=turn_state.assistant_reply_so_far,
            sentence_index=sentence_index,
        )
        generation_ms = float(next_sentence.get("generation_ms", 0.0) or 0.0)
        total_generation_ms += generation_ms
        sentence_text = str(next_sentence.get("sentence_text", "") or "").strip()
        is_final = bool(next_sentence.get("is_final", False))

        if not sentence_text:
            if sentence_index == 1:
                break
            if is_final:
                break
            continue

        expressive = strip_hidden_style_tags(sentence_text)
        if expressive.emotion:
            response_emotion = expressive.emotion
        sentence_text = speech_filter.apply(expressive.clean_text).spoken_text

        sentence_state = SentenceState(
            sentence_index=sentence_index,
            text=sentence_text,
            status="generated",
            is_final=is_final,
            timing_ms={"generation_ms": generation_ms},
        )
        turn_state.append_sentence(sentence_state)
        payload["reply_text"] = turn_state.assistant_reply_so_far
        payload["incremental_preview"] = _serialize_turn_state(turn_state)
        payload["timing_ms"]["conversation_ms"] = round(float(payload["timing_ms"]["planner_ms"]) + total_generation_ms, 1)
        payload["timing_ms"][f"sentence_{sentence_index}_generation_ms"] = generation_ms

        if synthesize_speech and tts is not None:
            turn_state.status = "synthesizing"
            sentence_state.status = "synthesizing"
            if sentence_index == 1:
                status_payload["status"] = "speaking"
                _write_status(status_path, status_payload)
            chunk_started = time.perf_counter()
            tts_result = tts.synthesize(sentence_text, emotion=expressive.emotion)
            chunk_tts_ms = round((time.perf_counter() - chunk_started) * 1000, 1)
            total_tts_ms += chunk_tts_ms
            audio_bytes = _read_live_tts_audio_bytes(tts_result)
            policy = build_interruptibility([sentence_text])[0] if sentence_index == 1 else {"interruptible": True, "protect_ms": 0}
            sentence_state.status = "ready"
            sentence_state.timing_ms["tts_ms"] = chunk_tts_ms
            chunk_payload = {
                "chunk_index": sentence_index,
                "text": sentence_text,
                "audio_content_type": tts_result.content_type,
                "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                "timing_ms": {
                    "tts_ms": chunk_tts_ms,
                    "generation_ms": generation_ms,
                    "audio_bytes": len(audio_bytes),
                    "text_chars": len(sentence_text),
                    "text_words": len(sentence_text.split()),
                },
                "interruptible": bool(policy.get("interruptible", True)),
                "protect_ms": int(policy.get("protect_ms", 0) or 0),
                "is_final": is_final,
            }
            payload["reply_chunks"].append(chunk_payload)
            payload["timing_ms"][f"chunk_{sentence_index}_tts_ms"] = chunk_tts_ms
            payload["timing_ms"][f"chunk_{sentence_index}_audio_bytes"] = len(audio_bytes)
            payload["timing_ms"][f"chunk_{sentence_index}_text_chars"] = len(sentence_text)
            payload["timing_ms"][f"chunk_{sentence_index}_text_words"] = len(sentence_text.split())
            payload["timing_ms"]["tts_ms"] = round(total_tts_ms, 1)
            payload["timing_ms"]["chunk_count"] = len(payload["reply_chunks"])
            payload["status"] = "speaking"
            payload["incremental_preview"] = _serialize_turn_state(turn_state)
            if sentence_index == 1:
                payload["audio_content_type"] = tts_result.content_type
                payload["audio_base64"] = chunk_payload["audio_base64"]
                payload["timing_ms"]["time_to_first_chunk_audio_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
            _write_output(output_path, payload)

        if is_final:
            break

    if not turn_state.sentences:
        fallback_state = SentenceState(
            sentence_index=1,
            text="",
            status="failed",
            is_final=True,
            timing_ms={},
        )
        turn_state.sentences.append(fallback_state)
        return _run_simple_chunked(
            started_at=started_at,
            args=args,
            transcript=transcript,
            synthesize_speech=synthesize_speech,
            conversation=conversation,
            stream_brain=stream_brain,
            voice_affect=voice_affect,
            output_path=output_path,
            status_path=status_path,
            status_payload=status_payload,
            response_mode="simple_chunked",
            planner_state=planner_state,
            turn_state=turn_state,
        )

    turn_state.status = "completed"
    payload["reply_text"] = turn_state.assistant_reply_so_far
    payload["incremental_preview"] = _serialize_turn_state(turn_state)
    payload["timing_ms"]["conversation_ms"] = round(float(payload["timing_ms"]["planner_ms"]) + total_generation_ms, 1)
    payload["timing_ms"]["tts_ms"] = round(total_tts_ms, 1)
    payload["timing_ms"]["chunk_count"] = len(payload["reply_chunks"])
    stream_result = _stream_result_from_live_payload(
        stream_event=stream_event,
        stream_brain=stream_brain,
        payload=payload,
        response_emotion=response_emotion,
        decision=stream_decision,
    )
    stream_brain.record_result(stream_result)
    payload["stream"] = _serialize_stream_turn_result(stream_result)
    _append_memory_fast_path(conversation, user_text=transcript, reply_text=payload["reply_text"], session_id=args.session_id)
    return payload


def main() -> int:
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
        if not transcript:
            raise ValueError("transcription came back empty")
        affect_started = time.perf_counter()
        voice_affect = VoiceAffectAnalyzer().analyze_path(audio_path, transcript=transcript).as_payload()
        affect_ms = round((time.perf_counter() - affect_started) * 1000, 1)

        status_payload["status"] = "running"
        status_payload["transcript"] = transcript
        status_payload["voice_affect"] = voice_affect
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
                voice_affect=voice_affect,
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
                voice_affect=voice_affect,
                output_path=output_path,
                status_path=status_path,
                status_payload=status_payload,
                response_mode=response_mode,
                planner_state=planner_state,
                turn_state=turn_state,
            )

        payload.setdefault("timing_ms", {})
        payload["timing_ms"]["stt_ms"] = stt_ms
        payload["timing_ms"]["voice_affect_ms"] = affect_ms
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
