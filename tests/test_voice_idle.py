from __future__ import annotations

import pytest

from gamma.config import settings
from gamma.supervisor import voice_idle
from gamma.supervisor.voice_idle import VoiceIdleUnloader, ensure_voice_sidecar


class FakeSidecars:
    def __init__(self, *, up: bool = True) -> None:
        self.up = {kind: up for kind in voice_idle.SIDECARS}
        self.calls: list[tuple[str, str]] = []

    def endpoint(self, kind: str) -> str:
        return f"http://127.0.0.1:1/{kind}"

    def healthy(self, endpoint: str) -> bool:
        return self.up[endpoint.rsplit("/", 1)[1]]

    def start(self, kind: str) -> dict:
        self.calls.append(("start", kind))
        self.up[kind] = True
        return {"status": "ok"}

    def stop(self, kind: str) -> dict:
        self.calls.append(("stop", kind))
        self.up[kind] = False
        return {"status": "ok"}


def _unloader(fake: FakeSidecars, *, mode: list[str], now: list[float], last: list[float | None]):
    return VoiceIdleUnloader(
        presence_mode=lambda: mode[0],
        last_activity=lambda: last[0],
        healthy=fake.healthy,
        endpoint=fake.endpoint,
        start=fake.start,
        stop=fake.stop,
        clock=lambda: now[0],
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "voice_idle_unload_enabled", True)
    monkeypatch.setattr(settings, "voice_idle_unload_minutes", 20)
    monkeypatch.setattr(settings, "voice_sleep_unload_minutes", 5)
    voice_idle._healthy_at.clear()


def test_asleep_unloads_after_sleep_grace_only():
    fake, mode, now, last = FakeSidecars(), ["sleep"], [1000.0], [1000.0]
    unloader = _unloader(fake, mode=mode, now=now, last=last)
    now[0] = 1000 + 4 * 60
    assert unloader.tick() == []
    now[0] = 1000 + 5 * 60
    assert unloader.tick() == [("unload", "tts"), ("unload", "audio-understanding")]
    now[0] += 600
    assert unloader.tick() == []  # already down: nothing repeated


def test_awake_waits_for_the_longer_idle_window():
    fake, mode, now, last = FakeSidecars(), ["wake"], [0.0], [0.0]
    unloader = _unloader(fake, mode=mode, now=now, last=last)
    now[0] = 19 * 60
    assert unloader.tick() == []
    last[0] = 18 * 60  # she spoke recently
    now[0] = 30 * 60
    assert unloader.tick() == []
    now[0] = 38 * 60
    assert [a for a, _ in unloader.tick()] == ["unload", "unload"]


def test_waking_preloads_unloaded_sidecars_and_restarts_the_clock():
    fake, mode, now, last = FakeSidecars(up=False), ["sleep"], [0.0], [None]
    unloader = _unloader(fake, mode=mode, now=now, last=last)
    unloader.tick()
    mode[0] = "wake"
    now[0] = 10_000.0
    assert unloader.tick() == [("preload", "tts"), ("preload", "audio-understanding")]
    now[0] = 10_000 + 60
    assert unloader.tick() == []  # old inactivity does not count against a fresh wake


def test_unmanaged_endpoints_are_left_alone():
    fake, mode, now, last = FakeSidecars(), ["sleep"], [10_000.0], [0.0]
    unloader = _unloader(fake, mode=mode, now=now, last=last)
    unloader._endpoint = lambda kind: None
    assert unloader.tick() == []
    assert fake.calls == []


def test_ensure_starts_a_down_sidecar_only_under_a_lifecycle_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_idle, "activity_path", lambda: tmp_path / "touch")
    monkeypatch.setattr(voice_idle, "_last_touch", 0.0)
    monkeypatch.setattr(voice_idle, "sidecar_endpoint", lambda kind: "http://127.0.0.1:1/tts")
    fake = FakeSidecars(up=False)
    healthy = lambda endpoint: fake.up["tts"]

    monkeypatch.delenv("GAMMA_LIFECYCLE_MANAGER", raising=False)
    assert ensure_voice_sidecar("tts", healthy=healthy, start=fake.start) is False
    assert fake.calls == []
    assert (tmp_path / "touch").exists()  # activity still counts

    monkeypatch.setenv("GAMMA_LIFECYCLE_MANAGER", "systemd-user")
    assert ensure_voice_sidecar("tts", healthy=healthy, start=fake.start) is True
    assert fake.calls == [("start", "tts")]
    assert ensure_voice_sidecar("tts", healthy=healthy, start=fake.start) is False  # healthy, cached
