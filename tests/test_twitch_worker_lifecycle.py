import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import psutil

from gamma.integrations.twitch.irc import parse_irc_line
from gamma.integrations.twitch.readiness import ConnectionReadiness


def test_readiness_requires_own_authentication_and_join():
    state = ConnectionReadiness("bot", "room")
    for line in [":server 001 other :Welcome", ":other!user JOIN #room",
                 ":viewer!user PRIVMSG #room : 001 bot JOIN #room"]:
        state.observe(parse_irc_line(line))
    assert not state.ready
    state.observe(parse_irc_line(":server 001 bot :Welcome"))
    assert not state.ready
    state.observe(parse_irc_line(":bot!bot@local JOIN #room"))
    assert state.ready
    state.reset()
    assert not state.ready and not state.joined and not state.authenticated


def test_authenticated_dashboard_controls_real_irc_worker_over_local_tls(tmp_path):
    source = Path(__file__).resolve().parents[1]
    (tmp_path / "src").symlink_to(source / "src", target_is_directory=True)
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/app.example.toml").write_text("")
    (tmp_path / "data/memory").mkdir(parents=True)
    cert, key = tmp_path / "certificate.pem", tmp_path / "fixture-key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True, timeout=15)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.2)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    stop, connected, allow_join, pong = [threading.Event() for _ in range(4)]
    failures = []
    def serve():
        while not stop.is_set():
            try:
                raw, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as stream:
                    stream.settimeout(3)
                    commands = b""
                    while b"JOIN #room\r\n" not in commands:
                        chunk = stream.recv(4096)
                        if not chunk:
                            break
                        commands += chunk
                        assert len(commands) <= 8192
                    assert b"NICK bot\r\n" in commands
                    stream.sendall(b":server 001 bot :Welcome\r\n:other!user JOIN #room\r\n")
                    connected.set()
                    while not allow_join.wait(0.1):
                        if stop.is_set():
                            return
                    stream.sendall(b":bot!bot@localhost JOIN #room\r\nPING :fixture\r\n")
                    while not stop.is_set():
                        try:
                            data = stream.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        if b"PONG :fixture" in data:
                            pong.set()
            except Exception as exc:
                if not stop.is_set():
                    failures.append(type(exc).__name__)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    password = uuid4().hex
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), PYTHONPATH=str(tmp_path / "src"),
               SHANA_DATABASE_URL=f"sqlite:///{tmp_path / 'data/memory/fixture.db'}",
               SHANA_DASHBOARD_AUTH_ENABLED="true", SHANA_DASHBOARD_AUTH_USERNAME="fixture",
               SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=uuid4().hex,
               SHANA_DASHBOARD_COOKIE_SECURE="false", SHANA_DASHBOARD_PORT=str(port),
               SHANA_TWITCH_CHANNEL="room", SHANA_TWITCH_BOT_USERNAME="bot", SHANA_TWITCH_OAUTH_TOKEN="fictional-token",
               SHANA_TWITCH_IRC_HOST="127.0.0.1", SHANA_TWITCH_IRC_PORT=str(listener.getsockname()[1]),
               SHANA_TWITCH_DRY_RUN="true", SSL_CERT_FILE=str(cert), GAMMA_LIFECYCLE_MANAGER="")
    log = (tmp_path / "dashboard.log").open("wb")
    dashboard = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.dashboard.main:app", "--host", "127.0.0.1",
                                  "--port", str(port), "--no-access-log"], cwd=tmp_path, env=env, stdout=log, stderr=log)
    workers = []
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
    def wait_for(check, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.1)
        raise AssertionError("fixture condition timed out")
    def status():
        response = client.get("/api/workers")
        response.raise_for_status()
        return next(w for w in response.json()["workers"] if w["worker_id"] == "twitch_worker")
    try:
        def healthy():
            try:
                return client.get("/health").status_code == 200
            except httpx.ConnectError:
                return False
        wait_for(healthy)
        assert client.post("/api/workers/twitch_worker/start").status_code == 401
        assert client.post("/login", data={"username": "fixture", "password": password}).status_code == 303
        started = client.post("/api/workers/twitch_worker/start").json()
        assert started["ok"], started
        first = psutil.Process(started["worker"]["pid"])
        workers.append(first)
        assert first.ppid() == dashboard.pid
        assert connected.wait(5)
        assert status()["observed_state"] == "starting"
        allow_join.set()
        wait_for(lambda: status()["observed_state"] == "ready")
        assert pong.wait(3)
        trace = started["trace_id"]
        logs = client.get("/api/worker-logs", params={"workers": "twitch_worker", "event_type": "connection", "trace_id": trace}).json()
        assert logs["events"] and all(e["worker_id"] == "twitch_worker" and e["trace_id"] == trace for e in logs["events"])
        assert "fictional-token" not in json.dumps(logs)
        restarted = client.post("/api/workers/twitch_worker/restart").json()
        assert restarted["ok"], restarted
        second = psutil.Process(restarted["worker"]["pid"])
        workers.append(second)
        assert second.pid != first.pid and not first.is_running()
        wait_for(lambda: status()["observed_state"] == "ready")
        assert client.post("/api/workers/twitch_worker/stop").json()["ok"]
        assert not second.is_running() and status()["observed_state"] == "stopped"
        legacy = client.post("/api/twitch/worker/start").json()
        assert legacy["ok"] and legacy["worker"] == "twitch_irc", legacy
        third = psutil.Process(legacy["process"]["pid"])
        workers.append(third)
        wait_for(lambda: status()["observed_state"] == "ready")
        assert status()["desired_state"] == "running"
        assert client.post("/api/twitch/worker/stop").json()["ok"]
        assert not third.is_running() and status()["desired_state"] == "stopped"
        assert client.get("/health").status_code == 200
        assert not failures
    finally:
        allow_join.set()
        for process in workers:
            if process.is_running():
                process.terminate()
                process.wait(timeout=5)
        dashboard.terminate()
        dashboard.wait(timeout=10)
        client.close()
        log.close()
        stop.set()
        listener.close()
        thread.join(5)
