from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import settings
from .models import (
    DASHBOARD_MONITOR_TARGET,
    DEFAULT_TARGET_POLICY,
    KNOWN_TARGET_POLICIES,
    STREAM_PUBLIC_TARGET,
    PerformerOutputEvent,
)
from .turns import SpokenTurnStore


@dataclass(slots=True)
class _Subscriber:
    subscriber_id: str
    target_policy: str
    client_name: str
    client_host: str | None
    connected_at: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


class PerformerEventBus:
    def __init__(
        self,
        *,
        history_limit: int = 200,
        subscriber_queue_size: int = 100,
        state_path: Path | None = None,
        turn_store: SpokenTurnStore | None = None,
    ) -> None:
        self._history: deque[PerformerOutputEvent] = deque(maxlen=max(1, history_limit))
        self._subscribers: dict[str, _Subscriber] = {}
        self._state_path = state_path
        self._muted_targets: set[str] = self._load_muted_targets()
        self._subscriber_queue_size = max(1, subscriber_queue_size)
        self._next_sequence = 1
        self._turn_store = turn_store or SpokenTurnStore()
        self._lock = threading.Lock()

    def publish(self, event: PerformerOutputEvent) -> None:
        with self._lock:
            if event.sequence is None:
                event.sequence = self._next_sequence
                self._next_sequence += 1
            elif event.sequence >= self._next_sequence:
                self._next_sequence = event.sequence + 1
            self._turn_store.apply_event(event)
            self._history.append(event)
            subscribers = [
                subscriber
                for subscriber in self._subscribers.values()
                if _subscriber_receives_event(subscriber.target_policy, event.target_policy)
                and _event_allowed_by_muted_targets(subscriber.target_policy, event, self._muted_targets)
            ]
        payload = event.model_dump()
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._put_nowait, subscriber.queue, payload)

    def recent(
        self,
        *,
        limit: int = 50,
        target_policy: str | None = None,
        after_sequence: int | None = None,
    ) -> list[PerformerOutputEvent]:
        with self._lock:
            items = list(self._history)
            muted_targets = set(self._muted_targets)
        after_sequence = _normalize_after_sequence(after_sequence)
        if after_sequence is not None:
            items = [event for event in items if (event.sequence or 0) > after_sequence]
        if target_policy:
            subscriber_target = _normalize_target_policy(target_policy)
            items = [
                event
                for event in items
                if _subscriber_receives_event(subscriber_target, event.target_policy)
                and _event_allowed_by_muted_targets(subscriber_target, event, muted_targets)
            ]
        return items[-max(1, limit):]

    async def subscribe(
        self,
        *,
        replay_recent: int = 0,
        after_sequence: int | None = None,
        target_policy: str = "stream_public",
        client_name: str | None = None,
        client_host: str | None = None,
    ) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        subscriber_id = uuid4().hex
        subscriber_target = _normalize_target_policy(target_policy)
        after_sequence = _normalize_after_sequence(after_sequence)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        subscriber = _Subscriber(
            subscriber_id=subscriber_id,
            target_policy=subscriber_target,
            client_name=_normalize_client_name(client_name),
            client_host=str(client_host or "").strip() or None,
            connected_at=_utc_now(),
            loop=asyncio.get_running_loop(),
            queue=queue,
        )
        with self._lock:
            self._subscribers[subscriber_id] = subscriber
            recent = list(self._history)
            muted_targets = set(self._muted_targets)
        if replay_recent > 0:
            if after_sequence is not None:
                recent = [event for event in recent if (event.sequence or 0) > after_sequence]
            recent = [
                event
                for event in recent
                if _subscriber_receives_event(subscriber_target, event.target_policy)
                and _event_allowed_by_muted_targets(subscriber_target, event, muted_targets)
            ][-max(0, replay_recent):]
        else:
            recent = []
        for event in recent:
            self._put_nowait(queue, event.model_dump())
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def set_target_muted(self, target_policy: str, muted: bool, *, reason: str = "operator") -> dict[str, Any]:
        target = _normalize_target_policy(target_policy)
        with self._lock:
            if muted:
                self._muted_targets.add(target)
            else:
                self._muted_targets.discard(target)
            self._save_state_locked()
        event_type = "output_cleared" if muted else "target_mute_changed"
        self.publish(
            PerformerOutputEvent(
                type=event_type,  # type: ignore[arg-type]
                turn_id=f"target-mute-{uuid4().hex}",
                source="performer_control",
                target_policy=target,
                payload={"target_policy": target, "muted": muted, "reason": reason},
            )
        )
        return {"ok": True, "target_policy": target, "muted": muted, "stats": self.stats()}

    def clear_target(self, target_policy: str, *, reason: str = "operator") -> dict[str, Any]:
        target = _normalize_target_policy(target_policy)
        self.publish(
            PerformerOutputEvent(
                type="output_cleared",
                turn_id=f"target-clear-{uuid4().hex}",
                source="performer_control",
                target_policy=target,
                payload={"target_policy": target, "reason": reason},
            )
        )
        return {"ok": True, "target_policy": target, "cleared": True, "stats": self.stats()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            subscribers_by_target: dict[str, int] = {}
            subscribers: list[dict[str, str | None]] = []
            for subscriber in self._subscribers.values():
                subscribers_by_target[subscriber.target_policy] = subscribers_by_target.get(subscriber.target_policy, 0) + 1
                subscribers.append(
                    {
                        "subscriber_id": subscriber.subscriber_id,
                        "target_policy": subscriber.target_policy,
                        "client_name": subscriber.client_name,
                        "client_host": subscriber.client_host,
                        "connected_at": subscriber.connected_at,
                    }
                )
            return {
                "subscriber_count": len(self._subscribers),
                "history_count": len(self._history),
                "last_sequence": self._next_sequence - 1,
                "target_policies": list(KNOWN_TARGET_POLICIES),
                "subscribers_by_target": subscribers_by_target,
                "subscribers": subscribers,
                "muted_targets": sorted(self._muted_targets),
            }

    def replay_window(self) -> dict[str, int | None]:
        with self._lock:
            first_sequence = self._history[0].sequence if self._history else None
            last_sequence = self._next_sequence - 1
        return {"first_sequence": first_sequence, "last_sequence": last_sequence}

    def replay_gap_after(self, after_sequence: int | None) -> bool:
        after_sequence = _normalize_after_sequence(after_sequence)
        if after_sequence is None:
            return False
        window = self.replay_window()
        first_sequence = window["first_sequence"]
        if first_sequence is None:
            return False
        return after_sequence < first_sequence - 1

    def recent_turns(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return [turn.model_dump() for turn in self._turn_store.recent(limit=limit)]

    @staticmethod
    def _put_nowait(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def _load_muted_targets(self) -> set[str]:
        if self._state_path is None:
            return set()
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(payload, dict):
            return set()
        raw_targets = payload.get("muted_targets", [])
        if not isinstance(raw_targets, list):
            return set()
        return {_normalize_target_policy(item) for item in raw_targets if str(item or "").strip()}

    def _save_state_locked(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "updated_at": _utc_now(),
            "muted_targets": sorted(self._muted_targets),
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self._state_path)

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_target_policy(target_policy: str | None) -> str:
    value = str(target_policy or "").strip().lower()
    return value or DEFAULT_TARGET_POLICY


def _normalize_client_name(client_name: str | None) -> str:
    value = str(client_name or "").strip().lower()
    return value[:80] or "unknown_client"


def _normalize_after_sequence(after_sequence: int | None) -> int | None:
    if after_sequence is None:
        return None
    return max(0, int(after_sequence))


def _subscriber_receives_event(subscriber_target: str, event_target: str) -> bool:
    subscriber_target = _normalize_target_policy(subscriber_target)
    event_target = _normalize_target_policy(event_target)
    if subscriber_target == event_target:
        return True
    if subscriber_target == DASHBOARD_MONITOR_TARGET:
        return event_target in {STREAM_PUBLIC_TARGET, DASHBOARD_MONITOR_TARGET}
    return False


def _event_allowed_by_muted_targets(subscriber_target: str, event: PerformerOutputEvent, muted_targets: set[str]) -> bool:
    event_target = _normalize_target_policy(event.target_policy)
    if event_target not in muted_targets:
        return True
    if event.type in {"output_cleared", "target_mute_changed"}:
        return True
    return _normalize_target_policy(subscriber_target) != event_target


_BUS = PerformerEventBus(state_path=settings.data_dir / "runtime" / "performer" / "state.json")


def get_performer_event_bus() -> PerformerEventBus:
    return _BUS
