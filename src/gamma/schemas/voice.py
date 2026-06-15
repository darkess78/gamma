from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field


class AudioEvent(BaseModel):
    label: str
    confidence: float
    start_ms: float | None = None
    end_ms: float | None = None
    source: str


class SpeakerAffect(BaseModel):
    emotion: str = "unknown"
    confidence: float = 0.0
    energy: str = "unknown"
    pace: str = "unknown"
    delivery: str = "unknown"
    source: str


class VoiceInputContext(BaseModel):
    ok: bool
    speaker_affect: SpeakerAffect | None = None
    events: list[AudioEvent] = Field(default_factory=list)
    features: dict[str, float | int | str | None] = Field(default_factory=dict)
    analyzer_version: str = "audio-understanding-v1"
    timing_ms: dict[str, float] = Field(default_factory=dict)
    detail: str | None = None


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
    audio_context: VoiceInputContext | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)


class LiveVoiceJobResponse(BaseModel):
    turn_id: str
    status: str
    session_id: str | None = None
    synthesize_speech: bool = True
    response_mode: str | None = None
    worker_pid: int | None = None
    transcript: str | None = None
    audio_context: VoiceInputContext | None = None
    reply_text: str | None = None
    reply_chunks: list[VoiceReplyChunk] = Field(default_factory=list)
    audio_content_type: str | None = None
    audio_base64: str | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
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
    audio_context: VoiceInputContext | None = None
    reply_text: str
    reply_chunks: list[VoiceReplyChunk] = Field(default_factory=list)
    audio_content_type: str | None = None
    audio_base64: str | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
