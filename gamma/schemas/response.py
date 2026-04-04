from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    type: str
    text: str
    importance: float = 0.5
    tags: list[str] = Field(default_factory=list)


class ToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


class AssistantResponse(BaseModel):
    spoken_text: str
    emotion: str
    motions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    audio_path: str | None = None
    audio_content_type: str | None = None
