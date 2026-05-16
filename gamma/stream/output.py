from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..avatar_events.models import AvatarEvent
from ..config import settings
from .models import StreamOutputEvent, utc_now


class OutputDispatchRecord(BaseModel):
    adapter: str
    ok: bool
    output_event_id: str
    event_type: str
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutputDispatchResult(BaseModel):
    records: list[OutputDispatchRecord] = Field(default_factory=list)


class StreamOutputAdapter(ABC):
    name: str

    @abstractmethod
    def handle(self, event: StreamOutputEvent) -> OutputDispatchRecord:
        raise NotImplementedError


class StreamOutputDispatcher:
    def __init__(self, adapters: list[StreamOutputAdapter] | None = None) -> None:
        self._adapters = adapters if adapters is not None else [JsonlStreamOutputAdapter()]

    def dispatch(self, events: list[StreamOutputEvent]) -> OutputDispatchResult:
        records: list[OutputDispatchRecord] = []
        for event in events:
            for adapter in self._adapters:
                try:
                    records.append(adapter.handle(event))
                except Exception as exc:
                    records.append(
                        OutputDispatchRecord(
                            adapter=adapter.name,
                            ok=False,
                            output_event_id=event.output_event_id,
                            event_type=event.type,
                            detail=str(exc),
                        )
                    )
        return OutputDispatchResult(records=records)


class JsonlStreamOutputAdapter(StreamOutputAdapter):
    name = "jsonl_stream_output"
    rotate_bytes = 10 * 1024 * 1024

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.data_dir / "runtime" / "stream_outputs" / "current.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def handle(self, event: StreamOutputEvent) -> OutputDispatchRecord:
        self._rotate_if_needed()
        payload = {
            "recorded_at": utc_now(),
            "output_event": event.model_dump(),
            "adapter_payload": self._adapter_payload(event),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return OutputDispatchRecord(
            adapter=self.name,
            ok=True,
            output_event_id=event.output_event_id,
            event_type=event.type,
            metadata={"path": str(self.path)},
        )

    def read_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    items.append(item)
        return items[-max(1, limit):]

    def _adapter_payload(self, event: StreamOutputEvent) -> dict[str, Any]:
        if event.type == "emotion_changed":
            return AvatarEvent(event_type="emotion_changed", payload=dict(event.payload)).model_dump()
        if event.type == "avatar_motion":
            return AvatarEvent(event_type="motion", payload=dict(event.payload)).model_dump()
        if event.type == "subtitle_line":
            return {"subtitle": event.payload.get("text", "")}
        return dict(event.payload)

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.rotate_bytes:
            return
        stamp = utc_now().replace(":", "").replace("-", "")
        rotated = self.path.with_name(f"{self.path.stem}.{stamp}.jsonl")
        suffix = 1
        while rotated.exists():
            rotated = self.path.with_name(f"{self.path.stem}.{stamp}.{suffix}.jsonl")
            suffix += 1
        self.path.replace(rotated)


class StreamOutputLogService:
    def __init__(self, adapter: JsonlStreamOutputAdapter | None = None) -> None:
        self._adapter = adapter or JsonlStreamOutputAdapter()

    def recent_outputs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._adapter.read_recent(limit=limit)
