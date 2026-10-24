"""Run from Gamma's venv. Real dashboard/worker acceptance with disposable state.

No production lifecycle commands, credentials, models, or configuration are used.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil


def main():
    source = Path(__file__).resolve().parents[1]
    production = subprocess.check_output(
        ["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    evidence = {"source": str(source), "production_before": production.strip(), "checks": []}
    with tempfile.TemporaryDirectory(prefix="gamma-worker-acceptance-") as temporary:
        root = Path(temporary)
        (root / "src").symlink_to(source / "src", target_is_directory=True)
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        (root / "data" / "memory").mkdir(parents=True)
        env = os.environ.copy()
        # Test configuration is process-only. No ignored local configuration is copied.
        password, session = secrets.token_hex(16), secrets.token_hex(32)
        env.update(SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(root / "src"),
                   SHANA_DASHBOARD_AUTH_ENABLED="true", SHANA_DASHBOARD_AUTH_USERNAME="fixture",
                   SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=session,
                   SHANA_DASHBOARD_COOKIE_SECURE="false", GAMMA_LIFECYCLE_MANAGER="",
                   SHANA_LOG_STDERR_ENABLED="false")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        env["SHANA_DASHBOARD_PORT"] = str(port)
        log = (root / "server.log").open("wb")
        dashboard = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.dashboard.main:app",
                                      "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                     cwd=root, env=env, stdout=log, stderr=log)
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
        worker_pids = set()
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                if dashboard.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("isolated dashboard failed to start")
                time.sleep(0.1)
            assert client.get("/api/workers").status_code == 401
            assert client.post("/api/workers/improvement_worker/start").status_code == 401
            assert client.post("/login", data={"username": "fixture", "password": password}).status_code == 303
            assert client.get("/dashboard/workers").status_code == 200
            evidence["checks"].append("real HTTP authentication and workers HTML")

            def control(action):
                response = client.post(f"/api/workers/improvement_worker/{action}")
                response.raise_for_status()
                result = response.json()
                assert result["ok"], result
                if result["worker"].get("pid"):
                    worker_pids.add(result["worker"]["pid"])
                return result

            def ready():
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    response = client.get("/api/workers")
                    response.raise_for_status()
                    status = next(x for x in response.json()["workers"] if x["worker_id"] == "improvement_worker")
                    if status["observed_state"] == "ready":
                        return status
                    time.sleep(0.1)
                raise AssertionError("worker never reported ready")

            start = time.perf_counter()
            first = control("start")
            status = ready()
            evidence["first_start_ready_ms"] = round((time.perf_counter() - start) * 1000, 1)
            first_pid = status["pid"]
            assert psutil.Process(first_pid).cwd() == str(root)
            logs = client.get("/api/worker-logs", params={"workers": "improvement_worker", "trace_id": first["trace_id"]}).json()
            names = {x["event"] for x in logs["events"]}
            assert {"lifecycle.requested", "lifecycle.completed", "lifecycle.ready"} <= names, names
            assert len({x["instance_id"] for x in logs["events"]}) >= 2
            evidence["checks"].append("dashboard to real worker readiness and cross-process trace")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: control("start"), range(2)))
            assert all(x["worker"]["pid"] == first_pid for x in responses)
            evidence["checks"].append("concurrent starts retain one worker PID")
            # Exercise CLI and dashboard against the same serialized lifecycle authority.
            cli = subprocess.run([sys.executable, "-m", "gamma.supervisor.cli", "start", "improvement-worker"],
                                 cwd=root, env=env, capture_output=True, text=True, timeout=20)
            assert cli.returncode == 0
            assert json.loads(cli.stdout)["improvement-worker"]["worker"]["pid"] == first_pid
            start = time.perf_counter()
            restarted = control("restart")
            second = ready()
            evidence["restart_ready_ms"] = round((time.perf_counter() - start) * 1000, 1)
            assert second["pid"] != first_pid and second["instance_id"] != status["instance_id"]
            assert second["restart_count"] == 1
            assert not psutil.pid_exists(first_pid)
            evidence["checks"].append("CLI compatibility and restart generation replacement")
            # The disposable root intentionally has no Git repository. The real runner
            # must reject this job at its source-validation gate before any inference.
            assert not (root / ".git").exists()
            accepted = client.post("/api/improvement/work", json={"goal": "Measure the fictional acceptance fixture only.",
                                                                  "budget_minutes": 15, "maximum_cycles": 1})
            accepted.raise_for_status()
            job_id = accepted.json()["request"]["id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job_logs = client.get("/api/worker-logs", params={"workers": "improvement_worker", "trace_id": job_id}).json()
                job_events = {e["event"] for e in job_logs["events"]}
                if "job.completed" in job_events:
                    break
                time.sleep(0.1)
            assert {"job.accepted", "job.started", "job.completed"} <= job_events, job_events
            assert next(e for e in job_logs["events"] if e["event"] == "job.completed")["outcome"] == "failed"
            evidence["checks"].append("accepted job trace crosses dashboard and worker; invalid checkout rejected before inference")
            filtered = client.get("/api/worker-logs", params={"workers": "improvement_worker", "event_type": "lifecycle", "severity": "info", "limit": 1}).json()
            assert len(filtered["events"]) == 1 and filtered["next_cursor"]
            older = client.get("/api/worker-logs", params={"workers": "improvement_worker", "limit": 1, "before": filtered["next_cursor"]}).json()
            assert older["events"][0]["event_id"] != filtered["events"][0]["event_id"]
            assert client.get("/api/worker-logs", params={"workers": "shana"}).json()["events"] == []
            assert filtered["bytes_read"] <= 512 * 1024 and "reported_dropped_events" in filtered
            evidence["checks"].append("server-side filters, cursor pages, byte bound, loss fields")
            victim = psutil.Process(second["pid"])
            assert victim.cwd() == str(root)
            victim.kill()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                observed = next(x for x in client.get("/api/workers").json()["workers"] if x["worker_id"] == "improvement_worker")
                if observed["observed_state"] == "failed":
                    break
                time.sleep(0.1)
            assert observed["observed_state"] == "failed" and observed["last_failure"] == "process_exited"
            assert client.get("/health").status_code == 200
            control("start")
            recovered = ready()
            assert recovered["instance_id"] != second["instance_id"]
            evidence["checks"].append("optional worker crash is visible; dashboard survives and starts a new generation")
            stopped = control("stop")
            assert stopped["worker"]["observed_state"] == "stopped"
            assert not psutil.pid_exists(recovered["pid"])
            assert client.get("/health").status_code == 200
            evidence["checks"].append("worker stopped, dashboard still responsive")
        finally:
            # Cleanup addresses only processes launched by this disposable run.
            try:
                client.post("/api/workers/improvement_worker/stop")
            except Exception:
                pass
            for pid in worker_pids:
                try:
                    process = psutil.Process(pid)
                    if process.cwd() == str(root):
                        process.terminate()
                        process.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
            dashboard.terminate()
            dashboard.wait(timeout=10)
            client.close()
            log.close()
    after = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    assert after == production
    evidence["production_after"] = after.strip()
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
