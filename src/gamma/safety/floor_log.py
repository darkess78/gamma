"""Admin-only record of what the safety floor stopped.

The floor keeps a stopped line out of everything Shana can read back (replies, history, memory,
working state). The owner still needs to see what happened, so each stop is appended here,
under ``data/safety/``, which no prompt or memory path reads.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings

_lock = threading.Lock()
_MAX_TEXT = 2000


def floor_log_path() -> Path:
    return settings.data_dir / "safety" / "floor_events.jsonl"


def record_floor_event(*, text: str, rules: list[str], layers: list[str], source: str = "",
                       session_id: str | None = None) -> None:
    entry: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source or "speech",
        "session_id": session_id or "",
        "layers": list(layers),
        "rules": [str(rule)[:120] for rule in rules][:10],
        "text": (text or "")[:_MAX_TEXT],
    }
    path = floor_log_path()
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_floor_events(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first."""
    try:
        lines = floor_log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(events) >= max(1, limit):
            break
    return events
