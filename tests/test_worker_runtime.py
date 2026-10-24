import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from gamma.config import settings
from gamma.supervisor.lifecycle_lock import lifecycle_lock
from gamma.supervisor.manager import ProcessManager
from gamma.supervisor.worker_events import WorkerEvents, read_worker_logs
from gamma.supervisor.workers import WorkerRuntime


def test_lock_excludes_processes_and_threads_and_releases_after_error(tmp_path):
    path = tmp_path / "lifecycle.lock"
    errors = []
    def contender():
        try:
            with lifecycle_lock(path, timeout=0.05):
                errors.append("entered")
        except TimeoutError:
            errors.append("timeout")
    with pytest.raises(ValueError):
        with lifecycle_lock(path):
            with lifecycle_lock(path):
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(2)
                code = "from pathlib import Path; from gamma.supervisor.lifecycle_lock import lifecycle_lock; " \
                       "\nwith lifecycle_lock(Path(__import__('sys').argv[1]), timeout=0.05): pass"
                result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=5)
                assert result.returncode != 0 and b"TimeoutError" in result.stderr
                raise ValueError()
    assert errors == ["timeout"]
    with lifecycle_lock(path, timeout=0.1):
        pass


def test_process_ownership_requires_exact_module_and_checkout(tmp_path):
    manager = ProcessManager()
    proc = Mock()
    with patch.object(settings, "project_root", tmp_path):
        proc.cwd.return_value = str(tmp_path / "other")
        proc.cmdline.return_value = [sys.executable, "-m", "gamma.improvement.worker"]
        assert not manager.looks_like_module_process(proc, "gamma.improvement.worker")
        proc.cwd.return_value = str(tmp_path)
        assert manager.looks_like_module_process(proc, "gamma.improvement.worker")
        proc.cmdline.return_value[-1] += "_other"
        assert not manager.looks_like_module_process(proc, "gamma.improvement.worker")


def test_dead_or_stale_heartbeat_cannot_claim_readiness(tmp_path):
    manager = ProcessManager()
    manager._runtime_dir = tmp_path
    runtime = WorkerRuntime(manager)
    heartbeat = tmp_path / "improvement_worker.heartbeat.json"
    heartbeat.write_text(json.dumps({"pid": 10, "timestamp": time.time(), "state": "ready", "instance_id": "old"}))
    with patch.object(manager, "find_module_process", return_value=Mock()), patch.object(manager, "process_payload", return_value={"running": True, "pid": 11, "create_time": time.time()-1}):
        assert runtime.status("improvement_worker")["observed_state"] == "degraded"
    with patch.object(manager, "find_module_process", return_value=None):
        assert runtime.status("improvement_worker")["observed_state"] == "stopped"
    with pytest.raises(ValueError):
        runtime.control("../../unsafe", "start")
    with pytest.raises(ValueError):
        runtime.control("shana", "start")


def test_logs_filters_cursor_loss_and_content_boundary(tmp_path):
    one = WorkerEvents(tmp_path, "improvement_worker")
    two = WorkerEvents(tmp_path, "shana")
    try:
        one.emit("lifecycle.ready", trace_id="one", reason_code="ready", password="secret", prompt="private", worker_id="spoof")
        one.handler.handleError(None)
        one.emit("job.started", trace_id="one", job_id="job-one")
        one.emit("job.completed", trace_id="two", job_id="job-two")
        two.emit("lifecycle.ready", trace_id="one")
        data = read_worker_logs(tmp_path, ("improvement_worker",), trace_id="one", limit=1)
        assert len(data["events"]) == 1 and data["next_cursor"]
        assert data["events"][0]["event_type"] == "job"
        assert data["reported_dropped_events"] == 1
        older = read_worker_logs(tmp_path, ("improvement_worker",), trace_id="one", before=data["next_cursor"])
        assert [x["event"] for x in older["events"]] == ["lifecycle.ready"]
        assert "secret" not in one.path.read_text() and "private" not in one.path.read_text()
        assert all(e["worker_id"] == "improvement_worker" for e in older["events"])
        assert not read_worker_logs(tmp_path, ("improvement_worker",), severity="error")["events"]
        assert len(read_worker_logs(tmp_path, ("improvement_worker",), event_type="lifecycle")["events"]) == 1
        assert not read_worker_logs(tmp_path, ("improvement_worker",), since="9999")["events"]
        with one.path.open("ab") as stream:
            stream.write(b'bad-record\n{"partial":')
        assert read_worker_logs(tmp_path, ("improvement_worker",))["skipped_records"] == 1
    finally:
        one.close()
        two.close()


