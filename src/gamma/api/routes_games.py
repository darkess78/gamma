"""Games connect here with Gamma's game protocol (gamma.games.protocol, specs/games.md).

Off unless games_enabled is set, and every connection must present games_key
as ?key=..., since game mods usually cannot send the API bearer token.
"""
from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..games.protocol import GameSession, ModelChooser, RandomChooser

router = APIRouter()
TICK_SECONDS = 1.0
MAX_MESSAGE_BYTES = 256_000


def build_chooser():
    if settings.games_chooser == "random":
        return RandomChooser()
    from ..llm.factory import build_llm_adapter
    return ModelChooser(build_llm_adapter())


def _key_ok(key: str) -> bool:
    expected = settings.games_key
    return bool(settings.games_enabled and expected and key) and secrets.compare_digest(
        key.encode("utf-8"), expected.encode("utf-8"))


@router.websocket("/v1/games/play")
async def play_game(websocket: WebSocket, key: str = "") -> None:
    if not _key_ok(key):
        await websocket.close(code=4401, reason="games disabled or wrong key")
        return
    await websocket.accept()
    session = GameSession(build_chooser())
    try:
        while not session.closed:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                replies = session.tick()
            else:
                if len(raw) > MAX_MESSAGE_BYTES:
                    replies = [{"type": "error", "message": "message too large"}]
                else:
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        message = None
                    # A model choice can take seconds; keep it off the event loop.
                    replies = await asyncio.to_thread(session.receive, message)
            for reply in replies:
                await websocket.send_text(json.dumps(reply))
        await websocket.close(code=1000)
    except WebSocketDisconnect:
        return
