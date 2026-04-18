from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings


_DEFAULT_STATE_PATH = settings.data_dir / "assistant_state.json"
_ALLOWED_EMOTIONS = {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"}


@dataclass(slots=True)
class AssistantFeelingState:
    current_emotion: str = "neutral"
    recent_emotions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    updated_at: str | None = None

    def to_prompt_block(self) -> str:
        lines = [f"current_emotion: {self.current_emotion}"]
        if self.recent_emotions:
            lines.append("recent_emotions: " + ", ".join(self.recent_emotions[-5:]))
        if self.notes:
            lines.append("recent_feelings_notes:")
            lines.extend(f"- {note}" for note in self.notes[-4:])
        if self.updated_at:
            lines.append(f"updated_at: {self.updated_at}")
        return "\n".join(lines)


class AssistantStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_STATE_PATH

    def load(self) -> AssistantFeelingState:
        if not self._path.exists():
            return AssistantFeelingState()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return AssistantFeelingState()
        current = str(payload.get("current_emotion", "neutral")).strip().lower()
        if current not in _ALLOWED_EMOTIONS:
            current = "neutral"
        recent = [
            str(item).strip().lower()
            for item in payload.get("recent_emotions", [])
            if str(item).strip().lower() in _ALLOWED_EMOTIONS
        ]
        notes = [" ".join(str(item).split())[:180] for item in payload.get("notes", []) if str(item).strip()]
        updated_at = payload.get("updated_at")
        return AssistantFeelingState(
            current_emotion=current,
            recent_emotions=recent[-8:],
            notes=notes[-8:],
            updated_at=str(updated_at) if updated_at else None,
        )

    def update(self, *, emotion: str, user_text: str, reply_text: str) -> AssistantFeelingState:
        state = self.load()
        normalized = emotion.strip().lower() if emotion else "neutral"
        if normalized not in _ALLOWED_EMOTIONS:
            normalized = "neutral"
        state.current_emotion = normalized
        state.recent_emotions.append(normalized)
        state.recent_emotions = state.recent_emotions[-8:]
        note = self._build_note(user_text=user_text, reply_text=reply_text, emotion=normalized)
        if note:
            state.notes.append(note)
            state.notes = state.notes[-8:]
        state.updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "current_emotion": state.current_emotion,
                    "recent_emotions": state.recent_emotions,
                    "notes": state.notes,
                    "updated_at": state.updated_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return state

    def _build_note(self, *, user_text: str, reply_text: str, emotion: str) -> str | None:
        user_preview = " ".join(user_text.split())[:90]
        reply_preview = " ".join(reply_text.split())[:90]
        if not user_preview and not reply_preview:
            return None
        return f"{emotion}: user='{user_preview}' reply='{reply_preview}'"
