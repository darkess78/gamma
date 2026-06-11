from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..avatar_events.models import AvatarEvent
from ..config import settings
from ..performer.bus import PerformerEventBus, get_performer_event_bus
from ..performer.models import performer_event_from_stream_output
from .models import StreamOutputEvent, utc_now


class OutputDispatchRecord(BaseModel):
    """Output dispatch record.
    
    Attributes:
        adapter: Adapter name.
        ok: Success status.
        output_event_id: Output event ID.
        event_type: Event type.
        detail: Error detail (optional).
        metadata: Optional metadata.
    """
    adapter: str
    ok: bool
    output_event_id: str
    event_type: str
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutputDispatchResult(BaseModel):
    """Output dispatch result.
    
    Attributes:
        records: Dispatch records.
    """
    records: list[OutputDispatchRecord] = Field(default_factory=list)


class StreamOutputAdapter(ABC):
    """Stream output adapter.
    
    Attributes:
        name: Adapter name.
    
    Methods:
        handle: Handle output event.
    """
    name: str

    @abstractmethod
    def handle(self, event: StreamOutputEvent) -> OutputDispatchRecord:
        """Handle output event.
        
        Args:
            event: Output event.
        
        Returns:
            OutputDispatchRecord: Dispatch record.
        """
        raise NotImplementedError


class StreamOutputDispatcher:
    """Stream output dispatcher.
    
    Attributes:
        _adapters: Output adapters.
    
    Methods:
        __init__: Initialize dispatcher.
        dispatch: Dispatch events.
    """

    def __init__(self, adapters: list[StreamOutputAdapter] | None = None) -> None:
        """Initialize dispatcher.
        
        Args:
            adapters: Output adapters (default: jsonl + performer bus).
        """
        self._adapters = adapters if adapters is not None else [JsonlStreamOutputAdapter(), PerformerBusOutputAdapter()]

    def dispatch(self, events: list[StreamOutputEvent]) -> OutputDispatchResult:
        """Dispatch events.
        
        Args:
            events: Output events.
        
        Returns:
            OutputDispatchResult: Dispatch result.
        """
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
        """Read recent outputs.
        
        Args:
            limit: Max items to read (default 50).
        
        Returns:
            list[dict[str, Any]]: Recent output items as dicts.
        """
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
        """Build adapter payload from output event.
        
        Args:
            event: Output event.
        
        Returns:
            dict[str, Any]: Adapter payload dict.
        """
        if event.type == "emotion_changed":
            return AvatarEvent(event_type="emotion_changed", payload=dict(event.payload)).model_dump()
        if event.type == "avatar_motion":
            return AvatarEvent(event_type="motion", payload=dict(event.payload)).model_dump()
        if event.type == "subtitle_line":
            return {"subtitle": event.payload.get("text", ""), "clear": bool(event.payload.get("clear", False))}
        if event.type == "speech_ended":
            return {"speech": "ended", **dict(event.payload)}
        if event.type == "overlay_update":
            return {"overlay": dict(event.payload)}
        return dict(event.payload)

    def _rotate_if_needed(self) -> None:
        """Rotate output file if size exceeds threshold.
        
        Returns:
            None.
        """
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
    """Stream output log service.
    
    Attributes:
        _adapter: JSONL output adapter.
    
    Methods:
        __init__: Initialize log service.
        recent_outputs: Get recent outputs.
    """

    def __init__(self, adapter: JsonlStreamOutputAdapter | None = None) -> None:
        """Initialize log service.
        
        Args:
            adapter: JSONL adapter (default: create new).
        """
        self._adapter = adapter or JsonlStreamOutputAdapter()

    def recent_outputs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent outputs.
        
        Args:
            limit: Max items to return (default 50).
        
        Returns:
            list[dict[str, Any]]: Recent output items.
        """
        return self._adapter.read_recent(limit=limit)


class PerformerBusOutputAdapter(StreamOutputAdapter):
    """Performer bus output adapter.
    
    Attributes:
        name: Adapter name.
        _bus: Performer event bus.
    
    Methods:
        __init__: Initialize adapter.
        handle: Handle output event.
    """
    name = "performer_event_bus"

    def __init__(self, bus: PerformerEventBus | None = None) -> None:
        """Initialize adapter.
        
        Args:
            bus: Performer event bus (default: global instance).
        """
        self._bus = bus or get_performer_event_bus()

    def handle(self, event: StreamOutputEvent) -> OutputDispatchRecord:
        """Handle output event.
        
        Args:
            event: Output event.
        
        Returns:
            OutputDispatchRecord: Dispatch record.
        """
        performer_event = performer_event_from_stream_output(event)
        if performer_event is None:
            return OutputDispatchRecord(
                adapter=self.name,
                ok=True,
                output_event_id=event.output_event_id,
                event_type=event.type,
                detail="ignored: no performer event mapping",
            )
        self._bus.publish(performer_event)
        return OutputDispatchRecord(
            adapter=self.name,
            ok=True,
            output_event_id=event.output_event_id,
            event_type=event.type,
            metadata={"performer_event_id": performer_event.event_id, "performer_event_type": performer_event.type},
        )
