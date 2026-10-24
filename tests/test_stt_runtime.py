import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from gamma.voice.stt_runtime import STTRuntime


@pytest.mark.parametrize("details", [None, {"device": "cuda", "device_index": 0}])
def test_inference_metadata_does_not_override_dashboard_runtime_mode(monkeypatch, details):
    from gamma.voice import roundtrip
    monkeypatch.setattr(roundtrip.settings, "stt_runtime", "worker")
    client = Mock()
    client.status.return_value = {"running": details is not None, "runtime": details}
    service = roundtrip.VoiceRoundtripService(conversation=Mock())
    service._stt = STTRuntime(client)
    status = service.stt_status()
    assert status["runtime"] == "worker"
    assert status["inference_runtime"] == details
    assert client.status.return_value["runtime"] == details


def test_close_drains_active_then_rejects_new_work():
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()
    client = Mock()
    client.status.return_value = {"busy": True}
    def transcribe(path):
        entered.set()
        assert release.wait(3)
        return "fixture"
    client.transcribe_audio.side_effect = transcribe
    runtime = STTRuntime(client)
    with ThreadPoolExecutor(2) as executor:
        result = executor.submit(runtime.transcribe_audio, "fixture")
        assert entered.wait(3)
        def close():
            closing.set()
            runtime.close()
        closed = executor.submit(close)
        assert closing.wait(3)
        try:
            assert runtime.status()["busy"] is True
            with pytest.raises(RuntimeError):
                runtime.control("stop")
            client.close.assert_not_called()
        finally:
            release.set()
        assert result.result(3) == "fixture"
        closed.result(3)
    client.close.assert_called_once()
    with pytest.raises(RuntimeError):
        runtime.transcribe_audio("late")
    assert client.transcribe_audio.call_count == 1


def test_remote_worker_provider_rejected_before_child_creation(monkeypatch, tmp_path):
    from gamma.voice import roundtrip
    monkeypatch.setattr(roundtrip.settings, "stt_runtime", "worker")
    monkeypatch.setattr(roundtrip.settings, "stt_provider", "openai")
    factory = Mock()
    monkeypatch.setattr(roundtrip, "STTWorkerClient", factory)
    service = roundtrip.VoiceRoundtripService(conversation=Mock())
    with pytest.raises(ValueError, match="local provider"):
        service.transcribe_path(tmp_path / "fixture")
    factory.assert_not_called()


def test_controls_disable_until_explicit_start_and_filter_real_logs(tmp_path):
    from gamma.supervisor.worker_events import WorkerEvents, read_worker_logs
    client = Mock()
    client.status.side_effect = lambda: {"running": True, "busy": False}
    client.transcribe_audio.return_value = "private transcript fixture"
    runtime = STTRuntime(client, events=WorkerEvents(tmp_path, "stt"))
    try:
        stopped = runtime.control("stop")
        assert not stopped["enabled"]
        with pytest.raises(RuntimeError):
            runtime.transcribe_audio("secret-path")
        client.transcribe_audio.assert_not_called()
        runtime.control("start")
        assert runtime.transcribe_audio("secret-path") == "private transcript fixture"
        result = read_worker_logs(tmp_path, ("stt",), event_type="lifecycle", trace_id=stopped["trace_id"])
        assert len(result["events"]) == 2
        assert all(row["trace_id"] == stopped["trace_id"] and row["event"] == "lifecycle.stop" for row in result["events"])
        jobs = read_worker_logs(tmp_path, ("stt",), event_type="job")["events"]
        assert {row["event"] for row in jobs} == {"job.started", "job.completed"}
        all_logs = "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))
        assert "secret-path" not in all_logs and "private transcript" not in all_logs
        client.start.side_effect = RuntimeError("private provider error")
        with pytest.raises(RuntimeError):
            runtime.control("restart")
        assert not runtime.status()["enabled"]
        failures = read_worker_logs(tmp_path, ("stt",), severity="error")["events"]
        assert len(failures) == 1 and failures[0]["reason_code"] == "operation_failed"
    finally:
        runtime.close()


def test_provider_admission_denial_is_retryable_and_releases_owned_capacity(tmp_path):
    from gamma.resources.admission import Admission, AdmissionBusy
    from gamma.brain.queue import BrainQueueFull
    client = Mock()
    client.status.return_value = {"running": False}
    client.transcribe_audio.side_effect = AdmissionBusy("fixture occupied")
    admission = Admission(tmp_path / "admission.db")
    runtime = STTRuntime(client, admission=admission)
    with pytest.raises(BrainQueueFull, match="shared voice inference"):
        runtime.transcribe_audio("fixture.wav")
    assert admission.status("stt") is None
    assert admission.status("voice-inference") is None
    client.transcribe_audio.side_effect = None
    client.transcribe_audio.return_value = "retry after capacity returns"
    assert runtime.transcribe_audio("fixture.wav") == "retry after capacity returns"
