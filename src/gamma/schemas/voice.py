from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field


class VoiceReplyChunk(BaseModel):
    chunk_index: int
    text: str
    audio_content_type: str | None = None
    audio_base64: str | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    interruptible: bool = True
    protect_ms: int = 0
    is_final: bool = False


class VoiceTranscriptionResponse(BaseModel):
    transcript: str
    timing_ms: dict[str, float] = {}


class LiveVoiceJobResponse(BaseModel):
    turn_id: str
    status: str
    session_id: str | None = None
    synthesize_speech: bool = True
    response_mode: str | None = None
    worker_pid: int | None = None
    transcript: str | None = None
    reply_text: str | None = None
    reply_chunks: list[VoiceReplyChunk] = Field(default_factory=list)
    audio_content_type: str | None = None
    audio_base64: str | None = None
    timing_ms: dict[str, float] = {}
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested_at: str | None = None
    cancelled_at: str | None = None
    cancel_latency_ms: float | None = None
    cancel_reason: str | None = None
    error: str | None = None


class VoiceRoundtripResponse(BaseModel):
    transcript: str
    reply_text: str
    reply_chunks: list[VoiceReplyChunk] = Field(default_factory=list)
    audio_content_type: str | None = None
    audio_base64: str | None = None
    timing_ms: dict[str, float] = {}
