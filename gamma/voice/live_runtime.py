from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fastapi import UploadFile

from ..schemas.voice import LiveVoiceJobResponse
from .live_jobs import LiveVoiceJobManager


class LiveTurnRuntime(ABC):
    """Boundary for live voice turn execution.

    The current implementation is subprocess + JSON files. Dashboard and API
    callers should depend on this interface so a lower-latency runtime can
    replace it later without changing those callers.
    """

    @abstractmethod
    async def start_turn(
        self,
        *,
        audio_file: UploadFile,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str = "simple_chunked",
        turn_id: str | None = None,
    ) -> LiveVoiceJobResponse:
        raise NotImplementedError

    @abstractmethod
    def get_turn(self, turn_id: str) -> LiveVoiceJobResponse:
        raise NotImplementedError

    @abstractmethod
    def cancel_turn(self, turn_id: str, *, reason: str = "interrupted") -> LiveVoiceJobResponse:
        raise NotImplementedError

    @abstractmethod
    def get_recent_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError


class SubprocessLiveTurnRuntime(LiveTurnRuntime):
    def __init__(self, manager: LiveVoiceJobManager | None = None) -> None:
        self._manager = manager or LiveVoiceJobManager()

    async def start_turn(
        self,
        *,
        audio_file: UploadFile,
        session_id: str | None,
        synthesize_speech: bool,
        response_mode: str = "simple_chunked",
        turn_id: str | None = None,
    ) -> LiveVoiceJobResponse:
        return await self._manager.start_job(
            audio_file=audio_file,
            session_id=session_id,
            synthesize_speech=synthesize_speech,
            response_mode=response_mode,
            turn_id=turn_id,
        )

    def get_turn(self, turn_id: str) -> LiveVoiceJobResponse:
        return self._manager.get_job(turn_id)

    def cancel_turn(self, turn_id: str, *, reason: str = "interrupted") -> LiveVoiceJobResponse:
        return self._manager.cancel_job(turn_id, reason=reason)

    def get_recent_history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._manager.get_recent_history(limit=limit)
