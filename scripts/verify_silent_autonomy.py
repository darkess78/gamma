"""Disposable authenticated HTTP acceptance; optional real Ollama cognition.

Real mode holds the shared production model reservation around the entire test.
The disposable API uses a separate ledger only inside that exclusive reservation.
No production process or configuration is changed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import psutil

from verify_brain_process import port, until


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    production = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    calls = []
    provider_state = {"active": 0, "uncertain": False}
    hold, entered, release = threading.Event(), threading.Event(), threading.Event()
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"options": payload.get("options"), "think": payload.get("think")})
            if hold.is_set():
                entered.set()
                assert release.wait(15)
            if args.model:
                if args.cpu:
                    payload.setdefault("options", {})["num_gpu"] = 0
                provider_state["active"] += 1
                try:
                    with httpx.Client(timeout=90, trust_env=False) as http:
                        reply = http.post(args.endpoint.rstrip("/") + "/api/generate", json=payload)
                        reply.raise_for_status()
                        value = reply.json()
                        assert value.get("done") is True
                except httpx.HTTPStatusError as exc:
                    provider_state["uncertain"] = exc.response.status_code not in {400, 401, 403, 404, 413, 422, 429}
                    self.send_error(exc.response.status_code)
                    return
                except BaseException:
                    provider_state["uncertain"] = True
                    raise
                finally:
                    provider_state["active"] -= 1
            else:
                value = {"response": json.dumps({"action": "stay_silent", "requested_delivery": "silent",
                    "final_text": "", "internal_summary": "Crate B has five fewer bolts than crate A, based on the reported counts.",
                    "reason_code": "internal_comparison", "state_updates": {}})}
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    @contextmanager
    def reservation():
        if not args.model:
            yield
            return
        from gamma.resources.shared_execution import authority, _write_evidence
        ledger, demands, wait = authority(args.endpoint, "model")
        token = ledger.acquire(demands, priority="interactive", timeout=wait, workload="silent_acceptance", execution_kind="remote")
        try:
            # Do not evict another task's resident model for this acceptance.
            assert httpx.get(args.endpoint.rstrip("/") + "/api/ps").json()["models"] == [], "provider occupied"
            yield
        finally:
            if provider_state["active"] or provider_state["uncertain"]:
                ledger.uncertain(token)
            else:
                _write_evidence(token, {"endpoint": args.endpoint, "completion": "all_acceptance_requests_returned_done"})
                ledger.release(token)
    with reservation(), tempfile.TemporaryDirectory(prefix="gamma-silent-acceptance-") as directory:
        root = Path(directory)
        (root / "src").symlink_to(source / "src", target_is_directory=True)
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        (root / "config/admission.toml").write_text("wait_seconds = 0.2\n[budgets.model-work]\ncapacity = 2\nreserved = 1\n")
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{provider.server_port}"
        token = secrets.token_hex(24)
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
            SHANA_DATABASE_URL=f"sqlite:///{root}/data/memory/fixture.db", GAMMA_ADMISSION_DB=str(root / "admission.db"),
            GAMMA_ADMISSION_POLICY=str(root / "config/admission.toml"),
            SHANA_LLM_PROVIDER="local", SHANA_LLM_ROUTER_ENABLED="false", SHANA_LOCAL_LLM_ENABLE_ROUTING="false",
            SHANA_LOCAL_LLM_MODEL=args.model or "fixture", SHANA_LOCAL_LLM_ENDPOINT=endpoint,
            SHANA_LOCAL_LLM_KEEP_ALIVE="0", SHANA_LOCAL_LLM_TIMEOUT_SECONDS="90",
            SHANA_SPEECH_FILTER_LLM_ENABLED="false", SHANA_BRAIN_MODEL_RUNTIME="worker",
            SHANA_TTS_PROVIDER="stub", SHANA_TTS_RUNTIME="worker", SHANA_AUDIO_UNDERSTANDING_ENABLED="false",
            SHANA_PROACTIVE_IDLE_ENABLED="false", SHANA_PROACTIVE_IDLE_TICK_SECONDS="1",
            SHANA_PROACTIVE_IDLE_QUIET_HOURS_START="", SHANA_PROACTIVE_IDLE_QUIET_HOURS_END="",
            SHANA_API_AUTH_ENABLED="true", SHANA_API_BEARER_TOKEN=token, GAMMA_LIFECYCLE_MANAGER="")
        seed = '''from gamma.stream.self_goals import StreamSelfGoalStore
from gamma.presence import save_presence_state, presence_state_for_mode
goals = StreamSelfGoalStore()
goal = goals.propose(title="Compare fictional crate counts", description="Determine which crate has fewer bolts and by how many using the reported counts.")
goals.set_status(goal.id, status="approved")
save_presence_state(presence_state_for_mode("wake"))
print(goal.id)
'''
        goal = int(subprocess.check_output([sys.executable, "-c", seed], cwd=root, env=env, text=True))
        api_port = port()
        with (root / "server.log").open("wb") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.main:app", "--host", "127.0.0.1",
                "--port", str(api_port), "--no-access-log"], cwd=root, env=env, stdout=log, stderr=log)
            children = []
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=90, trust_env=False,
                                  headers={"Authorization": f"Bearer {token}"}) as client:
                    until(lambda: client.get("/health").status_code == 200)
                    assert httpx.get(f"http://127.0.0.1:{api_port}/v1/autonomy/silent").status_code == 401
                    client.post("/v1/voice/tts/stop").raise_for_status()
                    assert client.get("/v1/voice/tts/status").json()["enabled"] is False
                    assert client.get("/v1/performer/status").json()["stats"]["subscriber_count"] == 0
                    brain = client.get("/v1/brain/status").json()["worker"]
                    def observe(key, text):
                        response = client.post("/v1/autonomy/silent/observations", json={"key": key, "text": text, "goal_id": goal})
                        response.raise_for_status()
                        return response.json()
                    observe("counts", "Crate A has 8 bolts; crate B has 3 bolts.")
                    observe("counts", "Crate A has 8 bolts; crate B has 3 bolts. These are fictional reported counts.")
                    started = time.monotonic()
                    client.post("/v1/autonomy/silent/enable", json={"budget": 1, "cooldown_seconds": 5}).raise_for_status()
                    def completed():
                        state = client.get("/v1/autonomy/silent").json()
                        if not state["active"] and not state["enabled"] and state["completed"] == 0:
                            raise AssertionError(state)
                        return state if state["completed"] == 1 else None
                    state = until(completed, seconds=75)
                    elapsed = round(time.monotonic() - started, 3)
                    result = state["last_result"]
                    assert result["goal_id"] == goal and result["delivery"] == "silent" and not result["external_actions"]
                    summary = result["summary"].lower()
                    assert "b" in summary and ("5" in summary or "five" in summary), result
                    assert calls[0]["options"]["num_predict"] == 512 and calls[0]["think"] is False
                    with sqlite3.connect(root / "data/memory/fixture.db") as db:
                        assert db.execute("select current_objective from workingstatecheckpoint where session_id='autonomy:silent'").fetchone()[0] == result["summary"]
                        assert db.execute("select count(*) from gamma_delivery_grant").fetchone()[0] == 0
                        assert db.execute("select count(*) from gamma_decision_outbox where status='internal_only'").fetchone()[0] == 1
                        assert db.execute("select count(*) from conversationjournalentry where role='assistant'").fetchone()[0] == 0
                    assert client.get("/v1/voice/tts/status").json()["enabled"] is False
                    assert client.get("/v1/brain/status").json()["worker"]["pid"] == brain["pid"]
                    evidence = dict(provider=args.model or "scripted HTTP fixture", result=result, cycle_seconds=elapsed,
                        tts_stopped=True, playback_listener=False, delivery_grants=0, persistent_brain_pid=brain["pid"],
                        coalesced=state["observations"]["coalesced"], token_cap_verified=True)
                    if not args.model:
                        hold.set()
                        observe("counts", "New reported counts: A=8, B=3")
                        client.post("/v1/autonomy/silent/enable", json={"budget": 1, "cooldown_seconds": 5}).raise_for_status()
                        assert entered.wait(5)
                        with ThreadPoolExecutor(1) as pool:
                            owner = pool.submit(client.post, "/v1/conversation/respond", json={"user_text": "Owner fixture input", "fast_mode": True})
                            until(lambda: client.get("/v1/brain/status").json()["pending_foreground"] == 1)
                            for n in range(40):
                                overload = observe(str(n), "x" * 2000)
                            assert overload["count"] == 32 and overload["overloaded"] and overload["dropped"] == 8
                            control_start = time.monotonic()
                            paused = client.post("/v1/autonomy/silent/pause")
                            paused.raise_for_status()
                            disabled = client.post("/v1/autonomy/silent/disable")
                            disabled.raise_for_status()
                            evidence["stop_control_seconds"] = round(time.monotonic() - control_start, 3)
                            assert evidence["stop_control_seconds"] < 1
                            assert disabled.json()["active"] and disabled.json()["observations"]["count"] == 0
                            release.set()
                            owner.result(10).raise_for_status()
                        state = client.get("/v1/autonomy/silent").json()
                        assert state["completed"] == 0 and not state["active"]
                        with sqlite3.connect(root / "data/memory/fixture.db") as db:
                            assert db.execute("select count(*) from gamma_decision_outbox where session_id='autonomy:silent'").fetchone()[0] == 1
                        evidence.update(overload=overload, owner_completed=True, cancelled_result_discarded=True)
                    client.post("/v1/brain/stop").raise_for_status()
                    assert client.get("/health").status_code == 200
            finally:
                release.set()
                children = psutil.Process(server.pid).children(recursive=True) if server.poll() is None else []
                server.terminate()
                try:
                    server.wait(15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(5)
                for child in children:
                    if child.is_running():
                        child.terminate()
                psutil.wait_procs(children, timeout=5)
                provider.shutdown()
                provider.server_close()
    assert production == subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    evidence["production_unchanged"] = True
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