def test_log_reads_and_pages_are_bounded_and_duplicates_removed(tmp_path):
    events = WorkerEvents(tmp_path, "improvement_worker")
    try:
        events.emit("lifecycle.ready")
        record = events.path.read_bytes()
        events.path.write_bytes(record * 4000)
        data = read_worker_logs(tmp_path, ("improvement_worker",), limit=20000)
        assert data["window_truncated"]
        assert data["bytes_read"] <= 512 * 1024
        assert len(data["events"]) == 1
    finally:
        events.close()


def test_worker_routes_share_auth_and_bound_inputs(tmp_path):
    from gamma.dashboard import main
    with patch.object(main, "is_authenticated", return_value=False), TestClient(main.app) as client:
        assert client.get("/api/workers").status_code == 401
        assert client.post("/api/workers/improvement_worker/start").status_code == 401
        assert client.get("/api/worker-logs").status_code == 401
    with patch.object(main, "is_authenticated", return_value=True), TestClient(main.app) as client:
        assert client.get("/dashboard/workers").status_code == 200
        assert client.get("/api/worker-logs?limit=201").status_code == 422
        assert client.get("/api/worker-logs?workers=unknown").status_code == 400
        assert client.post("/api/workers/shana/start").status_code == 400


def test_actual_log_io_loss_and_retention(tmp_path):
    events = WorkerEvents(tmp_path, "improvement_worker")
    with patch.object(events.handler.stream, "write", side_effect=OSError("private disk error")):
        events.emit("job.started", job_id="synthetic")
    assert events.handler.dropped_events == 1
    events.emit("job.completed", job_id="synthetic", outcome="failed")
    data = read_worker_logs(tmp_path, ("improvement_worker",), severity="error")
    assert data["reported_dropped_events"] == 1 and len(data["events"]) == 1
    assert "private disk error" not in events.path.read_text()
    events.close()
    for _ in range(15):
        producer = WorkerEvents(tmp_path, "improvement_worker")
        producer.emit("lifecycle.ready")
        producer.close()
    assert len(list((tmp_path / "worker-logs" / "improvement_worker").glob("*.jsonl"))) <= 9


def test_busy_worker_control_refuses_force_stop_and_failed_launch_is_visible(tmp_path):
    manager = ProcessManager()
    manager._runtime_dir = tmp_path
    runtime = WorkerRuntime(manager)
    with patch.object(runtime, "status", return_value={"observed_state": "busy"}), patch.object(manager, "stop_module") as stop:
        assert not runtime.control("improvement_worker", "stop")["ok"]
        stop.assert_not_called()
    with patch.object(manager, "start_module", side_effect=OSError("secret launch detail")), patch.object(manager, "find_module_process", return_value=None):
        failed = runtime.control("improvement_worker", "start")
        assert not failed["ok"]
        assert failed["worker"]["observed_state"] == "failed"
        assert "secret launch detail" not in json.dumps(failed)


def test_missing_log_sink_counts_loss_without_stopping_worker(tmp_path):
    with patch("gamma.supervisor.worker_events.LossCountingHandler", side_effect=OSError("unavailable")):
        events = WorkerEvents(tmp_path, "improvement_worker")
        events.emit("lifecycle.ready")
        assert events.handler.dropped_events == 1
        events.close()
