from __future__ import annotations

import base64
import time
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ..config import settings
from ..schemas.voice import VoiceRoundtripResponse, VoiceTranscriptionResponse
from .stt import STTService
from ..conversation.service import ConversationService


class VoiceRoundtripService:
    def __init__(self) -> None:
        self._stt = STTService()
        self._conversation = ConversationService()

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
        try:
            transcript_response = self.transcribe_path(temp_path)
            transcript = transcript_response.transcript
            timing.update(transcript_response.timing_ms)
            if not transcript:
                raise ValueError("transcription came back empty")

            conversation_started = time.perf_counter()
            response = self._conversation.respond(
                transcript,
                session_id=session_id,
                synthesize_speech=synthesize_speech,
            )
            timing["conversation_ms"] = round((time.perf_counter() - conversation_started) * 1000, 1)
            timing["total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)

            audio_base64: str | None = None
            if response.audio_path:
                audio_bytes = Path(response.audio_path).read_bytes()
                audio_base64 = base64.b64encode(audio_bytes).decode("ascii")

            return VoiceRoundtripResponse(
                transcript=transcript,
                reply_text=response.spoken_text,
                audio_content_type=response.audio_content_type,
                audio_base64=audio_base64,
                timing_ms=timing,
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    async def run_transcription(self, *, audio_file: UploadFile) -> VoiceTranscriptionResponse:
        temp_path = await self.save_upload(audio_file)
        try:
            return self.transcribe_path(temp_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def transcribe_path(self, path: Path) -> VoiceTranscriptionResponse:
        stt_started = time.perf_counter()
        transcript = self._stt.transcribe_audio(str(path)).strip()
        return VoiceTranscriptionResponse(
            transcript=transcript,
            timing_ms={"stt_ms": round((time.perf_counter() - stt_started) * 1000, 1)},
        )

    async def save_upload(self, audio_file: UploadFile) -> Path:
        uploads_dir = settings.data_dir / "runtime" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(audio_file.filename or "").suffix or self._suffix_for_content_type(audio_file.content_type)
        target = uploads_dir / f"voice-upload-{uuid4().hex}{suffix}"
        content = await audio_file.read()
        target.write_bytes(content)
        return target

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
