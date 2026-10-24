"""Disposable dashboard transport checks, called by verify_modular_state.

Uses synthetic providers and protocol receipts; does not claim audible playback.
"""
import hashlib
import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect


def verify(root, environment, api_port, api, evidence):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    password = secrets.token_hex(24)
    env = dict(environment, SHANA_PORT=str(api_port), SHANA_BIND_HOST="127.0.0.1",
        SHANA_DASHBOARD_AUTH_ENABLED="true", SHANA_DASHBOARD_AUTH_USERNAME="fixture",
        SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=secrets.token_hex(32),
        SHANA_DASHBOARD_COOKIE_SECURE="false")
    base = f"http://127.0.0.1:{port}"
    with (root / "dashboard.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.dashboard.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=root, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=base, timeout=20) as dashboard:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        if dashboard.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("isolated dashboard startup failed")
                    time.sleep(0.1)
                assert dashboard.get("/api/playback/audio/" + "a" * 32).status_code == 401
                assert dashboard.post("/api/stt/stop").status_code == 401
                assert dashboard.post("/api/tts/stop").status_code == 401
                assert dashboard.post("/api/autonomy/silent/enable").status_code == 401
                assert dashboard.get("/api/tts/logs").status_code == 401
                login = dashboard.post("/login", data={"username": "fixture", "password": password})
                assert login.status_code == 303
                cookie = dashboard.cookies.get("gamma_dashboard_session")
                assert cookie
                stopped_tts = dashboard.post("/api/tts/stop")
                stopped_tts.raise_for_status()
                tts_events = dashboard.get("/api/tts/logs", params={"trace_id": stopped_tts.json()["trace_id"], "event_type": "lifecycle"})
                tts_events.raise_for_status()
                assert len(tts_events.json()["events"]) == 2
                assert all(event["event"] == "lifecycle.stop" for event in tts_events.json()["events"])
                assert not stopped_tts.json()["running"] and not stopped_tts.json()["enabled"]
                assert dashboard.get("/api/autonomy/silent").json()["enabled"] is False
                with sqlite3.connect(root / "data/memory/fixture.db") as db:
                    grant_count = db.execute("select count(*) from gamma_delivery_grant").fetchone()[0]
                before_events = api.get("/v1/performer/events/recent").json()["items"]
                dashboard.post("/api/autonomy/silent/enable").raise_for_status()
                deadline = time.monotonic() + 20
                while True:
                    silent = dashboard.get("/api/autonomy/silent").json()
                    if not silent["enabled"] and not silent["active"]:
                        break
                    assert time.monotonic() < deadline, silent
                    time.sleep(0.1)
                assert silent["completed"] == 1 and silent["remaining"] == 0, silent
                with sqlite3.connect(root / "data/memory/fixture.db") as db:
                    assert db.execute("select count(*) from gamma_delivery_grant").fetchone()[0] == grant_count
                    record = db.execute("select status,payload from gamma_decision_outbox where session_id='autonomy:silent'").fetchone()
                    assert record[0] == "internal_only" and json.loads(record[1])["display_text"] == ""
                    roles = db.execute("select role from conversationjournalentry where session_id='autonomy:silent' order by role").fetchall()
                    assert roles == [("event",)], roles
                assert api.get("/v1/performer/events/recent").json()["items"] == before_events
                assert not dashboard.get("/api/tts/status").json()["running"]
                evidence["checks"].append("dashboard_enabled_silent_cycle_commits_internal_summary_without_listener_tts_grant_or_output")
                text_only = api.post("/v1/conversation/respond", json={"user_text": "Fictional text while speech is stopped.", "synthesize_speech": False})
                text_only.raise_for_status()
                assert not text_only.json()["audio_path"]
                recognized = api.post("/v1/voice/transcribe", files={"audio_file": ("fixture.txt", b"Fictional recognition remains available", "text/plain")})
                recognized.raise_for_status()
                assert recognized.json()["transcript"] == "Fictional recognition remains available"
                denied = api.post("/v1/conversation/respond", json={"user_text": "Fictional disabled speech request.", "synthesize_speech": True})
                assert denied.status_code == 502
                assert not dashboard.get("/api/tts/status").json()["running"]
                dashboard.post("/api/tts/start").raise_for_status()
                evidence["checks"].append("dashboard_tts_stop_preserves_text_and_stt_and_blocks_synthesis")
                original_stt = dashboard.get("/api/stt/status").json()
                assert original_stt["running"] and original_stt["enabled"]
                stopped = dashboard.post("/api/stt/stop")
                stopped.raise_for_status()
                assert not stopped.json()["running"] and not stopped.json()["enabled"]
                rejected = api.post("/v1/voice/transcribe", files={"audio_file": ("blocked.txt", b"Private disabled fixture", "text/plain")})
                assert rejected.status_code == 503
                assert not dashboard.get("/api/stt/status").json()["running"]
                filtered = dashboard.get("/api/stt/logs", params={"trace_id": stopped.json()["trace_id"], "event_type": "lifecycle"})
                filtered.raise_for_status()
                assert len(filtered.json()["events"]) == 2
                assert all(row["event"] == "lifecycle.stop" for row in filtered.json()["events"])
                assert dashboard.get("/api/stt/logs", params={"trace_id": "unmatched"}).json()["events"] == []
                started = dashboard.post("/api/stt/start")
                started.raise_for_status()
                assert started.json()["running"] and started.json()["enabled"]
                restarted = dashboard.post("/api/stt/restart")
                restarted.raise_for_status()
                assert restarted.json()["running"] and restarted.json()["pid"] != started.json()["pid"]
                evidence["checks"].append("authenticated_dashboard_stt_stop_blocks_jobs_start_restart_and_log_filter")
                browser = subprocess.run(["node", str(Path(__file__).with_name("verify_stt_controls.cjs"))],
                    env=dict(env, FIXTURE_BASE=base, FIXTURE_PASSWORD=password), capture_output=True, text=True, timeout=45)
                assert browser.returncode == 0, browser.stderr
                assert json.loads(browser.stdout)["browser_log_filters"]
                evidence["checks"].append("chromium_stt_start_stop_restart_and_trace_type_filters")
                with connect(f"ws://127.0.0.1:{port}/api/playback/events", origin=base,
                        additional_headers={"Cookie": f"gamma_dashboard_session={cookie}"}, proxy=None) as ws:
                    def receive(kind):
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            message = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
                            if message.get("type") == kind:
                                return message
                        raise AssertionError(f"Missing {kind}")

                    ready = receive("ready")
                    lease = ready["playback_lease"]["lease_id"]
                    ws.send(json.dumps({"type": "subscriber_capabilities", "text_ready": True, "audio_ready": True}))
                    # Both messages share the ordered WebSocket stream.
                    ws.send(json.dumps({"type": "playback_heartbeat", "schema_version": 1, "lease_id": lease}))
                    live = api.post("/v1/voice/live/start", data={"session_id": "proxy-fixture", "synthesize_speech": "true"},
                        files={"audio_file": ("fixture.txt", b"Fictional proxy transcript", "text/plain")})
                    live.raise_for_status()
                    turn_id = live.json()["turn_id"]
                    event = receive("speech_chunk_ready")
                    offer = event["playback"]
                    assert offer["lease_id"] == lease
                    audio = dashboard.get(f"/api/playback/audio/{offer['grant_id']}")
                    audio.raise_for_status()
                    assert hashlib.sha256(audio.content).hexdigest() == offer["audio_digest"]
                    assert audio.headers["cache-control"] == "no-store"
                    ws.send(json.dumps({"type": "playback_prepare", "schema_version": 1,
                        "lease_id": lease, "event_id": offer["event_id"]}))
                    permit = receive("playback_permit")
                    assert permit["event_id"] == offer["event_id"]
                    api.post(f"/v1/voice/live/{turn_id}/cancel", data={"reason": "fixture"}).raise_for_status()
                    revoked = receive("playback_revoked")
                    assert revoked["event_id"] == offer["event_id"]
                    assert dashboard.get(f"/api/playback/audio/{offer['grant_id']}").status_code == 410
                    wake = api.post("/v1/presence/wake", json={"audience": {"kind": "unknown"}, "session_id": "voiced-wake-fixture"})
                    wake.raise_for_status()
                    assert wake.json()["wake"]["status"] == "audio_ready", wake.json()
                    wake_event = receive("speech_started")
                    assert wake_event["turn_id"] == wake.json()["wake"]["turn_id"]
                    wake_offer = wake_event["playback"]
                    wake_audio = dashboard.get(f"/api/playback/audio/{wake_offer['grant_id']}")
                    wake_audio.raise_for_status()
                    assert hashlib.sha256(wake_audio.content).hexdigest() == wake_offer["audio_digest"]
                    durable = api.get("/v1/performer/targets/dashboard_monitor/last-durable-output").json()["output"]
                    assert durable["status"] == "ready" and durable["spoken"] is False
                    api.post(f"/v1/delivery/{wake_offer['grant_id']}/cancel").raise_for_status()
                    evidence["checks"].append("wake_delivers_approved_audio_after_decision_without_claiming_playback")
                    streamed = api.post("/v1/stream/events", params={"synthesize_speech": "true"}, json={
                        "kind": "mic_transcript", "text": "A fictional stream delivery check.",
                        "session_id": "stream-delivery-fixture", "actor": {"source": "local", "platform_id": "fixture"}})
                    assert streamed.status_code == 200, streamed.text
                    response = streamed.json()["assistant_response"]
                    assert response and response["audio_path"], streamed.json()
                    stream_event = receive("speech_started")
                    stream_offer = stream_event["playback"]
                    assert stream_offer["grant_id"] == response["tts_metadata"]["delivery_grant"]
                    stream_audio = dashboard.get(f"/api/playback/audio/{stream_offer['grant_id']}")
                    stream_audio.raise_for_status()
                    assert hashlib.sha256(stream_audio.content).hexdigest() == stream_offer["audio_digest"]
                    api.post(f"/v1/delivery/{stream_offer['grant_id']}/cancel").raise_for_status()
                    evidence["checks"].append("stream_http_decision_uses_ordered_delivery_and_returns_approved_audio")
                evidence["checks"].append("dashboard_session_proxy_relays_real_lease_offer_permit_revocation_and_verified_audio")
        finally:
            process.terminate()
            process.wait(timeout=15)
