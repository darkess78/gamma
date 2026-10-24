"""Disposable HTTP acceptance for independent cognition. No production restarts."""
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
import psutil


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def until(function, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = function()
            if result:
                return result
        except httpx.ConnectError:
            pass
        time.sleep(.1)
    raise AssertionError("acceptance condition timed out")


def main():
    source = Path(__file__).resolve().parents[1]
    production = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    evidence = {"source": str(source), "provider": "mock", "checks": []}
    with tempfile.TemporaryDirectory(prefix="gamma-brain-acceptance-") as directory:
        root = Path(directory)
        (root / "src").symlink_to(source / "src", target_is_directory=True)
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        token = secrets.token_hex(24)
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
            SHANA_DATABASE_URL=f"sqlite:///{root}/data/memory/fixture.db",
            SHANA_LLM_PROVIDER="mock", SHANA_LLM_ROUTER_ENABLED="false",
            SHANA_SPEECH_FILTER_LLM_ENABLED="false", SHANA_BRAIN_MODEL_RUNTIME="worker",
            SHANA_STT_PROVIDER="stub", SHANA_STT_RUNTIME="worker",
            SHANA_TTS_PROVIDER="stub", SHANA_TTS_RUNTIME="worker",
            SHANA_AUDIO_UNDERSTANDING_ENABLED="false", SHANA_PROACTIVE_IDLE_ENABLED="false",
            SHANA_PROACTIVE_IDLE_TICK_SECONDS="1", SHANA_PROACTIVE_IDLE_QUIET_HOURS_START="",
            SHANA_PROACTIVE_IDLE_QUIET_HOURS_END="", SHANA_API_AUTH_ENABLED="true",
            SHANA_API_BEARER_TOKEN=token, GAMMA_LIFECYCLE_MANAGER="")
        api_port = port()
        log = (root / "api.log").open("wb")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.main:app", "--host", "127.0.0.1",
            "--port", str(api_port), "--no-access-log"], cwd=root, env=env, stdout=log, stderr=log)
        dashboard_port = port()
        password = secrets.token_hex(24)
        dashboard_env = dict(env, SHANA_PORT=str(api_port), SHANA_BIND_HOST="127.0.0.1",
            SHANA_DASHBOARD_AUTH_ENABLED="true", SHANA_DASHBOARD_AUTH_USERNAME="fixture",
            SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=secrets.token_hex(32),
            SHANA_DASHBOARD_COOKIE_SECURE="false")
        dashboard_process = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.dashboard.main:app",
            "--host", "127.0.0.1", "--port", str(dashboard_port), "--no-access-log"],
            cwd=root, env=dashboard_env, stdout=log, stderr=log)
        dashboard = httpx.Client(base_url=f"http://127.0.0.1:{dashboard_port}", timeout=25)
        children = []
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=25,
                    headers={"Authorization": f"Bearer {token}"}) as client:
                until(lambda: client.get("/health").status_code == 200)
                until(lambda: dashboard.get("/login").status_code == 200)
                assert dashboard.post("/api/brain/stop").status_code == 401
                assert dashboard.post("/login", data={"username": "fixture", "password": password}).status_code == 303
                assert dashboard.get("/dashboard/workers").status_code == 200
                initial = client.get("/v1/brain/status").json()["worker"]
                assert initial["ready"], initial
                pid, generation = initial["pid"], initial["generation"]
                assert pid != server.pid
                def text(session):
                    result = client.post("/v1/conversation/respond", json={"user_text": "A fictional greeting.",
                        "session_id": session, "fast_mode": True})
                    assert result.status_code == 200, (result.text, client.get("/v1/brain/status").json())
                    assert result.json()["tts_metadata"]["brain_pid"] == pid
                    return result.json()
                text("brain-text")
                cli = subprocess.run([sys.executable, "-m", "gamma.run_llm_test", "Fictional CLI input"],
                    cwd=root, env=dict(env, SHANA_PORT=str(api_port), SHANA_BIND_HOST="127.0.0.1"),
                    capture_output=True, text=True, timeout=25)
                assert cli.returncode == 0, cli.stderr
                assert json.loads(cli.stdout)["reply"]
                assert client.get("/v1/brain/status").json()["worker"]["pid"] == pid
                evidence["checks"].append("dashboard_llm_smoke_cli_uses_api_and_same_brain")
                voice = client.post("/v1/voice/roundtrip", data={"session_id": "brain-voice", "synthesize_speech": "false"},
                    files={"audio_file": ("fixture.txt", b"A fictional voice-derived greeting.", "text/plain")})
                assert voice.status_code == 200, voice.text
                streamed = client.post("/v1/stream/events", json={"kind": "mic_transcript",
                    "text": "Fictional stream input", "session_id": "brain-stream", "actor": {"source": "local"}})
                assert streamed.status_code == 200, streamed.text
                assert streamed.json()["assistant_response"]["tts_metadata"]["brain_pid"] == pid
                client.post("/v1/presence/mode", json={"mode": "wake"}).raise_for_status()
                live = client.post("/v1/voice/live/start", data={"session_id": "brain-live", "synthesize_speech": "false",
                    "response_mode": "incremental_experimental"},
                    files={"audio_file": ("fixture.txt", b"Fictional live voice input", "text/plain")})
                assert live.status_code == 200, live.text
                live_id = live.json()["turn_id"]
                until(lambda: client.get(f"/v1/voice/live/{live_id}").json()["status"] in {"completed", "failed"})
                completed_live = client.get(f"/v1/voice/live/{live_id}").json()
                assert completed_live["status"] == "completed", completed_live
                assert completed_live["response_mode"] == "simple_chunked"
                evidence["checks"].append("stream_and_live_compatibility_paths_use_the_shared_brain")
                client.post("/v1/presence/mode", json={"mode": "wake"}).raise_for_status()
                subprocess.run([sys.executable, "-c",
                    "from gamma.stream.self_goals import StreamSelfGoalStore; "
                    "s=StreamSelfGoalStore(); g=s.propose(title='Fictional preparation', description='Identify a useful next internal step.'); "
                    "s.set_status(g.id, status='approved')"], cwd=root, env=env, check=True)
                client.post("/v1/autonomy/silent/enable", json={"budget": 1, "cooldown_seconds": 5}).raise_for_status()
                until(lambda: client.get("/v1/autonomy/silent").json()["completed"] == 1)
                current = client.get("/v1/brain/status").json()["worker"]
                assert current["pid"] == pid and current["generation"] == generation
                with sqlite3.connect(root / "data/memory/fixture.db") as db:
                    sessions = {row[0] for row in db.execute("select session_id from conversationjournalentry where completion_status='completed'")}
                    assert {"brain-text", "brain-voice", "autonomy:silent"} <= sessions, sessions
                evidence["checks"].append("text_voice_and_silent_cycle_commit_through_one_child")
                paused = dashboard.post("/api/brain/pause")
                paused.raise_for_status()
                assert paused.json()["worker"]["state"] == "paused"
                assert client.post("/v1/conversation/respond", json={"user_text": "paused"}).status_code == 503
                dashboard.post("/api/brain/resume").raise_for_status()
                text("after-resume")
                evidence["checks"].append("pause_resume_preserve_pid_and_reject_new_work")
                children = psutil.Process(server.pid).children(recursive=True)
                dashboard.post("/api/brain/stop").raise_for_status()
                assert not psutil.pid_exists(pid)
                assert dashboard.get("/dashboard/workers").status_code == 200
                assert dashboard.get("/api/brain/status").json()["worker"]["state"] == "stopped"
                assert client.get("/health").status_code == 200
                assert client.post("/v1/conversation/respond", json={"user_text": "stopped"}).status_code == 503
                restarted = dashboard.post("/api/brain/start")
                restarted.raise_for_status()
                updated = restarted.json()["worker"]
                assert updated["generation"] != generation and updated["pid"] != pid
                pid = updated["pid"]
                text("after-start")
                os.kill(pid, signal.SIGKILL)
                until(lambda: client.get("/v1/brain/status").json()["worker"]["state"] == "failed")
                assert dashboard.get("/dashboard/workers").status_code == 200
                assert dashboard.get("/api/brain/status").json()["worker"]["state"] == "failed"
                assert client.post("/v1/conversation/respond", json={"user_text": "crashed"}).status_code == 503
                assert client.get("/health").status_code == 200
                restarted = dashboard.post("/api/brain/start")
                restarted.raise_for_status()
                pid = restarted.json()["worker"]["pid"]
                text("after-crash")
                evidence["checks"].append("authenticated_dashboard_controls_stop_crash_restart_keep_api_and_dashboard_responsive")
                children.extend(psutil.Process(server.pid).children(recursive=True))
                evidence["brain_pid"] = pid
                evidence["api_pid"] = server.pid
        except BaseException:
            log.flush()
            # Fixture logs contain fictional inputs only; bearer is never printed.
            print((root / "api.log").read_text()[-10000:], file=sys.stderr)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            dashboard.close()
            dashboard_process.terminate()
            try:
                dashboard_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                dashboard_process.kill()
                dashboard_process.wait(timeout=5)
            log.close()
            for child in children:
                try:
                    child.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
    assert production == subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    evidence["checks"].append("production_service_pids_unchanged")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
