from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AssistantTurnStatus = Literal["planned", "generating", "synthesizing", "speaking", "completed", "interrupted", "cancelled", "failed"]
SentenceStatus = Literal["pending", "generated", "synthesizing", "ready", "played", "discarded", "failed"]


@dataclass(slots=True)
class SentenceState:
    sentence_index: int
    text: str = ""
    status: SentenceStatus = "pending"
    is_final: bool = False
    timing_ms: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class AssistantTurnState:
    turn_id: str
    session_id: str | None
    user_text: str
    response_mode: str = "simple_chunked"
    status: AssistantTurnStatus = "planned"
    planner_state: dict[str, object] = field(default_factory=dict)
    assistant_reply_so_far: str = ""
    next_sentence_index: int = 1
    cancel_requested: bool = False
    interrupted: bool = False
    sentences: list[SentenceState] = field(default_factory=list)

    def append_sentence(self, sentence: SentenceState) -> None:
        self.sentences.append(sentence)
        if sentence.text:
            self.assistant_reply_so_far = f"{self.assistant_reply_so_far} {sentence.text}".strip()
        self.next_sentence_index = sentence.sentence_index + 1
