from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import psutil

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gamma.observability import (
    bind_context,
    configure_logging,
    install_request_logging,
    log_event,
    reset_context,
)
from gamma.supervisor.manager import ProcessManager


class ObservabilityTest(unittest.TestCase):
    def test_json_record_shape_context_redaction_and_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.jsonl"
            logger = configure_logging(
                f"test-{id(path)}",
                log_path=path,
                max_bytes=100_000,
                backup_count=2,
                stderr=False,
            )
            token = bind_context(request_id="request-1", event_id="event-1")
            try:
                try:
                    raise RuntimeError("failed with Bearer super-secret")
                except RuntimeError:
                    log_event(
                        logger,
                        logging.ERROR,
                        "test.failure",
                        "Operation failed.",
                        exc_info=True,
                        authorization="Bearer super-secret",
                        nested={"password": "hidden", "ok": True},
                    )
            finally:
                reset_context(token)
            for handler in logger.handlers:
                handler.flush()
            payload = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])

        self.assertEqual(payload["service"], f"test-{id(path)}")
        self.assertEqual(payload["event"], "test.failure")
        self.assertEqual(payload["request_id"], "request-1")
        self.assertEqual(payload["event_id"], "event-1")
        self.assertEqual(payload["authorization"], "[REDACTED]")
        self.assertEqual(payload["nested"]["password"], "[REDACTED]")
        self.assertEqual(payload["error_class"], "RuntimeError")
        self.assertIn("Traceback", payload["traceback"])
        self.assertNotIn("super-secret", json.dumps(payload))

    def test_rotating_file_retains_bounded_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime.jsonl"
            logger = configure_logging(
                f"rotate-{id(path)}",
                log_path=path,
                max_bytes=350,
                backup_count=2,
                stderr=False,
            )
            for index in range(30):
                log_event(logger, logging.INFO, "test.rotation", "x" * 120, index=index)
            for handler in logger.handlers:
                handler.flush()
            files = sorted(path.parent.glob("runtime.jsonl*"))

        self.assertLessEqual(len(files), 3)
        self.assertGreater(len(files), 1)

    def test_request_id_is_created_or_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = FastAPI()
            logger = configure_logging(
                f"http-{id(app)}",
                log_path=Path(temp_dir) / "http.jsonl",
                stderr=False,
            )
            install_request_logging(app, service="test-http", logger=logger)

            @app.get("/items/{item_id}")
            def item(item_id: str) -> dict[str, str]:
                return {"item_id": item_id}

            with TestClient(app) as client:
                generated = client.get("/items/1")
                preserved = client.get("/items/2", headers={"X-Request-ID": "request-existing"})

        self.assertTrue(generated.headers["X-Request-ID"])
        self.assertEqual(preserved.headers["X-Request-ID"], "request-existing")

    def test_supervisor_preserves_previous_service_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ProcessManager()
            manager._runtime_dir = Path(temp_dir)
            manager.stdout_log("worker").write_text("previous stdout\n", encoding="utf-8")
            manager.stderr_log("worker").write_text("previous stderr\n", encoding="utf-8")

            manager._preserve_service_logs("worker", keep=2)

            stdout_archives = list(Path(temp_dir).glob("worker.stdout.log.*"))
            stderr_archives = list(Path(temp_dir).glob("worker.stderr.log.*"))
            self.assertEqual(len(stdout_archives), 1)
            self.assertEqual(len(stderr_archives), 1)
            self.assertEqual(stdout_archives[0].read_text(encoding="utf-8"), "previous stdout\n")
            self.assertEqual(stderr_archives[0].read_text(encoding="utf-8"), "previous stderr\n")
            self.assertEqual(manager.stdout_log("worker").read_text(encoding="utf-8"), "")

    def test_supervisor_handles_fast_worker_exit(self) -> None:
        manager = ProcessManager()
        process = Mock()
        process.pid = 123
        process.oneshot.side_effect = psutil.ZombieProcess(123)

        self.assertEqual(manager.process_payload(process), {"running": False, "pid": 123})
