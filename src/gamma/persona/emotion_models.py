from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class AssistantEmotionState:
    current_emotion: str = "neutral"
    intensity: float = 0.0
    emotional_target: str | None = None
    cause_summary: str | None = None
    updated_at: str | None = None
    decay_turns_remaining: int = 0
    recent_emotions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [
            f"current_emotion: {self.current_emotion}",
            f"intensity: {self.intensity:.2f}",
            f"decay_turns_remaining: {self.decay_turns_remaining}",
        ]
        if self.emotional_target:
            lines.append(f"emotional_target: {self.emotional_target}")
        if self.cause_summary:
            lines.append(f"cause_summary: {self.cause_summary}")
        if self.recent_emotions:
            lines.append("recent_emotions: " + ", ".join(self.recent_emotions[-5:]))
        if self.notes:
            lines.append("recent_feelings_notes:")
            lines.extend(f"- {note}" for note in self.notes[-4:])
        if self.updated_at:
            lines.append(f"updated_at: {self.updated_at}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class EmotionalEpisode:
    event_summary: str
    emotion: str
    intensity: float
    trigger_type: str
    relationship_effect: str
    importance: float
    created_at: str
    session_id: str | None = None
    subject_name: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class EmotionalPattern:
    pattern_text: str
    emotion_family: str
    confidence: float
    evidence_count: int
    subject_scope: str
    last_reinforced_at: str

    def as_dict(self) -> dict:
        return asdict(self)
