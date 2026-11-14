"""Performer output routes: event stream, targets, VTube Studio and 3D avatar adapters, audio artifacts."""
from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import deps
from ..config import settings
from ..performer.models import DEFAULT_TARGET_POLICY, KNOWN_TARGET_POLICIES


router = APIRouter()

_vtube_studio_runner_task: asyncio.Task[None] | None = None
_avatar3d_runner_task: asyncio.Task[None] | None = None


@router.get("/v1/performer/targets/{target_policy}/last-durable-output")
def performer_last_durable_output(target_policy: str) -> dict:
    if target_policy not in KNOWN_TARGET_POLICIES:
        raise HTTPException(status_code=400, detail="unsupported target policy")
    return {
        "ok": True,
        "target_policy": target_policy,
        "output": deps.get_conversation_service().durable_output_state(target_policy),
    }


@router.get("/v1/performer/events/recent")
def performer_recent_events(limit: int = 50, target_policy: str | None = None, after_sequence: int | None = None) -> dict:
    try:
        bus = deps.get_performer_bus()
        if target_policy is not None:
            target_policy = target_policy.strip().lower() or DEFAULT_TARGET_POLICY
        items = [
            event.model_dump()
            for event in bus.recent(limit=limit, target_policy=target_policy, after_sequence=after_sequence)
        ]
        return {
            "items": items,
            "stats": bus.stats(target_policy=target_policy),
            "replay": {
                **bus.replay_window(),
                "after_sequence": after_sequence,
                "gap": bus.replay_gap_after(after_sequence),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/v1/performer/status")
def performer_status() -> dict:
    try:
        bus = deps.get_performer_bus()
        recent = bus.recent(limit=1)
        recent_by_target = {}
        for target_policy in KNOWN_TARGET_POLICIES:
            target_recent = bus.recent(limit=1, target_policy=target_policy)
            recent_by_target[target_policy] = target_recent[-1].model_dump() if target_recent else None
        return {
            "ok": True,
            "stats": bus.stats(),
            "recent_event": recent[-1].model_dump() if recent else None,
            "recent_by_target": recent_by_target,
            "recent_turns": bus.recent_turns(limit=5),
            "adapters": {
                "vtube_studio": {**deps.get_vtube_studio_adapter().status(), "runner": deps.get_vtube_studio_runner().status()},
                "avatar3d": {**deps.get_avatar3d_adapter().status(), "runner": deps.get_avatar3d_runner().status()},
                "discord": deps.get_discord_runtime().status(),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/adapters/vtube-studio/start")
async def performer_vtube_studio_start() -> dict:
    global _vtube_studio_runner_task
    try:
        runner = deps.get_vtube_studio_runner()
        if _vtube_studio_runner_task is not None and not _vtube_studio_runner_task.done():
            return {"ok": True, "already_running": True, "status": runner.status()}
        _vtube_studio_runner_task = asyncio.create_task(runner.run_until_stopped())
        await asyncio.sleep(0)
        return {"ok": True, "already_running": False, "status": runner.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/adapters/vtube-studio/stop")
async def performer_vtube_studio_stop() -> dict:
    try:
        runner = deps.get_vtube_studio_runner()
        runner.stop()
        return {"ok": True, "status": runner.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def start_avatar3d_runner() -> dict:
    """Start the 3D avatar (VMC) runner if it is not already running."""
    global _avatar3d_runner_task
    runner = deps.get_avatar3d_runner()
    if not runner.adapter.config.enabled:
        return {"ok": False, "error": "avatar3d is disabled; set [avatar3d] enabled = true", "status": runner.status()}
    if _avatar3d_runner_task is not None and not _avatar3d_runner_task.done():
        return {"ok": True, "already_running": True, "status": runner.status()}
    _avatar3d_runner_task = asyncio.create_task(runner.run_until_stopped())
    await asyncio.sleep(0)
    return {"ok": True, "already_running": False, "status": runner.status()}


@router.post("/v1/performer/adapters/avatar-3d/start")
async def performer_avatar3d_start() -> dict:
    try:
        return await start_avatar3d_runner()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/adapters/avatar-3d/stop")
async def performer_avatar3d_stop() -> dict:
    try:
        runner = deps.get_avatar3d_runner()
        runner.stop()
        return {"ok": True, "status": runner.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/mute")
def performer_target_mute(target_policy: str, reason: str = "operator") -> dict:
    try:
        return deps.get_performer_bus().set_target_muted(target_policy, True, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/unmute")
def performer_target_unmute(target_policy: str, reason: str = "operator") -> dict:
    try:
        return deps.get_performer_bus().set_target_muted(target_policy, False, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/performer/targets/{target_policy}/clear")
def performer_target_clear(target_policy: str, reason: str = "operator") -> dict:
    try:
        return deps.get_performer_bus().clear_target(target_policy, reason=reason)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


_SERVABLE_AUDIO = re.compile(r"tts-[A-Za-z0-9-]{1,80}\.(?:wav|mp3|ogg|opus|flac|m4a|aac)")


@router.get("/v1/audio/artifacts/{filename}")
def audio_artifact(filename: str) -> FileResponse:
    """Shana's own synthesized speech only; mic captures and text sidecars are never served."""
    safe_name = Path(filename).name
    if not _SERVABLE_AUDIO.fullmatch(safe_name):
        raise HTTPException(status_code=404, detail="audio artifact not found")
    audio_path = settings.audio_output_dir / safe_name
    try:
        if audio_path.resolve().parent != settings.audio_output_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid audio artifact path")
    except OSError as exc:
        raise HTTPException(status_code=400, detail="invalid audio artifact path") from exc
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="audio artifact not found")
    media_type = mimetypes.guess_type(str(audio_path))[0] or "application/octet-stream"
    return FileResponse(str(audio_path), media_type=media_type)


@router.websocket("/v1/performer/events")
async def performer_events(
    websocket: WebSocket,
    replay_recent: int = 0,
    after_sequence: int | None = None,
    target_policy: str = DEFAULT_TARGET_POLICY,
    client_name: str = "",
) -> None:
    subscriber_target_policy = target_policy.strip().lower() or DEFAULT_TARGET_POLICY
    if not deps.websocket_feed_allowed(websocket, subscriber_target_policy):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    bus = deps.get_performer_bus()
    subscriber_id, queue = await bus.subscribe(
        replay_recent=max(0, min(replay_recent, 100)),
        after_sequence=after_sequence,
        target_policy=subscriber_target_policy,
        client_name=client_name,
        client_host=websocket.client.host if websocket.client else None,
    )
    await websocket.send_json(
        {
            "type": "ready",
            "subscriber_id": subscriber_id,
            "target_policy": subscriber_target_policy,
            "client_name": client_name.strip().lower() or "unknown_client",
            "replay_recent": max(0, min(replay_recent, 100)),
            "after_sequence": after_sequence,
            "replay_window": bus.replay_window(),
            "replay_gap": bus.replay_gap_after(after_sequence),
            "stats": bus.stats(target_policy=subscriber_target_policy),
        }
    )
    queue_task = asyncio.create_task(queue.get())
    receive_task = asyncio.create_task(websocket.receive_json())
    try:
        while True:
            done, _pending = await asyncio.wait(
                {queue_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if queue_task in done:
                await websocket.send_json(queue_task.result())
                queue_task = asyncio.create_task(queue.get())
            if receive_task in done:
                message = receive_task.result()
                if isinstance(message, dict) and message.get("type") == "subscriber_capabilities":
                    capabilities = bus.update_subscriber_capabilities(
                        subscriber_id,
                        text_ready=message.get("text_ready"),
                        audio_ready=message.get("audio_ready"),
                    )
                    await websocket.send_json({"type": "subscriber_capabilities", "capabilities": capabilities})
                receive_task = asyncio.create_task(websocket.receive_json())
    except WebSocketDisconnect:
        pass
    finally:
        queue_task.cancel()
        receive_task.cancel()
        bus.unsubscribe(subscriber_id)
