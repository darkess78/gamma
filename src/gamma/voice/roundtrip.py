from __future__ import annotations

import asyncio
import base64
import time
import threading
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ..brain.runtime import await_brain_job
from ..brain.queue import BrainQueueFull
from ..brain.queue import BrainQueueClosed
from ..supervisor.worker_events import WorkerEvents
from ..resources.admission import Admission
from ..config import settings
from ..schemas.voice import VoiceReplyChunk, VoiceRoundtripResponse, VoiceTranscriptionResponse
from .audio_understanding import AudioUnderstandingService, build_audio_prompt_context
from .stt import STTService
from .stt_client import STTWorkerClient
from .stt_runtime import STTRuntime
from ..conversation.service import ConversationService
from .reply_chunking import split_reply_text
from .reply_interruptibility import build_interruptibility
from .uploads import save_stream


class VoiceRoundtripService:
    def __init__(self, *, conversation: ConversationService | None = None) -> None:
        self._stt = None
        self._audio_understanding = None
        self._conversation = conversation if conversation is not None else ConversationService()
        self._transcription_lock = threading.Lock()
        self._transcription_slots = threading.BoundedSemaphore(8)
        self._delivery_slots = threading.BoundedSemaphore(8)
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()
        with self._transcription_lock:
            if isinstance(self._stt, STTRuntime):
                self._stt.close()

    def stt_status(self) -> dict:
        if settings.stt_runtime != "worker":
            return {"runtime": settings.stt_runtime, "controls": []}
        state = self._stt.status() if self._stt is not None else {
            "pid": None, "running": False, "busy": False, "enabled": True, "closed": self._closed.is_set(),
        }
        return {"runtime": "worker", **state, "controls": [] if self._closed.is_set() else ["start", "stop", "restart"]}

    def stt_control(self, action: str) -> dict:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("unsupported STT action")
        if settings.stt_runtime != "worker" or self._closed.is_set():
            raise RuntimeError("STT worker controls unavailable")
        if not self._transcription_lock.acquire(blocking=False):
            raise RuntimeError("STT worker busy")
        try:
            self._ensure_stt()
            return self._stt.control(action)
        finally:
            self._transcription_lock.release()

    async def run(
        self,
        *,
        audio_file: UploadFile,
        session_id: str | None = None,
        synthesize_speech: bool = True,
    ) -> VoiceRoundtripResponse:
        started_at = time.perf_counter()
        temp_path = await self.save_upload(audio_file)
        timing: dict[str, float] = {}
        transcript_response = await self._transcribe_owned(temp_path)
        transcript = transcript_response.transcript
        audio_context = transcript_response.audio_context
        timing.update(transcript_response.timing_ms)
        if not transcript:
            raise ValueError("transcription came back empty")

        conversation_started = time.perf_counter()
        response = await await_brain_job(
            lambda: self._conversation.respond(
                transcript,
                session_id=session_id,
                synthesize_speech=synthesize_speech,
                fast_mode=True,
                defer_delivery=True,
                background_context=build_audio_prompt_context(audio_context) if audio_context else None,
            )
        )
        await self._deliver_owned(response, synthesize_speech=synthesize_speech)
        timing["conversation_ms"] = round((time.perf_counter() - conversation_started) * 1000, 1)
        if response.timing_ms:
            timing.update(response.timing_ms)
        timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)

        audio_base64: str | None = None
        reply_chunks: list[VoiceReplyChunk] = []
        if not response.audio_path:
            chunk_texts = split_reply_text(response.speech_text or "")
            chunk_policies = build_interruptibility(chunk_texts)
            for index, chunk_text in enumerate(chunk_texts, start=1):
                policy = chunk_policies[index - 1] if index - 1 < len(chunk_policies) else {}
                reply_chunks.append(
                    VoiceReplyChunk(
                        chunk_index=index,
                        text=chunk_text,
                        interruptible=bool(policy.get("interruptible", True)),
                        protect_ms=int(policy.get("protect_ms", 0) or 0),
                        is_final=index == len(chunk_texts),
                    )
                )

        return VoiceRoundtripResponse(
            transcript=transcript,
            audio_context=audio_context,
            reply_text=response.display_text or "",
            reply_chunks=reply_chunks,
            audio_content_type=response.audio_content_type,
            audio_base64=None,
            delivery_grant=response.tts_metadata.get("delivery_grant"),
            delivery_audio_url=response.tts_metadata.get("delivery_audio_url"),
            timing_ms=timing,
        )

    async def run_transcription(self, *, audio_file: UploadFile) -> VoiceTranscriptionResponse:
        temp_path = await self.save_upload(audio_file)
        return await self._transcribe_owned(temp_path)

    async def _transcribe_owned(self, path: Path) -> VoiceTranscriptionResponse:
        if not self._transcription_slots.acquire(blocking=False):
            path.unlink(missing_ok=True)
            raise BrainQueueFull("transcription queue full")
        cancelled = threading.Event()

        def execute() -> VoiceTranscriptionResponse:
            try:
                with self._transcription_lock:
                    if cancelled.is_set():
                        raise asyncio.CancelledError()
                    return self.transcribe_path(path)
            finally:
                try:
                    path.unlink(missing_ok=True)
                finally:
                    self._transcription_slots.release()

        task = asyncio.ensure_future(asyncio.to_thread(execute))

        def _on_done(t: asyncio.Task) -> None:
            if not t.cancelled():
                t.exception()

        task.add_done_callback(_on_done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _deliver_owned(self, response, *, synthesize_speech: bool) -> None:
        grant = response.tts_metadata.get("delivery_grant")
        if not self._delivery_slots.acquire(blocking=False):
            if grant:
                self._conversation.cancel_delivery(grant)
            raise BrainQueueFull("delivery queue full")
        cancelled = threading.Event()

        def execute() -> None:
            try:
                if cancelled.is_set():
                    raise asyncio.CancelledError()
                self._conversation.deliver_response(response, synthesize_speech=synthesize_speech)
            finally:
                self._delivery_slots.release()

        task = asyncio.ensure_future(asyncio.to_thread(execute))

        def _on_done(t: asyncio.Task) -> None:
            if not t.cancelled():
                t.exception()

        task.add_done_callback(_on_done)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            if grant:
                self._conversation.cancel_delivery(grant)
            raise

    def transcribe_path(self, path: Path) -> VoiceTranscriptionResponse:
        if self._closed.is_set():
            raise RuntimeError("voice transcription is closed")
        stt_started = time.perf_counter()
        self._ensure_stt()
        if isinstance(self._stt, STTRuntime) and not self._stt.status()["enabled"]:
            raise BrainQueueClosed("STT worker stopped")
        if self._audio_understanding is None:
            self._audio_understanding = AudioUnderstandingService()
        transcript = self._stt.transcribe_audio(str(path)).strip()
        stt_ms = round((time.perf_counter() - stt_started) * 1000, 1)
        audio_context = self._audio_understanding.analyze_path(path, transcript=transcript)
        return VoiceTranscriptionResponse(
            transcript=transcript,
            audio_context=audio_context,
            timing_ms={
                "stt_ms": stt_ms,
                "audio_understanding_ms": audio_context.timing_ms.get("total_ms", 0.0),
            },
        )

    def _ensure_stt(self) -> None:
        if self._stt is None:
            if settings.stt_runtime == "worker":
                if settings.stt_provider.strip().lower() not in {"stub", "faster-whisper", "faster_whisper", "whisper", "local"}:
                    raise ValueError("STT worker requires a local provider")
                self._stt = STTRuntime(STTWorkerClient(
                    [sys.executable, "-m", "gamma.voice.stt_worker"], cwd=settings.project_root,
                ), events=WorkerEvents(settings.data_dir / "runtime", "stt"),
                    admission=Admission(settings.data_dir / "runtime/model-admission.sqlite3"))
            elif settings.stt_runtime == "inprocess":
                self._stt = STTService()
            else:
                raise ValueError("unsupported STT runtime")

    async def save_upload(self, audio_file: UploadFile) -> Path:
        uploads_dir = settings.data_dir / "runtime" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(audio_file.filename or "").suffix or self._suffix_for_content_type(audio_file.content_type)
        target = uploads_dir / f"voice-upload-{uuid4().hex}{suffix}"
        return await save_stream(audio_file.file, target, max_bytes=settings.voice_upload_max_bytes)

    def _suffix_for_content_type(self, content_type: str | None) -> str:
        mapping = {
            "audio/webm": ".webm",
            "audio/webm;codecs=opus": ".webm",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/ogg": ".ogg",
        }
        return mapping.get((content_type or "").lower(), ".bin")
