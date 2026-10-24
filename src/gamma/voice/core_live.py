"""Core-owned live turn orchestration during the worker migration."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import threading
from uuid import uuid4

from ..conversation.cancellation import CancellationBoundary
from ..brain.queue import BrainQueueFull
from ..brain.runtime import await_brain_job
from ..delivery.digest import fingerprint
from ..performer.models import PerformerOutputEvent
from ..presence import load_presence_state
from ..schemas.voice import LiveVoiceJobResponse
from .live_runtime import LiveTurnRuntime


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _context():
    state = load_presence_state(downgrade_stale_live=True)
    return fingerprint({key: state.get(key) for key in
        ("control_revision", "mode", "audience", "outputs", "requires_confirmation")})


@dataclass
class _Turn:
    record: LiveVoiceJobResponse
    context: str
    cancelled: CancellationBoundary = field(default_factory=CancellationBoundary)
    grant: str | None = None


class CoreLiveTurnRuntime(LiveTurnRuntime):
    def __init__(self, *, media, conversation, bus, capacity=8, history_limit=100, compatibility_modes=False):
        if capacity < 1 or history_limit < capacity:
            raise ValueError("invalid live turn limits")
        self._media, self._conversation, self._bus = media, conversation, bus
        self._compatibility_modes = compatibility_modes
        self._capacity, self._history_limit = capacity, history_limit
        self._lock = threading.RLock()
        self._jobs, self._active, self._tasks = {}, set(), set()
        self._closed = False

    async def start_turn(self, *, audio_file, session_id, synthesize_speech,
                         response_mode="simple_chunked", turn_id=None):
        if self._compatibility_modes and response_mode == "incremental_experimental":
            response_mode = "simple_chunked"
        if response_mode != "simple_chunked":
            raise ValueError("core live runtime supports simple_chunked only")
        turn_id = turn_id or uuid4().hex
        if not isinstance(turn_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", turn_id):
            raise ValueError("invalid turn_id")
        with self._lock:
            if self._closed or len(self._active) >= self._capacity:
                raise BrainQueueFull("live turn capacity unavailable")
            if turn_id in self._jobs:
                raise ValueError("turn_id already exists")
            for old_id in list(self._jobs):
                if len(self._jobs) < self._history_limit:
                    break
                if old_id not in self._active:
                    del self._jobs[old_id]
            turn = _Turn(LiveVoiceJobResponse(turn_id=turn_id, status="queued", session_id=session_id,
                synthesize_speech=synthesize_speech, response_mode=response_mode, created_at=_now()), _context())
            self._jobs[turn_id] = turn
            self._active.add(turn_id)
        try:
            path = await self._media.save_upload(audio_file)
        except BaseException as exc:
            with self._lock:
                if not turn.cancelled.is_set():
                    turn.record.status, turn.record.error = "failed", type(exc).__name__
                self._active.discard(turn_id)
            raise
        task = asyncio.create_task(self._run(turn, path), name=f"live-turn-{turn_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.get_turn(turn_id)

    def _obsolete(self, turn):
        if not turn.cancelled.is_set() and turn.context != _context():
            self.cancel_turn(turn.record.turn_id, reason="presence_changed")
        return turn.cancelled.is_set()

    async def _run(self, turn, path):
        turn_id = turn.record.turn_id
        try:
            if self._obsolete(turn):
                path.unlink(missing_ok=True)
                return
            with self._lock:
                if turn.cancelled.is_set():
                    path.unlink(missing_ok=True)
                    return
                turn.record.status, turn.record.started_at = "transcribing", _now()
            transcript = await self._media._transcribe_owned(path)
            if self._obsolete(turn):
                return
            with self._lock:
                if turn.cancelled.is_set():
                    return
                turn.record.transcript, turn.record.audio_context = transcript.transcript, transcript.audio_context
                turn.record.timing_ms = dict(transcript.timing_ms)
                turn.record.status = "queued_for_brain"
            if not transcript.transcript.strip():
                raise ValueError("empty transcript")

            def generate():
                if self._obsolete(turn):
                    raise asyncio.CancelledError()
                with self._lock:
                    if turn.cancelled.is_set():
                        raise asyncio.CancelledError()
                    turn.record.status = "generating"
                response = self._conversation.respond(transcript.transcript, session_id=turn.record.session_id,
                    synthesize_speech=turn.record.synthesize_speech, fast_mode=True, delivery_context="direct_voice",
                    defer_delivery=True, _cancellation=turn.cancelled)
                with self._lock:
                    turn.grant = response.tts_metadata.get("delivery_grant")
                    turn.record.decision_trace_id = response.route_trace_id
                    turn.record.delivery_grant = turn.grant
                if self._obsolete(turn):
                    if turn.grant:
                        self._conversation.cancel_delivery(turn.grant)
                    raise asyncio.CancelledError()
                return response

            response = await await_brain_job(generate)
            if self._obsolete(turn):
                return
            with self._lock:
                if turn.cancelled.is_set():
                    return
                turn.record.status = "synthesizing" if turn.record.synthesize_speech and response.speech_text else "delivering"
            await self._deliver_owned(turn, response)
            if self._obsolete(turn):
                return
            await asyncio.to_thread(self._conversation.check_delivery, response)
            if self._obsolete(turn):
                return
            with self._lock:
                if turn.cancelled.is_set():
                    return
                text = response.display_text or ""
                self._bus.publish(PerformerOutputEvent(type="subtitle_update", turn_id=turn_id,
                    source="core_live", target_policy="dashboard_monitor", payload={"text": text, "delivery_grant": turn.grant}))
                url = response.tts_metadata.get("delivery_audio_url")
                if url and turn.grant:
                    turn.record.playback_expected = True
                    self._bus.publish(PerformerOutputEvent(type="speech_chunk_ready", turn_id=turn_id,
                        source="core_live", target_policy="dashboard_monitor", payload={"text": response.speech_text or "",
                            "audio_url": url, "delivery_grant": turn.grant, "chunk_index": 1, "is_final": True}))
                turn.record.reply_text = text
                turn.record.timing_ms.update(response.timing_ms)
                turn.record.status, turn.record.completed_at = "completed", _now()
        except asyncio.CancelledError:
            self.cancel_turn(turn_id, reason=turn.record.cancel_reason or "runtime_stopped")
        except Exception as exc:
            with self._lock:
                if not turn.cancelled.is_set():
                    turn.record.status, turn.record.error = "failed", type(exc).__name__
                    turn.record.completed_at = _now()
            if turn.grant:
                self._conversation.cancel_delivery(turn.grant)
        finally:
            with self._lock:
                self._active.discard(turn_id)

    async def _deliver_owned(self, turn, response):
        task = asyncio.create_task(asyncio.to_thread(self._conversation.deliver_response, response,
            synthesize_speech=turn.record.synthesize_speech))
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
                self.cancel_turn(turn.record.turn_id, reason="runtime_stopped")
        if cancelled:
            raise asyncio.CancelledError()

    def get_turn(self, turn_id):
        with self._lock:
            return self._jobs[turn_id].record.model_copy(deep=True)

    def cancel_turn(self, turn_id, *, reason="interrupted"):
        with self._lock:
            turn = self._jobs[turn_id]
            turn.cancelled.set()
            turn.record.status, turn.record.cancel_reason = "cancelled", reason
            turn.record.cancel_requested_at = turn.record.cancelled_at = _now()
            grant = turn.grant
            result = turn.record.model_copy(deep=True)
        if grant:
            self._conversation.cancel_delivery(grant)
        return result

    def cancel_active_turns(self, *, reason="interrupted"):
        with self._lock:
            return [self.cancel_turn(key, reason=reason) for key in list(self._active)]

    def get_recent_history(self, *, limit=20):
        with self._lock:
            return [turn.record.model_dump() for turn in list(self._jobs.values())[-max(1, min(limit, self._history_limit)):]]

    def close(self):
        with self._lock:
            self._closed = True
        return self.cancel_active_turns(reason="runtime_stopped")
