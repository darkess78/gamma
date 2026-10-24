import multiprocessing
from unittest.mock import Mock

import pytest

from gamma.resources.admission import Admission, AdmissionBusy
from gamma.voice.tts_runtime import TTSRuntime


def contend(path, start, result):
    admission = Admission(path)
    start.wait(5)
    try:
        result.put(("acquired", admission.acquire("tts")))
    except AdmissionBusy:
        result.put(("busy", None))


def test_processes_cannot_share_one_capacity_or_expire_dead_owner(tmp_path):
    path = tmp_path / "admission.db"
    admission = Admission(path)
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=contend, args=(path, start, results)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        start.set()
        outcomes = [results.get(timeout=10), results.get(timeout=10)]
        for child in children:
            child.join(10)
            assert child.exitcode == 0
        assert sorted(item[0] for item in outcomes) == ["acquired", "busy"]
        with pytest.raises(AdmissionBusy):
            Admission(path).acquire("tts")
        with pytest.raises(ValueError):
            admission.release("tts", "wrong-owner")
        token = next(item[1] for item in outcomes if item[0] == "acquired")
        admission.release("tts", token)
        assert admission.acquire("tts") != token
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_uncertain_tts_failure_persists_and_blocks_replacement(tmp_path):
    path = tmp_path / "admission.db"
    client = Mock()
    client.synthesize.side_effect = TimeoutError("synthetic adapter timeout")
    runtime = TTSRuntime(client, Admission(path))
    with pytest.raises(TimeoutError):
        runtime.synthesize("fixture")
    runtime.close()
    assert Admission(path).status("tts")["state"] == "uncertain"
    replacement = Mock()
    from gamma.errors import ExternalServiceError
    with pytest.raises(ExternalServiceError):
        TTSRuntime(replacement, Admission(path)).synthesize("another fixture")
    replacement.synthesize.assert_not_called()


def test_successful_tts_releases_capacity_and_close_rejects_work(tmp_path):
    admission = Admission(tmp_path / "admission.db")
    client = Mock()
    runtime = TTSRuntime(client, admission)
    def synthesize(**kwargs):
        assert admission.status("tts")["state"] == "active"
        return "audio"
    client.synthesize.side_effect = synthesize
    assert runtime.synthesize("fixture") == "audio"
    assert admission.status("tts") is None
    runtime.close()
    from gamma.errors import ExternalServiceError
    with pytest.raises(ExternalServiceError):
        runtime.synthesize("late")


def test_controls_never_clear_uncertain_lease(tmp_path):
    admission = Admission(tmp_path / "admission.db")
    token = admission.acquire("tts")
    admission.uncertain("tts", token)
    client = Mock()
    client.status.return_value = {"running": False, "busy": False}
    runtime = TTSRuntime(client, admission)
    assert runtime.status()["admission_state"] == "uncertain"
    assert "token" not in runtime.status()
    for action in ("start", "restart"):
        with pytest.raises(RuntimeError):
            runtime.control(action)
    runtime.control("stop")
    client.start.assert_not_called()
    assert admission.status("tts")["token"] == token
    assert runtime.status()["enabled"] is False


def test_tts_stop_prevents_synthesis_until_start(tmp_path):
    from gamma.errors import ExternalServiceError
    client = Mock()
    client.status.return_value = {"running": False, "busy": False}
    runtime = TTSRuntime(client, Admission(tmp_path / "admission.db"))
    runtime.control("stop")
    with pytest.raises(ExternalServiceError):
        runtime.synthesize("blocked")
    client.synthesize.assert_not_called()
    runtime.control("start")
    runtime.synthesize("allowed")
    client.synthesize.assert_called_once()
    runtime._lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            runtime.control("stop")
        assert runtime.status()["busy"] is True
    finally:
        runtime._lock.release()
