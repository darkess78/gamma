from unittest.mock import Mock

import pytest

from gamma.resources.admission import Admission
from gamma.supervisor.worker_events import WorkerEvents, read_worker_logs
from gamma.voice.tts_runtime import TTSRuntime


def test_tts_events_correlate_without_content_or_lease_tokens(tmp_path):
    client = Mock()
    client.status.return_value = {"running": False, "busy": False}
    admission = Admission(tmp_path / "leases.db")
    runtime = TTSRuntime(client, admission, events=WorkerEvents(tmp_path, "tts"))
    try:
        result = runtime.control("start")
        rows = read_worker_logs(tmp_path, ("tts",), event_type="lifecycle", trace_id=result["trace_id"])["events"]
        assert len(rows) == 2 and all(row["event"] == "lifecycle.start" for row in rows)
        runtime.synthesize("private synthesis fixture")
        tokens = [client.synthesize.call_args.kwargs["job_id"]]
        client.synthesize.side_effect = TimeoutError("private provider url and message")
        with pytest.raises(TimeoutError):
            runtime.synthesize("another private phrase")
        tokens.append(admission.status("tts")["token"])
        failures = read_worker_logs(tmp_path, ("tts",), severity="error")["events"]
        assert len(failures) == 1 and failures[0]["event"] == "job.failed"
        paired = read_worker_logs(tmp_path, ("tts",), trace_id=failures[0]["trace_id"])["events"]
        assert {row["event"] for row in paired} == {"job.started", "job.failed"}
        raw = "".join(path.read_text() for path in tmp_path.rglob("*.jsonl"))
        assert all(token not in raw for token in tokens)
        assert all(value not in raw for value in ["private synthesis", "another private", "private provider"])
        with pytest.raises(RuntimeError):
            runtime.control("reconcile")
        controls = read_worker_logs(tmp_path, ("tts",), severity="error", event_type="lifecycle")["events"]
        assert len(controls) == 1 and controls[0]["event"] == "lifecycle.reconcile"
    finally:
        runtime.close()


def test_telemetry_failure_does_not_change_synthesis_or_admission(tmp_path):
    client, events = Mock(), Mock()
    client.status.return_value = {"running": False}
    events.emit.side_effect = OSError("fixture log disk unavailable")
    events.close.side_effect = OSError("fixture close failure")
    admission = Admission(tmp_path / "leases.db")
    runtime = TTSRuntime(client, admission, events=events)
    runtime.control("start")
    result = runtime.synthesize("fixture")
    assert result is client.synthesize.return_value and admission.status("tts") is None
    runtime.close()
