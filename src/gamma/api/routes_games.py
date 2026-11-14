"""Games connect here with the Neuro SDK protocol (see gamma.games.neuro_protocol).

Off unless games_neuro_enabled is set, and every connection must present
games_neuro_key as ?key=..., since game mods cannot send the API bearer token.
"""
from __future__ import annotations

import asyncio
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..games.neuro_protocol import GameSession, ModelChooser, RandomChooser

router = APIRouter()
TICK_SECONDS = 1.0


def build_chooser():
    if settings.games_neuro_chooser == "random":
        return RandomChooser()
    from ..llm.factory import build_llm_adapter
    return ModelChooser(build_llm_adapter())


def _key_ok(key: str) -> bool:
    expected = settings.games_neuro_key
    return bool(settings.games_neuro_enabled and expected and key) and secrets.compare_digest(
        key.encode("utf-8"), expected.encode("utf-8"))


@router.websocket("/v1/games/neuro")
async def neuro_game(websocket: WebSocket, key: str = "") -> None:
    if not _key_ok(key):
        await websocket.close(code=4401, reason="games disabled or wrong key")
        return
    await websocket.accept()
    session = GameSession(build_chooser())
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                replies = session.tick()
            else:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # A model choice can take seconds; keep it off the event loop.
                replies = await asyncio.to_thread(session.receive, message)
            for reply in replies:
                await websocket.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        return
