from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.pool import NullPool
from sqlmodel import create_engine

from ..config import settings
from ..memory.documents import StateDocuments


_DEFAULT_STATE_PATH = settings.data_dir / "assistant_state.json"
_ALLOWED_EMOTIONS = {"neutral", "happy", "teasing", "concerned", "excited", "embarrassed", "annoyed"}


@dataclass(slots=True)
class AssistantFeelingState:
    """Assistant feeling state.
    
    Attributes:
        current_emotion: Current emotion.
        recent_emotions: List of recent emotions.
        notes: List of emotion notes.
        updated_at: Update timestamp.
    
    Methods:
        to_prompt_block: Generate prompt block.
    """
    current_emotion: str = "neutral"
    recent_emotions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    updated_at: str | None = None

    def to_prompt_block(self) -> str:
        """Generate prompt block.
        
        Returns:
            str: Prompt block string.
        """
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
    """Assistant state store.
    
    Attributes:
        _path: State file path.
    
    Methods:
        __init__: Initialize store.
        load: Load state.
        update: Update state.
        _build_note: Build emotion note.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Initialize store.
        
        Args:
            path: State file path (default from settings).
        """
        self._path = path or settings.data_dir / "assistant_state.json"
        if path is None:
            self._documents = StateDocuments()
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            database = self._path.with_suffix(self._path.suffix + ".state.sqlite3")
            self._documents = StateDocuments(create_engine(f"sqlite:///{database}", poolclass=NullPool))

    def load(self) -> AssistantFeelingState:
        """Load state.
        
        Returns:
            AssistantFeelingState: Loaded state.
        """
        return self._decode(self._documents.load("assistant_feeling", self._path))

    @staticmethod
    def _decode(payload: dict) -> AssistantFeelingState:
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

    def update(self, *, emotion: str, user_text: str, reply_text: str, session=None) -> AssistantFeelingState:
        """Update state.
        
        Args:
            emotion: New emotion.
            user_text: User text snippet.
            reply_text: Reply text snippet.
        
        Returns:
            AssistantFeelingState: Updated state.
        """
        def transform(payload):
            return self._propose(payload, emotion=emotion, user_text=user_text, reply_text=reply_text)
        return self._decode(self._documents.mutate("assistant_feeling", self._path, transform, session=session))

    def _propose(self, payload: dict, *, emotion: str, user_text: str, reply_text: str) -> dict:
        state = self._decode(payload)
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
        return asdict(state)

    def _build_note(self, *, user_text: str, reply_text: str, emotion: str) -> str | None:
        """Build emotion note.
        
        Args:
            user_text: User text.
            reply_text: Reply text.
            emotion: Emotion.
        
        Returns:
            str | None: Note string or None.
        """
        user_preview = " ".join(user_text.split())[:90]
        reply_preview = " ".join(reply_text.split())[:90]
        if not user_preview and not reply_preview:
            return None
        return f"{emotion}: user='{user_preview}' reply='{reply_preview}'"
