from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import settings
from .models import StreamTurnResult, utc_now


class StreamTraceStore:
    """Append-only replay trace for stream policy and generation decisions."""

    rotate_bytes = 10 * 1024 * 1024

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.data_dir / "runtime" / "stream_traces" / "current.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result: StreamTurnResult) -> None:
        self._rotate_if_needed()
        payload = {
            "recorded_at": utc_now(),
            "trace_id": result.trace_id,
            "input_event": result.input_event.model_dump(),
            "decision": result.decision.model_dump(),
            "safety_decision": result.safety_decision,
            "action_plan": result.action_plan.model_dump(),
            "assistant_response": result.assistant_response.model_dump() if result.assistant_response else None,
            "output_events": [event.model_dump() for event in result.output_events],
            "output_dispatch": result.output_dispatch,
            "timing_ms": result.timing_ms,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

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

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.rotate_bytes:
            return
        rotated = self.path.with_name(f"{self.path.stem}.{utc_now().replace(':', '').replace('-', '')}.jsonl")
        suffix = 1
        while rotated.exists():
            rotated = self.path.with_name(f"{self.path.stem}.{utc_now().replace(':', '').replace('-', '')}.{suffix}.jsonl")
            suffix += 1
        self.path.replace(rotated)
