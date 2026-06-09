from __future__ import annotations

from pydantic import BaseModel


class SpeakerContext(BaseModel):
    """Identity hint provided by the caller.

    source     - where the message came from: "local", "discord", "game"
    platform_id - the platform-specific identifier (Discord user ID, game username, etc.)
    voice_id   - reserved for future voice-biometric fingerprint matching
    """

    source: str
    platform_id: str | None = None
    voice_id: str | None = None


class ConversationRequest(BaseModel):
    user_text: str
    session_id: str | None = None
    synthesize_speech: bool = False
    speaker: SpeakerContext | None = None
    fast_mode: bool = False


class ConversationTurn(BaseModel):
    role: str
    text: str
