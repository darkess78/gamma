from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable

from fastapi import WebSocket


class LiveVoiceSession:
    partial_min_bytes = 16000
    partial_interval_seconds = 1.1
    poll_interval_seconds = 0.35

    def __init__(
        self,
        *,
        job_starter: Callable[..., dict],
        job_fetcher: Callable[[str], dict],
        job_canceler: Callable[..., dict],
        partial_transcriber: Callable[..., dict] | None = None,
    ) -> None:
        self._job_starter = job_starter
        self._job_fetcher = job_fetcher
        self._job_canceler = job_canceler
        self._partial_transcriber = partial_transcriber

    async def handle(self, websocket: WebSocket) -> None:
        await websocket.accept()
        await self._send_json(websocket, {"type": "ready"})

        audio_buffer = bytearray()
        receive_task = asyncio.create_task(websocket.receive())
        poll_task: asyncio.Task | None = None
        partial_task: asyncio.Task | None = None
        turn_open = False
        session_id: str | None = None
        synthesize_speech = True
        response_mode = "simple_chunked"
        current_turn_id = 0
        active_remote_turn_id: str | None = None
        active_remote_state: dict | None = None
        last_partial_text = ""
        interrupted_turn_ids: set[str] = set()
        sent_chunk_indexes: dict[str, set[int]] = {}

        async def cancel_partial_loop() -> None:
            nonlocal partial_task
            if partial_task is not None:
                partial_task.cancel()
                try:
                    await partial_task
                except asyncio.CancelledError:
                    pass
                partial_task = None

        async def partial_loop(turn_id: int) -> None:
            nonlocal last_partial_text
            while turn_open and current_turn_id == turn_id:
                await asyncio.sleep(self.partial_interval_seconds)
                if not turn_open or current_turn_id != turn_id:
                    return
                snapshot = bytes(audio_buffer)
                if len(snapshot) < self.partial_min_bytes or self._partial_transcriber is None:
                    continue
                try:
                    result = await asyncio.to_thread(self._partial_transcriber, pcm_bytes=snapshot)
                except Exception:
                    continue
                transcript = str(result.get("transcript", "")).strip()
                if not transcript or transcript == last_partial_text:
                    continue
                last_partial_text = transcript
                await self._send_json(
                    websocket,
                    {
                        "type": "partial_transcript",
                        "text": transcript,
                        "turn_id": turn_id,
                        "timing_ms": result.get("timing_ms", {}),
                    },
                )

        async def start_polling(remote_turn_id: str) -> None:
            nonlocal poll_task
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
            poll_task = asyncio.create_task(poll_remote_job(remote_turn_id))

        async def cancel_polling() -> None:
            nonlocal poll_task
            if poll_task is not None:
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
                poll_task = None

        async def poll_remote_job(remote_turn_id: str) -> None:
            nonlocal active_remote_turn_id, active_remote_state
            last_status: str | None = None
            while active_remote_turn_id == remote_turn_id:
                try:
                    payload = await asyncio.to_thread(self._job_fetcher, remote_turn_id)
                except Exception as exc:
                    await self._send_json(websocket, {"type": "error", "detail": f"Failed to fetch live turn: {exc}"})
                    await self._send_json(websocket, {"type": "state", "state": "failed", "detail": "Live turn polling failed."})
                    active_remote_turn_id = None
                    return
                active_remote_state = payload
                chunk_indexes = sent_chunk_indexes.setdefault(remote_turn_id, set())
                for chunk in payload.get("reply_chunks", []) or []:
                    if not isinstance(chunk, dict):
                        continue
                    chunk_index = int(chunk.get("chunk_index", 0) or 0)
                    if chunk_index <= 0 or chunk_index in chunk_indexes:
                        continue
                    if not chunk.get("audio_base64") or not chunk.get("audio_content_type"):
                        continue
                    chunk_indexes.add(chunk_index)
                    await self._send_json(
                        websocket,
                        {
                            "type": "reply_chunk_ready",
                            "turn_id": remote_turn_id,
                            "chunk": chunk,
                            "timing_ms": payload.get("timing_ms", {}),
                        },
                    )
                status = str(payload.get("status", "failed"))
                if status != last_status:
                    await self._send_json(
                        websocket,
                        {
                            "type": "state",
                            "state": status,
                            "detail": self._state_detail(status),
                            "turn_id": remote_turn_id,
                            "job": payload,
                        },
                    )
                    last_status = status

                if status == "completed":
                    if remote_turn_id in interrupted_turn_ids:
                        interrupted_turn_ids.discard(remote_turn_id)
                        await self._send_json(
                            websocket,
                            {
                                "type": "state",
                                "state": "cancelled",
                                "detail": "Interrupted turn result discarded.",
                                "turn_id": remote_turn_id,
                                "job": payload,
                            },
                        )
                    else:
                        await self._send_json(
                            websocket,
                            {"type": "transcript", "text": payload.get("transcript", ""), "turn_id": remote_turn_id},
                        )
                        await self._send_json(
                            websocket,
                            {
                                "type": "turn_result",
                                "turn_id": remote_turn_id,
                                "transcript": payload.get("transcript", ""),
                                "reply_text": payload.get("reply_text", ""),
                                "reply_chunks": payload.get("reply_chunks", []),
                                "audio_content_type": payload.get("audio_content_type"),
                                "audio_base64": payload.get("audio_base64"),
                                "timing_ms": payload.get("timing_ms", {}),
                                "job": payload,
                            },
                        )
                    active_remote_turn_id = None
                    sent_chunk_indexes.pop(remote_turn_id, None)
                    return
                if status in {"cancelled", "failed"}:
                    if status == "failed":
                        await self._send_json(
                            websocket,
                            {
                                "type": "error",
                                "detail": payload.get("error") or "Live turn failed.",
                                "turn_id": remote_turn_id,
                            },
                        )
                    active_remote_turn_id = None
                    sent_chunk_indexes.pop(remote_turn_id, None)
                    return
                await asyncio.sleep(self.poll_interval_seconds)

        async def cancel_active_remote_turn(reason: str, *, notify_client: bool = True) -> None:
            nonlocal active_remote_turn_id, active_remote_state
            if not active_remote_turn_id:
                return
            turn_id = active_remote_turn_id
            interrupted_turn_ids.add(turn_id)
            try:
                payload = await asyncio.to_thread(self._job_canceler, turn_id, reason=reason)
                active_remote_state = payload
            except Exception as exc:
                if notify_client:
                    await self._send_json(websocket, {"type": "error", "detail": f"Failed to cancel live turn: {exc}"})
                return
            if notify_client:
                await self._send_json(
                    websocket,
                    {
                        "type": "state",
                        "state": "cancelled",
                        "detail": "Live turn cancelled.",
                        "turn_id": turn_id,
                        "job": payload,
                    },
                )
            active_remote_turn_id = None
            sent_chunk_indexes.pop(turn_id, None)

        try:
            while True:
                wait_targets = {receive_task}
                if poll_task is not None:
                    wait_targets.add(poll_task)
                done, _pending = await asyncio.wait(wait_targets, return_when=asyncio.FIRST_COMPLETED)

                if receive_task in done:
                    message = receive_task.result()
                    if message.get("type") == "websocket.disconnect":
                        break
                    receive_task = asyncio.create_task(websocket.receive())

                    if message.get("bytes") is not None:
                        if turn_open:
                            audio_buffer.extend(message["bytes"])
                        continue

                    raw_text = message.get("text")
                    if not raw_text:
                        continue
                    payload = self._parse_message(raw_text)
                    event_type = payload.get("type", "")

                    if event_type == "ping":
                        await self._send_json(websocket, {"type": "pong"})
                        continue

                    if event_type == "interrupt_probe":
                        transcript = ""
                        timing_ms: dict = {}
                        audio_base64 = str(payload.get("audio_base64", "") or "")
                        if audio_base64 and self._partial_transcriber is not None:
                            try:
                                pcm_bytes = base64.b64decode(audio_base64)
                                result = await asyncio.to_thread(self._partial_transcriber, pcm_bytes=pcm_bytes)
                                transcript = str(result.get("transcript", "")).strip()
                                timing_ms = result.get("timing_ms", {}) if isinstance(result.get("timing_ms", {}), dict) else {}
                            except Exception:
                                transcript = ""
                                timing_ms = {}
                        await self._send_json(
                            websocket,
                            {
                                "type": "interrupt_probe_result",
                                "text": transcript,
                                "timing_ms": timing_ms,
                            },
                        )
                        continue

                    if event_type == "start_turn":
                        await cancel_partial_loop()
                        audio_buffer.clear()
                        current_turn_id += 1
                        last_partial_text = ""
                        active_remote_state = None
                        session_id = self._normalize_string(payload.get("session_id"))
                        synthesize_speech = bool(payload.get("synthesize_speech", True))
                        response_mode = self._normalize_response_mode(payload.get("response_mode"))
                        turn_open = True
                        partial_task = asyncio.create_task(partial_loop(current_turn_id))
                        await self._send_json(
                            websocket,
                            {
                                "type": "state",
                                "state": "listening",
                                "detail": "Listening for speech.",
                                "turn_id": current_turn_id,
                                "job": active_remote_state,
                            },
                        )
                        continue

                    if event_type == "cancel_turn":
                        turn_open = False
                        audio_buffer.clear()
                        await cancel_partial_loop()
                        await cancel_active_remote_turn("cancelled-by-client")
                        await self._send_json(websocket, {"type": "state", "state": "idle", "detail": "Turn cancelled."})
                        continue

                    if event_type == "interrupt":
                        turn_open = False
                        audio_buffer.clear()
                        await cancel_partial_loop()
                        await cancel_active_remote_turn("barge-in")
                        await self._send_json(websocket, {"type": "state", "state": "interrupted", "detail": "Interrupted by new speech."})
                        continue

                    if event_type == "end_turn":
                        turn_open = False
                        await cancel_partial_loop()
                        if not audio_buffer:
                            await self._send_json(websocket, {"type": "state", "state": "idle", "detail": "No audio captured for that turn."})
                            continue
                        try:
                            remote_job = await asyncio.to_thread(
                                self._job_starter,
                                pcm_bytes=bytes(audio_buffer),
                                session_id=session_id,
                                synthesize_speech=synthesize_speech,
                                response_mode=response_mode,
                                turn_id=None,
                            )
                        except Exception as exc:
                            audio_buffer.clear()
                            await self._send_json(websocket, {"type": "error", "detail": f"Failed to start live turn: {exc}"})
                            await self._send_json(websocket, {"type": "state", "state": "failed", "detail": "Failed to start live turn."})
                            continue
                        audio_buffer.clear()
                        active_remote_turn_id = str(remote_job.get("turn_id", current_turn_id))
                        active_remote_state = remote_job
                        await self._send_json(
                            websocket,
                            {
                                "type": "state",
                                "state": str(remote_job.get("status", "queued")),
                                "detail": self._state_detail(str(remote_job.get("status", "queued"))),
                                "turn_id": active_remote_turn_id,
                                "job": remote_job,
                            },
                        )
                        await start_polling(active_remote_turn_id)
                        continue

                if poll_task is not None and poll_task in done:
                    try:
                        poll_task.result()
                    except asyncio.CancelledError:
                        pass
                    poll_task = None
        finally:
            receive_task.cancel()
            await cancel_partial_loop()
            await cancel_polling()
            await cancel_active_remote_turn("websocket-closed", notify_client=False)

    def _state_detail(self, state: str) -> str:
        mapping = {
            "queued": "Queued for processing.",
            "running": "Transcribing and generating a reply.",
            "speaking": "Synthesizing speech.",
            "completed": "Live turn completed.",
            "cancelled": "Live turn cancelled.",
            "failed": "Live turn failed.",
        }
        return mapping.get(state, "Live voice state updated.")

    def _parse_message(self, raw_text: str) -> dict:
        try:
            payload = json.loads(raw_text)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _normalize_string(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_response_mode(self, value: object) -> str:
        if not isinstance(value, str):
            return "simple_chunked"
        normalized = value.strip().lower()
        if normalized == "incremental_experimental":
            return normalized
        return "simple_chunked"

    async def _send_json(self, websocket: WebSocket, payload: dict) -> None:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
