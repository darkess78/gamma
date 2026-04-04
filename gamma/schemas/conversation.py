from __future__ import annotations

from pydantic import BaseModel


class ConversationRequest(BaseModel):
    user_text: str
    session_id: str | None = None
    synthesize_speech: bool = False


class ConversationTurn(BaseModel):
    role: str
    text: str
