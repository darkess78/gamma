"""A loaded standby worker takes the next live voice turn; a stale or busy one never does."""
import argparse
import io
import json
import subprocess
import sys
import time

import pytest

from gamma import run_live_voice_worker as worker
from gamma.config import settings
from gamma.voice.live_jobs import LiveVoiceJob, LiveVoiceJobManager

# Stands in for the real worker: waits for one job line, then writes it where the turn's output goes.
FAKE_STANDBY = (
    "import json,sys\n"
    "print('standby ready', flush=True)\n"
    "line=sys.stdin.readline()\n"
    "if not line.strip(): sys.exit(0)\n"
    "job=json.loads(line)\n"
    "open(job['output_path'],'w').write(line)\n"
)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "live_voice_standby_worker", True)
    monkeypatch.setattr(settings, "live_voice_standby_max_age_seconds", 300)
    m = LiveVoiceJobManager(performer_bus=object())
    monkeypatch.setattr(m, "_standby_command", lambda: [sys.executable, "-c", FAKE_STANDBY])
    monkeypatch.setattr(m, "_standby_key", lambda: "same")
    yield m
    if m._standby is not None:
        m._discard_standby(m._standby)


def spawn(m, tmp_path, turn="t1"):
    paths = {k: tmp_path / f"{turn}.{k}" for k in ("wav", "out", "status", "stdout", "stderr")}
    process = m._spawn_worker(turn_id=turn, input_path=paths["wav"], output_path=paths["out"], status_path=paths["status"],
                              session_id="s", synthesize_speech=True, response_mode="simple_chunked",
                              stdout_log=paths["stdout"], stderr_log=paths["stderr"], owner_verified=True)
    return process, paths


def test_standby_takes_the_next_turn(manager, tmp_path, monkeypatch):
    manager._ensure_standby()
    standby = manager._standby
    assert standby is not None
    cold = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: cold.append(a))  # a cold spawn would be a bug here
    process, paths = spawn(manager, tmp_path)
    assert process is standby.process and not cold and manager._standby is None
    process.wait(timeout=10)
    job = json.loads(paths["out"].read_text())
    assert job["turn_id"] == "t1" and job["owner_verified"] is True and job["session_id"] == "s"
    assert "standby ready" in paths["stdout"].read_text()  # its log became the turn's log
    assert not standby.stdout_log.exists()


def test_no_standby_while_a_turn_runs(manager, tmp_path):
    busy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        manager._jobs["busy"] = LiveVoiceJob("busy", None, True, "simple_chunked", tmp_path / "a", tmp_path / "b",
                                             tmp_path / "c", tmp_path / "d", tmp_path / "e", "now", process=busy)
        manager._ensure_standby()
        assert manager._standby is None
    finally:
        busy.kill()
        busy.wait()


@pytest.mark.parametrize("why", ["config_changed", "too_old", "died"])
def test_stale_standby_is_stopped_not_used(manager, tmp_path, monkeypatch, why):
    manager._ensure_standby()
    standby = manager._standby
    if why == "config_changed":
        monkeypatch.setattr(manager, "_standby_key", lambda: "different")
    elif why == "too_old":
        standby.started = time.monotonic() - 301
    else:
        standby.process.kill()
        standby.process.wait()
    assert manager._claim_standby() is None
    assert standby.process.poll() is not None
    assert manager._standby is None


def test_disabled_setting_starts_nothing(manager, monkeypatch):
    monkeypatch.setattr(settings, "live_voice_standby_worker", False)
    manager._ensure_standby()
    assert manager._standby is None


def test_worker_reads_one_job_line():
    line = json.dumps({"turn_id": "t", "audio_path": "a.wav", "output_path": "o", "status_path": "s",
                       "synthesize_speech": False, "owner_verified": True}) + "\n"
    job = worker._read_standby_job(io.StringIO(line))
    args = argparse.Namespace()
    worker._apply_standby_job(args, job)
    assert (args.turn_id, args.audio_path, args.synthesize_speech, args.owner_verified, args.session_id) == \
        ("t", "a.wav", "false", True, None)
    assert worker._read_standby_job(io.StringIO("")) is None  # manager closed the pipe: exit quietly
    with pytest.raises(ValueError):
        worker._read_standby_job(io.StringIO('{"turn_id": "t"}\n'))
    with pytest.raises(ValueError):
        worker._read_standby_job(io.StringIO("[1]\n"))


@pytest.mark.parametrize("device, index, expected", [
    ("cuda", "1", "1"), ("cuda", "", "0"), ("cuda", "x", "0"), ("cpu", "1", None),
])
def test_gpu_stt_env_builds(manager, monkeypatch, device, index, expected):
    # This used to call a missing settings._as_int, so every GPU-STT live turn failed to start.
    monkeypatch.setenv("SHANA_STT_DEVICE", device)
    monkeypatch.setenv("SHANA_STT_DEVICE_INDEX", index)
    assert manager._selected_stt_env().get("SHANA_STT_DEVICE_INDEX") == expected
