"""Unload the GPU voice sidecars when Shana is asleep or idle, and bring them back on demand.

Resource routing (``gamma.resources``) decides *where* a sidecar loads; this decides *when*.
Starting always goes through the supervisor's dependency actions, so GPU admission and
allocation logging apply to every reload.

- Any process that is about to use a sidecar calls :func:`ensure_voice_sidecar` first. It records
  voice activity (a shared file, so the dashboard and workers count too) and starts the sidecar
  if it is down.
- The Shana API runs :class:`VoiceIdleUnloader`. It stops the sidecars after
  ``voice_sleep_unload_minutes`` without voice activity while presence is ``sleep``, or after
  ``voice_idle_unload_minutes`` while awake, and preloads them when presence leaves ``sleep``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..config import settings
from ..observability import configure_logging, log_event

SIDECARS = ("tts", "audio-understanding")
_ACTIVITY_TOUCH_INTERVAL = 5.0
_HEALTH_CACHE_SECONDS = 20.0

_logger = configure_logging("voice_idle")
_locks = {kind: threading.Lock() for kind in SIDECARS}
_healthy_at: dict[str, float] = {}
_last_touch = 0.0


def idle_management_active() -> bool:
    """Only processes run by a lifecycle manager (the live systemd units) start or stop sidecars."""
    return settings.voice_idle_unload_enabled and bool(os.getenv("GAMMA_LIFECYCLE_MANAGER", "").strip())


def activity_path() -> Path:
    return settings.data_dir / "runtime" / "voice_activity.touch"


def mark_voice_activity(*, now: float | None = None) -> None:
    global _last_touch
    current = time.time() if now is None else now
    if current - _last_touch < _ACTIVITY_TOUCH_INTERVAL:
        return
    _last_touch = current
    path = activity_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass


def last_voice_activity() -> float | None:
    try:
        return activity_path().stat().st_mtime
    except OSError:
        return None


def _manager():
    from .manager import ProcessManager

    return ProcessManager()


def sidecar_endpoint(kind: str) -> str | None:
    """The local endpoint of a managed sidecar, or None when it is not ours to manage."""
    if kind == "tts":
        from ..voice.voice_profiles import resolve_tts_config

        cfg = resolve_tts_config()
        if cfg.provider.strip().lower() not in {"qwen-tts", "qwen_tts", "qwen", "qwentts"}:
            return None
        endpoint = cfg.qwen_tts_endpoint
    elif kind == "audio-understanding":
        if not settings.audio_understanding_enabled:
            return None
        endpoint = settings.audio_understanding_endpoint
    else:
        raise ValueError(kind)
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return None
    from urllib.parse import urlparse

    return endpoint if urlparse(endpoint).hostname in {"127.0.0.1", "localhost"} else None


def sidecar_healthy(endpoint: str) -> bool:
    from .sidecars import SidecarsMixin

    url = SidecarsMixin._health_url(endpoint)
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _start(kind: str) -> dict[str, Any]:
    manager = _manager()
    if kind == "tts":
        return manager._tts_dependency_action("start")
    return manager._audio_understanding_dependency_action("start")


def _stop(kind: str) -> dict[str, Any]:
    manager = _manager()
    if kind == "tts":
        return manager._tts_dependency_action("stop")
    return manager._audio_understanding_dependency_action("stop")


def ensure_voice_sidecar(
    kind: str,
    *,
    healthy: Callable[[str], bool] = sidecar_healthy,
    start: Callable[[str], dict[str, Any]] = _start,
) -> bool:
    """Record voice activity and start ``kind`` if it is down. Returns whether a start was attempted."""
    mark_voice_activity()
    if not idle_management_active():
        return False
    endpoint = sidecar_endpoint(kind)
    if not endpoint:
        return False
    if time.monotonic() - _healthy_at.get(kind, -1e9) < _HEALTH_CACHE_SECONDS:
        return False
    with _locks[kind]:
        if healthy(endpoint):
            _healthy_at[kind] = time.monotonic()
            return False
        started_at = time.perf_counter()
        result = start(kind)
        ok = result.get("status") == "ok"
        if ok:
            _healthy_at[kind] = time.monotonic()
        log_event(
            _logger,
            logging.INFO if ok else logging.ERROR,
            "voice_idle.sidecar.started",
            "Started an unloaded voice sidecar on demand.",
            sidecar=kind,
            status=result.get("status"),
            detail=str(result.get("detail") or "")[:300],
            duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
        )
        return True


def forget_health(kind: str) -> None:
    _healthy_at.pop(kind, None)


class VoiceIdleUnloader:
    """Background loop in the Shana API process that unloads and preloads the voice sidecars."""

    def __init__(
        self,
        *,
        presence_mode: Callable[[], str] | None = None,
        last_activity: Callable[[], float | None] = last_voice_activity,
        healthy: Callable[[str], bool] = sidecar_healthy,
        endpoint: Callable[[str], str | None] = sidecar_endpoint,
        start: Callable[[str], dict[str, Any]] = _start,
        stop: Callable[[str], dict[str, Any]] = _stop,
        clock: Callable[[], float] = time.time,
        interval_seconds: float = 60.0,
    ) -> None:
        self._presence_mode = presence_mode or _current_presence_mode
        self._last_activity = last_activity
        self._healthy = healthy
        self._endpoint = endpoint
        self._start = start
        self._stop = stop
        self._clock = clock
        self._interval = interval_seconds
        self._baseline = clock()
        self._previous_mode: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not idle_management_active() or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="voice-idle-unloader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self.tick()
            except Exception:
                _logger.exception("voice idle unloader tick failed")

    def tick(self) -> list[tuple[str, str]]:
        """One decision pass. Returns the (action, sidecar) pairs it carried out."""
        now = self._clock()
        mode = (self._presence_mode() or "sleep").strip().lower()
        actions: list[tuple[str, str]] = []
        woke = self._previous_mode == "sleep" and mode != "sleep"
        self._previous_mode = mode
        if woke:
            self._baseline = now
            for kind in SIDECARS:
                endpoint = self._endpoint(kind)
                if endpoint and not self._healthy(endpoint):
                    result = self._start(kind)
                    actions.append(("preload", kind))
                    self._log("voice_idle.sidecar.preloaded", kind, result, mode=mode)
            return actions

        last = max(self._last_activity() or 0.0, self._baseline)
        minutes = settings.voice_sleep_unload_minutes if mode == "sleep" else settings.voice_idle_unload_minutes
        if now - last < max(0, minutes) * 60:
            return actions
        for kind in SIDECARS:
            endpoint = self._endpoint(kind)
            if endpoint and self._healthy(endpoint):
                result = self._stop(kind)
                forget_health(kind)
                actions.append(("unload", kind))
                self._log(
                    "voice_idle.sidecar.unloaded", kind, result, mode=mode, idle_minutes=round((now - last) / 60, 1)
                )
        return actions

    @staticmethod
    def _log(event: str, kind: str, result: dict[str, Any], **fields: Any) -> None:
        log_event(
            _logger,
            logging.INFO if result.get("status") == "ok" else logging.ERROR,
            event,
            "Voice sidecar idle management.",
            sidecar=kind,
            status=result.get("status"),
            detail=str(result.get("detail") or "")[:300],
            **fields,
        )


def _current_presence_mode() -> str:
    from ..presence import load_presence_state

    return str(load_presence_state().get("mode") or "sleep")
