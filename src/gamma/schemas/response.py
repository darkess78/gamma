from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


EmotionTag = Literal["neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"]
MemorySubjectType = Literal["primary_user", "other_person", "unknown"]
ResponseAction = Literal["reply", "acknowledge", "stay_silent", "defer", "tool_first"]
DeliveryMode = Literal["speech", "text_only", "silent", "deferred"]


class MemoryCandidate(BaseModel):
    type: str
    text: str
    importance: float = 0.5
    tags: list[str] = Field(default_factory=list)
    subject_type: MemorySubjectType = "primary_user"
    subject_name: str | None = None
    relationship_to_user: str | None = None


class ToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool: str
    ok: bool
    output: str
    metadata: dict = Field(default_factory=dict)


class BoundedStateUpdates(BaseModel):
    emotion: EmotionTag | None = None
    active_topic: str | None = Field(default=None, max_length=500)
    current_objective: str | None = Field(default=None, max_length=1000)
    deferred_intention: str | None = Field(default=None, max_length=1000)
    relationship_signals: list[str] = Field(default_factory=list, max_length=8)


class ConversationTurnDraft(BaseModel):
    action: ResponseAction
    requested_delivery: DeliveryMode
    final_text: str = Field(default="", max_length=16_000)
    internal_summary: str | None = Field(default=None, max_length=1000)
    emotion: EmotionTag = "neutral"
    voice_styles: list[str] = Field(default_factory=list, max_length=8)
    motions: list[str] = Field(default_factory=list, max_length=8)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=12)
    state_updates: BoundedStateUpdates = Field(default_factory=BoundedStateUpdates)
    reason_code: str = Field(default="reply", pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class VisionObject(BaseModel):
    name: str
    description: str | None = None
    confidence: float = 0.0


class VisionTextBlock(BaseModel):
    label: str
    text: str
    block_type: str = "text"


class VisionInterfaceElement(BaseModel):
    name: str
    element_type: str = "unknown"
    role: str | None = None
    state: str | None = None


class VisionAnalysis(BaseModel):
    image_type: str = "unknown"
    summary: str
    visible_text: str | None = None
    objects: list[VisionObject] = Field(default_factory=list)
    key_text_blocks: list[VisionTextBlock] = Field(default_factory=list)
    interface_elements: list[VisionInterfaceElement] = Field(default_factory=list)
    document_structure: list[str] = Field(default_factory=list)
    likely_actions: list[str] = Field(default_factory=list)
    spatial_notes: list[str] = Field(default_factory=list)
    suggested_follow_ups: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class AssistantResponse(BaseModel):
    # ``spoken_text`` remains the compatibility field for existing API clients.
    # New output consumers must use display_text or speech_text explicitly.
    spoken_text: str = ""
    display_text: str | None = None
    speech_text: str | None = None
    delivery_mode: DeliveryMode = "speech"
    response_action: ResponseAction = "reply"
    reason_code: str = "reply"
    emotion: EmotionTag = "neutral"
    voice_styles: list[str] = Field(default_factory=list)
    internal_summary: str | None = None
    motions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolExecutionResult] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    vision: VisionAnalysis | None = None
    audio_path: str | None = None
    audio_content_type: str | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    tts_metadata: dict[str, Any] = Field(default_factory=dict)
    route_trace_id: str | None = None
    state_updates: BoundedStateUpdates = Field(default_factory=BoundedStateUpdates)

    @model_validator(mode="after")
    def populate_compatibility_text(self) -> "AssistantResponse":
        legacy_text = self.spoken_text or ""
        if self.display_text is None:
            self.display_text = legacy_text
        if self.speech_text is None:
            self.speech_text = legacy_text if self.delivery_mode == "speech" else ""
        if not self.spoken_text:
            # Old clients receive intentionally communicated text, even for
            # text-only delivery, but this field is never a TTS authority.
            self.spoken_text = self.display_text or self.speech_text or ""
        if self.delivery_mode in {"silent", "deferred"}:
            self.display_text = ""
            self.speech_text = ""
            self.spoken_text = ""
            self.audio_path = None
            self.audio_content_type = None
        elif self.delivery_mode == "text_only":
            self.speech_text = ""
        return self
