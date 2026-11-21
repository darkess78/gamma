"""Opt-in stage timestamps on the host monotonic clock, without turn content."""
import os
import sys
import time


def mark(stage: str) -> None:
    if os.environ.get("SHANA_VOICE_LATENCY_TRACE") == "1":
        print(f"voice_latency {os.getpid()} {time.monotonic_ns()} {stage}", file=sys.stderr, flush=True)
