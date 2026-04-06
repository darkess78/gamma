from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EmotionTag = Literal["neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"]
MemorySubjectType = Literal["primary_user", "other_person", "unknown"]


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
    spoken_text: str
    emotion: EmotionTag = "neutral"
    internal_summary: str | None = None
    motions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolExecutionResult] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    vision: VisionAnalysis | None = None
    audio_path: str | None = None
    audio_content_type: str | None = None
