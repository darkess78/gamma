from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from ...errors import GammaError
from .client import GammaStreamClient
from .models import TwitchReplayEvent
from .normalize import normalize_replay_event


def replay_jsonl(
    path: Path,
    *,
    client: GammaStreamClient | None = None,
    owner_user_id: str | None = None,
    synthesize_speech: bool = False,
    fast_mode: bool = True,
    session_id: str | None = "twitch-replay",
) -> list[dict[str, Any]]:
    stream_client = client or GammaStreamClient()
    results: list[dict[str, Any]] = []
    for raw in iter_replay_events(path):
        event = TwitchReplayEvent.model_validate(raw)
        stream_event = normalize_replay_event(event, owner_user_id=owner_user_id, session_id=session_id)
        results.append(
            stream_client.post_event(
                stream_event,
                synthesize_speech=synthesize_speech,
                fast_mode=fast_mode,
            )
        )
    return results


def iter_replay_events(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GammaError(f"invalid replay JSON on line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise GammaError(f"invalid replay event on line {line_number}: expected object")
            yield payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Twitch-style JSONL events into the Gamma stream API.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--owner-user-id", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--synthesize-speech", action="store_true")
    parser.add_argument("--slow-mode", action="store_true", help="Send fast_mode=false to the stream API.")
    args = parser.parse_args()
    client = GammaStreamClient(base_url=args.base_url)
    results = replay_jsonl(
        args.path,
        client=client,
        owner_user_id=args.owner_user_id,
        synthesize_speech=args.synthesize_speech,
        fast_mode=not args.slow_mode,
    )
    print(json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()

