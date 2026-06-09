from __future__ import annotations

from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import DEFAULT_TARGET_POLICY, PerformerOutputEvent, utc_now


SpokenTurnStatus = Literal["queued", "generating", "synthesizing", "speaking", "completed", "interrupted", "cancelled", "failed"]


class SpokenTurn(BaseModel):
    turn_id: str
    status: SpokenTurnStatus = "queued"
    target_policy: str = DEFAULT_TARGET_POLICY
    source: str = "unknown"
    input: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] = Field(default_factory=dict)
    generated_text: str | None = None
    subtitle: str | None = None
    audio_artifacts: list[str] = Field(default_factory=list)
    chunk_count: int = 0
    cancellation_reason: str | None = None
    timing_ms: dict[str, float] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class SpokenTurnStore:
    def __init__(self, *, history_limit: int = 200) -> None:
        self._turns: dict[str, SpokenTurn] = {}
        self._order: deque[str] = deque(maxlen=max(1, history_limit))

    def upsert(self, turn_id: str, **updates: Any) -> SpokenTurn:
        current = self._turns.get(turn_id)
        if current is None:
            current = SpokenTurn(turn_id=turn_id)
            self._remember(turn_id)
        payload = current.model_dump()
        payload.update({key: value for key, value in updates.items() if value is not None})
        payload["updated_at"] = utc_now()
        updated = SpokenTurn(**payload)
        self._turns[turn_id] = updated
        return updated

    def transition(self, turn_id: str, status: SpokenTurnStatus, **updates: Any) -> SpokenTurn:
        return self.upsert(turn_id, status=status, **updates)

    def apply_event(self, event: PerformerOutputEvent) -> SpokenTurn:
        updates: dict[str, Any] = {
            "target_policy": event.target_policy,
            "source": event.source,
        }
        if isinstance(event.payload.get("input"), dict):
            updates["input"] = event.payload["input"]
        if isinstance(event.payload.get("actor"), dict):
            updates["actor"] = event.payload["actor"]
        if event.type == "turn_started":
            updates["status"] = _status_or_default(event.payload.get("status"), "generating")
        elif event.type == "turn_state_changed":
            updates["status"] = _status_or_default(event.payload.get("state"), "generating")
        elif event.type in {"speech_started", "speech_chunk_ready"}:
            updates["status"] = "speaking"
        elif event.type == "speech_ended":
            updates["status"] = "completed"
        elif event.type == "output_cleared":
            updates["status"] = _terminal_clear_status(event.payload.get("status"))
            updates["cancellation_reason"] = event.payload.get("reason")
        if "text" in event.payload:
            updates["generated_text"] = str(event.payload.get("text") or "")
        if event.type in {"subtitle_update", "subtitle_clear"}:
            updates["subtitle"] = str(event.payload.get("text") or "")
        if event.type == "speech_chunk_ready":
            updates["chunk_count"] = max((self.get(event.turn_id).chunk_count if self.get(event.turn_id) else 0), int(event.payload.get("chunk_index") or 0))
        if isinstance(event.payload.get("timing_ms"), dict):
            updates["timing_ms"] = event.payload["timing_ms"]
        if event.payload.get("audio_artifact"):
            current = self.get(event.turn_id)
            artifacts = list(current.audio_artifacts if current else [])
            artifact = str(event.payload["audio_artifact"])
            if artifact not in artifacts:
                artifacts.append(artifact)
            updates["audio_artifacts"] = artifacts
        return self.upsert(event.turn_id, **updates)

    def get(self, turn_id: str) -> SpokenTurn | None:
        return self._turns.get(turn_id)

    def recent(self, *, limit: int = 20) -> list[SpokenTurn]:
        ids = list(self._order)[-max(1, limit):]
        return [self._turns[turn_id] for turn_id in ids if turn_id in self._turns]

    def _remember(self, turn_id: str) -> None:
        if turn_id in self._order:
            return
        if len(self._order) == self._order.maxlen:
            old_turn_id = self._order[0]
            self._turns.pop(old_turn_id, None)
        self._order.append(turn_id)


def _status_or_default(value: Any, default: SpokenTurnStatus) -> SpokenTurnStatus:
    text = str(value or "").strip().lower()
    if text in {"queued", "generating", "synthesizing", "speaking", "completed", "interrupted", "cancelled", "failed"}:
        return text  # type: ignore[return-value]
    return default


def _terminal_clear_status(value: Any) -> SpokenTurnStatus:
    text = str(value or "").strip().lower()
    if text in {"cancelled", "failed", "interrupted"}:
        return text  # type: ignore[return-value]
    return "interrupted"
