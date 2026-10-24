from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from gamma.config import settings
from gamma.integrations.discord.voice_controls import VoiceSessionController
from gamma.integrations.discord.voice_session import DiscordVoiceSession


class _AudioSource:
    instances = []

    def __init__(self, path):
        self.path = path
        self.cleaned = False
        self.instances.append(self)

    def cleanup(self):
        self.cleaned = True


class _Voice:
    def __init__(self):
        self.started = asyncio.Event()
        self.connected = True
        self.playing = False
        self.plays = 0
        self.stops = 0
        self.after = None

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def play(self, source, *, after):
        self.plays += 1
        self.playing = True
        self.after = after
        self.started.set()

    def finish(self, error=None):
        self.playing = False
        self.after(error)

    def stop(self):
        self.stops += 1
        if self.playing:
            self.finish()

    def stop_listening(self):
        pass

    async def disconnect(self, *, force):
        self.connected = False


@pytest.fixture
def playback(tmp_path, monkeypatch):
    _AudioSource.instances = []
    monkeypatch.setitem(sys.modules, "discord", SimpleNamespace(FFmpegPCMAudio=_AudioSource))
    monkeypatch.setattr(settings, "audio_output_dir", tmp_path)
    path = tmp_path / "synthetic.wav"
    path.write_bytes(b"RIFFsynthetic")
    session = DiscordVoiceSession.__new__(DiscordVoiceSession)
    session.client = SimpleNamespace()
    session.config = SimpleNamespace(owner_user_id="1")
    session.generation = 0
    session.queue = asyncio.Queue()
    session.stats = {"replies": 0}
    session.controller = VoiceSessionController(session.client, guild_id="100", channel_id="300",
                                               owner_check=lambda uid: uid == "1",
                                               voice_factory=None, sink_factory=None)
    voice = _Voice()
    session.controller._voice = voice
    session.controller._state = "listening"
    return SimpleNamespace(session=session, voice=voice, path=path)


async def _start(state):
    task = asyncio.create_task(state.session._play(str(state.path), 0))
    await asyncio.wait_for(state.voice.started.wait(), 1)
    assert state.session.stats["replies"] == 0
    return task


def _cleaned(state):
    assert state.voice.stops >= 1
    assert len(_AudioSource.instances) == 1 and _AudioSource.instances[0].cleaned


def test_successful_receipt_counts_once_even_with_duplicate_callback(playback):
    async def run():
        task = await _start(playback)
        playback.voice.finish()
        playback.voice.after(None)
        await task
        assert playback.session.stats["replies"] == 1
        _cleaned(playback)

    asyncio.run(run())


@pytest.mark.parametrize("command", ["pause", "leave"])
def test_owner_stop_invalidates_receipt_and_drains_queue(playback, command):
    async def run():
        task = await _start(playback)
        await playback.session.queue.put((0, {"fixture": True}))
        result = await playback.session.command("1", command)
        assert result["ok"] and playback.session.generation == 1
        await task
        assert playback.session.stats["replies"] == 0
        assert playback.session.queue.empty()
        await playback.session.queue.join()
        _cleaned(playback)

    asyncio.run(run())


def test_generation_change_before_callback_does_not_count(playback):
    async def run():
        task = await _start(playback)
        playback.session.generation += 1
        playback.voice.finish()
        await task
        assert playback.session.stats["replies"] == 0
        _cleaned(playback)

    asyncio.run(run())


@pytest.mark.parametrize("command", ["pause", "leave"])
@pytest.mark.parametrize("callback_thread", [False, True])
def test_completed_receipt_before_owner_stop_is_retained(playback, command, callback_thread):
    async def run():
        task = await _start(playback)
        if callback_thread:
            thread = threading.Thread(target=playback.voice.finish)
            thread.start()
            thread.join(timeout=1)
            assert not thread.is_alive()
        else:
            playback.voice.finish()
        # Do not yield to the loop between the valid receipt and owner invalidation.
        assert not task.done()
        result = await playback.session.command("1", command)
        assert result["ok"] and playback.session.generation == 1
        assert not task.done()
        await task
        assert playback.session.stats["replies"] == 1
        _cleaned(playback)

    asyncio.run(run())


def test_nonowner_pause_preserves_active_playback(playback):
    async def run():
        task = await _start(playback)
        result = await playback.session.command("2", "pause")
        assert result == {"ok": False, "reason": "owner_required"}
        assert playback.session.generation == 0 and playback.voice.playing
        playback.voice.finish()
        await task
        assert playback.session.stats["replies"] == 1
        _cleaned(playback)

    asyncio.run(run())


def test_callback_error_remains_failure_without_success_count(playback):
    async def run():
        task = await _start(playback)
        playback.voice.finish(RuntimeError("SYNTHETIC_ERROR_CANARY"))
        with pytest.raises(RuntimeError, match="^Discord playback failed$"):
            await task
        assert playback.session.stats["replies"] == 0
        _cleaned(playback)

    asyncio.run(run())


def test_timeout_keeps_cleanup_and_does_not_count(playback, monkeypatch):
    original_wait_for = asyncio.wait_for

    async def bounded_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, .01 if timeout == 60 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", bounded_wait_for)

    async def run():
        task = await _start(playback)
        with pytest.raises(TimeoutError):
            await task
        assert playback.session.stats["replies"] == 0
        _cleaned(playback)

    asyncio.run(run())


@pytest.mark.parametrize("receipt_first", [False, True])
def test_task_cancellation_preserves_cancellation_and_does_not_count(playback, receipt_first):
    async def run():
        task = await _start(playback)
        if receipt_first:
            playback.voice.finish()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert playback.session.stats["replies"] == 0
        _cleaned(playback)

    asyncio.run(run())


@pytest.mark.parametrize("reason", ["stale", "missing_voice", "disconnected", "busy", "missing_file", "empty", "outside", "symlink"])
def test_no_start_does_not_count_or_construct_source(playback, tmp_path, reason):
    async def run():
        path = str(playback.path)
        if reason == "stale":
            playback.session.generation = 1
        elif reason == "missing_voice":
            playback.session.controller._voice = None
        elif reason == "disconnected":
            playback.voice.connected = False
        elif reason == "busy":
            playback.voice.playing = True
        elif reason == "missing_file":
            path = str(tmp_path / "missing.wav")
        elif reason == "empty":
            path = ""
        elif reason == "outside":
            subdir = tmp_path / "other"
            subdir.mkdir()
            outside = subdir / "outside.wav"
            outside.write_bytes(b"RIFFsynthetic")
            path = str(outside)
        elif reason == "symlink":
            link = tmp_path / "linked.wav"
            link.symlink_to(playback.path)
            path = str(link)
        await playback.session._play(path, 0)
        assert playback.session.stats["replies"] == playback.voice.plays == 0
        assert _AudioSource.instances == []

    asyncio.run(run())
