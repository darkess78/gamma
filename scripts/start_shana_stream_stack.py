from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from gamma.config import settings
from gamma.dashboard.service import DashboardService

from start_shana_voice_stack import start_voice_stack


def _print_step(message: str) -> None:
    print(f"[stream-start] {message}", flush=True)


def _probe_dashboard() -> dict[str, object]:
    host = "127.0.0.1" if settings.dashboard_bind_host in {"0.0.0.0", "::"} else settings.dashboard_bind_host
    url = f"http://{host}:{settings.dashboard_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return {"ok": response.status < 500, "detail": f"HTTP {response.status}"}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "detail": str(exc)}


def _wait_for_dashboard(timeout_seconds: int = 30) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] = {"ok": False, "detail": "not checked"}
    while time.monotonic() < deadline:
        last = _probe_dashboard()
        if last.get("ok"):
            return last
        time.sleep(1)
    return last


def main() -> int:
    _print_step("Starting Shana voice stack first.")
    voice = start_voice_stack()

    dashboard_service = DashboardService()

    dashboard = voice.get("dashboard", {})
    dashboard_health = voice.get("dashboard_health", _wait_for_dashboard())

    _print_step("Starting Twitch IRC worker.")
    twitch_worker = dashboard_service.start_twitch_worker()

    _print_step("Starting Twitch EventSub worker.")
    twitch_eventsub = dashboard_service.start_twitch_eventsub_worker()

    stream_ready = dashboard_service.stream_ready_status()
    result = {
        "ok": (
            bool(voice.get("ok"))
            and bool(dashboard.get("ok"))
            and bool(twitch_worker.get("ok"))
            and bool(twitch_eventsub.get("ok"))
        ),
        "voice_stack": voice,
        "dashboard": dashboard,
        "dashboard_health": dashboard_health,
        "twitch_worker": twitch_worker,
        "twitch_eventsub": twitch_eventsub,
        "stream_ready": stream_ready,
        "urls": {
            "shana": settings.shana_base_url,
            "dashboard": settings.dashboard_base_url,
        },
    }
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
