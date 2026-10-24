"""Exercise the real API and state database with fictional data and mock inference."""
import json
import base64
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx
import psutil


def main():
    source = Path(__file__).resolve().parents[1]
    production = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    evidence = {"source": str(source), "scope": "real HTTP and SQLite with mock inference", "checks": []}
    with tempfile.TemporaryDirectory(prefix="gamma-state-acceptance-") as directory:
        root = Path(directory)
        (root / "src").symlink_to(source / "src", target_is_directory=True)
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        (root / "data" / "memory").mkdir(parents=True)
        path = root / "data" / "memory" / "fixture.db"
        legacy_persona = root / "data" / "assistant_state.json"
        legacy_persona.write_text(json.dumps({"current_emotion": "neutral", "notes": ["Prior fictional feeling"]}))
        legacy_bytes = legacy_persona.read_bytes()
        # Existing state proves startup migration really keeps a prior-state backup.
        with sqlite3.connect(path) as db:
            db.execute("create table prior_fixture(value text)")
            db.execute("insert into prior_fixture values ('fictional')")
        token = secrets.token_hex(24)
        env = os.environ.copy()
        env.update(SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(root / "src"), SHANA_DATABASE_URL=f"sqlite:///{path}",
                   SHANA_LLM_PROVIDER="mock", SHANA_LLM_ROUTER_ENABLED="false", SHANA_TTS_PROVIDER="stub", SHANA_TTS_RUNTIME="worker",
                   SHANA_STT_PROVIDER="stub", SHANA_STT_RUNTIME="worker", SHANA_PROACTIVE_IDLE_ENABLED="false",
                   SHANA_AUDIO_UNDERSTANDING_ENABLED="false",
                   SHANA_LIVE_VOICE_RUNTIME="core",
                   SHANA_ASSISTANT_STATE_ENABLED="true",
                   SHANA_MEMORY_ENABLED="true", SHANA_MEMORY_WRITE_MODE="auto",
                   SHANA_API_AUTH_ENABLED="true", SHANA_API_BEARER_TOKEN=token,
                   GAMMA_LIFECYCLE_MANAGER="")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        log = (root / "api.log").open("wb")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.main:app", "--host", "127.0.0.1",
                                   "--port", str(port), "--no-access-log"], cwd=root, env=env, stdout=log, stderr=log)
        stt_child = None
        tts_child = None
        model_child = None
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=20) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("isolated API startup failed")
                    time.sleep(0.1)
                endpoint = "/v1/conversation/sessions/state-fixture/continuity"
                brain_before = client.get("/v1/brain/status")
                brain_before.raise_for_status()
                assert brain_before.json()["pending"] == 0 and not brain_before.json()["active"]
                assert brain_before.json()["pending_foreground"] == 0
                assert brain_before.json()["pending_background"] == 0
                assert client.get(endpoint).json()["state_version"] == 0
                response = client.post("/v1/conversation/respond", json={"user_text": "Hello, this is a fictional validation turn.",
                                        "session_id": "state-fixture", "synthesize_speech": False, "fast_mode": True})
                response.raise_for_status()
                assert response.json()["display_text"]
                if env.get("SHANA_BRAIN_MODEL_RUNTIME") == "worker":
                    children = [p for p in psutil.Process(server.pid).children() if "gamma.brain.model_worker" in p.cmdline()]
                    assert len(children) == 1
                    model_child = children[0]
                brain_after = client.get("/v1/brain/status").json()
                assert brain_after["state"] == "running" and brain_after["pending"] == 0 and not brain_after["active"]
                evidence["checks"].append("brain_queue_accepts_text_and_returns_to_idle_execution")
                sleep_result = client.post("/v1/presence/mode", json={"mode": "sleep"})
                assert sleep_result.status_code == 200, (sleep_result.text, (root / "api.log").read_text()[-6000:])
                stream = client.post("/v1/stream/events", json={"kind": "chat_message", "text": "Shana, fictional queue check.",
                    "actor": {"source": "twitch", "platform_id": "fixture"}})
                stream.raise_for_status()
                assert stream.json()["decision"]["reason"] == "presence_sleep_suppressed_public_output"
                assert not stream.json()["output_events"]
                evidence["checks"].append("stream_ingress_uses_shared_queue_and_suppresses_public_output_in_sleep")
                snapshot = client.get(endpoint).json()
                assert snapshot["state_version"] >= 2 and snapshot["summary"]["completed_exchanges"] == 1
                with sqlite3.connect(path) as db:
                    assert db.execute("select count(*) from gamma_decision_outbox where session_id='state-fixture'").fetchone()[0] == 1
                    assert db.execute("select status from gamma_decision_outbox where session_id='state-fixture'").fetchone()[0] in {"dispatched", "cancelled_operator"}
                    assert db.execute("select count(*) from gamma_publication where session_id='state-fixture'").fetchone()[0] == 1
                    assert db.execute("select count(*) from conversationjournalentry where completion_status='completed' and session_id='state-fixture'").fetchone()[0] == 2
                    assert db.execute("select count(*) from gamma_state_document where version=1 and key in ('assistant_feeling','assistant_emotion')").fetchone()[0] == 2
                    migrated = db.execute("select legacy_payload from gamma_state_document where key='assistant_feeling'").fetchone()[0]
                    assert json.loads(migrated)["notes"] == ["Prior fictional feeling"]
                assert legacy_persona.read_bytes() == legacy_bytes
                evidence["checks"].append("persona documents join decision commit and preserve legacy JSON")
                evidence["checks"].append("real text route commits owned state then atomically records authorized publication")
                backups = list((path.parent / "backups").glob("*.db"))
                assert backups
                with sqlite3.connect(backups[0]) as db:
                    assert db.execute("select value from prior_fixture").fetchone()[0] == "fictional"
                    assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
                evidence["checks"].append("startup preserves verified pre-migration database")
                assert client.delete(endpoint).status_code == 200
                assert client.get(endpoint).json()["state_version"] == snapshot["state_version"] + 1
                with sqlite3.connect(path) as db:
                    assert db.execute("select count(*) from gamma_decision_outbox where session_id='state-fixture'").fetchone()[0] == 0
                evidence["checks"].append("authenticated deletion removes owned state and invalidates prior version")
                tool_response = client.post("/v1/conversation/respond", json={
                    "user_text": "remember: A fictional adapter fact.", "session_id": "tool-fixture",
                    "synthesize_speech": False, "fast_mode": True})
                tool_response.raise_for_status()
                receipt = next(item for item in tool_response.json()["tool_results"] if item["tool"] == "save_core_memory")
                assert receipt["ok"] and receipt["metadata"]["status"] == "committed"
                with sqlite3.connect(path) as db:
                    core = db.execute("select payload from gamma_state_document where key='core_memories'").fetchone()[0]
                    assert "A fictional adapter fact." in json.loads(core)["text"]
                    assert db.execute("select version from gamma_state_version where session_id='tool-fixture'").fetchone()[0] == 1
                evidence["checks"].append("real authenticated tool request commits core fact with decision receipt")
                standalone = client.post("/v1/conversation/respond", json={"user_text": "Fictional standalone greeting", "fast_mode": True})
                standalone.raise_for_status()
                from gamma.resources.admission import Admission
                admission = Admission(root / "data/runtime/model-admission.sqlite3")
                resources = ["fixture-owner", "voice-inference"]
                reservation = admission.acquire_many(resources)
                try:
                    blocked_voice = client.post("/v1/voice/roundtrip", data={"synthesize_speech": "false"},
                        files={"audio_file": ("fixture.txt", b"Fictional occupied voice request", "text/plain")})
                    assert blocked_voice.status_code == 503, blocked_voice.text
                    blocked_speech = client.post("/v1/conversation/respond", json={
                        "user_text": "Fictional occupied speech request", "fast_mode": True, "synthesize_speech": True})
                    assert blocked_speech.status_code == 502, blocked_speech.text
                    text_only = client.post("/v1/conversation/respond", json={
                        "user_text": "Fictional text while voice is occupied", "fast_mode": True, "synthesize_speech": False})
                    text_only.raise_for_status()
                    assert text_only.json()["display_text"]
                    assert admission.status("stt") is None and admission.status("tts") is None
                    assert admission.resources_for(reservation) == sorted(resources)
                finally:
                    admission.release_many(resources, reservation)
                evidence["checks"].append("shared_voice_contention_rejects_stt_and_tts_without_partial_leases_while_text_works")
                spoken = client.post("/v1/conversation/respond", json={"user_text": "Fictional spoken greeting",
                    "session_id": "speech-fixture", "fast_mode": True, "synthesize_speech": True})
                spoken.raise_for_status()
                assert spoken.json()["audio_path"]
                assert spoken.json()["tts_metadata"]["delivery_state"] == "synthesized"
                grant = spoken.json()["tts_metadata"]["delivery_grant"]
                audio_url = f"/v1/delivery/{grant}/audio"
                approved_audio = client.get(audio_url)
                approved_audio.raise_for_status()
                assert approved_audio.content == Path(spoken.json()["audio_path"]).read_bytes()
                assert approved_audio.headers["cache-control"] == "no-store"
                assert client.get(audio_url, headers={"Authorization": "Bearer invalid"}).status_code == 401
                with sqlite3.connect(path) as db:
                    assert db.execute("select status from gamma_delivery_grant where id=?", (grant,)).fetchone()[0] == "dispatched"
                cancel_url = f"/v1/delivery/{grant}/cancel"
                assert client.post(cancel_url, headers={"Authorization": "Bearer invalid"}).status_code in (401, 403)
                assert client.post(cancel_url).json() == {"cancelled": True}
                assert client.post(cancel_url).json() == {"cancelled": False}
                assert client.get(audio_url).status_code == 410
                evidence["checks"].append("authenticated audio fetch verifies approved snapshot and rejects cancelled grant")
                with sqlite3.connect(path) as db:
                    assert db.execute("select status from gamma_delivery_grant where id=?", (grant,)).fetchone()[0] == "cancelled"
                evidence["checks"].append("authenticated HTTP cancellation persists and rejects unauthorized requests")
                evidence["checks"].append("HTTP speech facade returns synthesized artifact after decision commit without claiming playback")
                pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jI1sAAAAASUVORK5CYII=")
                pictured = client.post("/v1/conversation/respond-with-image", data={"user_text": "Describe this synthetic fixture"},
                    files={"image_file": ("fixture.png", pixel, "image/png")})
                assert pictured.status_code == 400 and "does not support image analysis" in pictured.text, (pictured.status_code, pictured.text)
                for item in (standalone,):
                    trace = item.json()["route_trace_id"]
                    with sqlite3.connect(path) as db:
                        record = db.execute("select session_id,status from gamma_exchange_owner where exchange_id=?", (trace,)).fetchone()
                        assert record[0].startswith("transient:") and record[1] == "completed"
                evidence["checks"].append("sessionless text HTTP request commits an owned decision; mock vision capability gate rejects images")
                wake = client.post("/v1/presence/wake", json={"audience": {"kind": "unknown"}, "session_id": "wake-fixture"})
                wake.raise_for_status()
                assert wake.json()["wake"]["status"] == "text_only"
                presence_file = root / "data/runtime/presence/state.json"
                with sqlite3.connect(path) as db:
                    saved_presence = db.execute("select payload,version from gamma_state_document where key='presence'").fetchone()
                for _ in range(2):
                    status = client.get("/v1/presence")
                    status.raise_for_status()
                    assert status.json()["state"]["mode"] == "wake"
                with sqlite3.connect(path) as db:
                    assert db.execute("select payload,version from gamma_state_document where key='presence'").fetchone() == saved_presence
                assert not presence_file.exists()
                evidence["checks"].append("queued_wake_commits_with_revision_and_status_reads_do_not_rewrite_presence")
                voice = client.post("/v1/voice/roundtrip", data={"session_id": "voice-fixture", "synthesize_speech": "false"},
                    files={"audio_file": ("fixture.txt", b"Fictional voice transcript", "text/plain")})
                voice.raise_for_status()
                children = [p for p in psutil.Process(server.pid).children() if "gamma.voice.stt_worker" in p.cmdline()]
                assert len(children) == 1
                stt_child = children[0]
                assert voice.json()["transcript"] == "Fictional voice transcript"
                assert voice.json()["reply_text"] and voice.json()["audio_base64"] is None
                assert not list((root / "data/runtime/uploads").glob("voice-upload-*"))
                voice_state = client.get("/v1/conversation/sessions/voice-fixture/continuity")
                voice_state.raise_for_status()
                assert voice_state.json()["state_version"] == 1
                evidence["checks"].append("roundtrip_stub_transcript_uses_brain_queue_commits_state_and_cleans_upload")
                voiced = client.post("/v1/voice/roundtrip", data={"session_id": "voice-audio-fixture", "synthesize_speech": "true"},
                    files={"audio_file": ("fixture.txt", b"Fictional spoken roundtrip", "text/plain")})
                voiced.raise_for_status()
                children = [p for p in psutil.Process(server.pid).children() if "gamma.voice.tts_worker" in p.cmdline()]
                assert len(children) == 1
                tts_child = children[0]
                assert base64.b64decode(voiced.json()["audio_base64"]).startswith(b"RIFF")
                evidence["checks"].append("roundtrip_returns_approved_snapshot_audio_after_separate_delivery")
                live = client.post("/v1/voice/live/start", data={"session_id": "live-fixture", "synthesize_speech": "true"},
                    files={"audio_file": ("fixture.txt", b"Fictional live transcript", "text/plain")})
                live.raise_for_status()
                turn_id = live.json()["turn_id"]
                deadline = time.monotonic() + 20
                while True:
                    job = client.get(f"/v1/voice/live/{turn_id}")
                    job.raise_for_status()
                    record = job.json()
                    if record["status"] in ("completed", "failed", "cancelled"):
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                assert record["status"] == "completed", record
                assert record["worker_pid"] is None and record["audio_base64"] is None
                assert record["transcript"] == "Fictional live transcript" and record["reply_text"]
                assert client.get("/v1/conversation/sessions/live-fixture/continuity").json()["state_version"] == 1
                assert not list((root / "data/runtime/uploads").glob("voice-upload-*"))
                events = client.get("/v1/performer/events/recent").json()["items"]
                audio_event = next(item for item in events if item["turn_id"] == turn_id and item["type"] == "speech_chunk_ready")
                live_grant = audio_event["payload"]["delivery_grant"]
                assert "audio_base64" not in audio_event["payload"]
                client.get(f"/v1/delivery/{live_grant}/audio").raise_for_status()
                client.post(f"/v1/voice/live/{turn_id}/cancel", data={"reason": "fixture"}).raise_for_status()
                assert client.get(f"/v1/delivery/{live_grant}/audio").status_code == 410
                evidence["checks"].append("core_live_job_uses_shared_stages_without_per_turn_subprocess_and_commits_state")
                children = [p for p in psutil.Process(server.pid).children() if "gamma.voice.stt_worker" in p.cmdline()]
                assert len(children) == 1 and children[0] == stt_child and stt_child.is_running()
                evidence["checks"].append("roundtrip_and_core_live_share_one_persistent_stt_child")
                assert [p for p in psutil.Process(server.pid).children() if "gamma.voice.tts_worker" in p.cmdline()] == [tts_child]
                evidence["checks"].append("roundtrip_and_core_live_share_persistent_tts")
                from verify_playback_proxy import verify
                if model_child is not None:
                    assert [p for p in psutil.Process(server.pid).children() if "gamma.brain.model_worker" in p.cmdline()] == [model_child]
                verify(root, env, port, client, evidence)
                assert not stt_child.is_running()
                children = [p for p in psutil.Process(server.pid).children() if "gamma.voice.stt_worker" in p.cmdline()]
                assert len(children) == 1
                stt_child = children[0]
                tts_children = [p for p in psutil.Process(server.pid).children() if "gamma.voice.tts_worker" in p.cmdline()]
                assert len(tts_children) == 1 and not tts_child.is_running()
                tts_child = tts_children[0]
                with sqlite3.connect(root / "data/runtime/model-admission.sqlite3") as leases:
                    assert leases.execute("select count(*) from resource_lease").fetchone()[0] == 0
                evidence["checks"].append("roundtrip_and_core_live_share_persistent_tts_with_released_admission")
                if model_child is not None:
                    assert not model_child.is_running()
                    children = [p for p in psutil.Process(server.pid).children() if "gamma.brain.model_worker" in p.cmdline()]
                    assert len(children) == 1
                    model_child = children[0]
                    evidence["checks"].append("model_process_reused_then_dashboard_restarts_and_filters_logs")
                    client.post("/v1/brain-model/stop").raise_for_status()
                    rejected = client.post("/v1/conversation/respond", json={"user_text": "Fictional stopped model check",
                        "session_id": "stopped-model-fixture", "synthesize_speech": False, "fast_mode": True})
                    assert rejected.status_code >= 400
                    assert not model_child.is_running()
                    assert not [p for p in psutil.Process(server.pid).children() if "gamma.brain.model_worker" in p.cmdline()]
                    client.get("/health").raise_for_status()
                    assert client.get("/v1/brain-model/status").json()["admission_state"] is None
                    client.post("/v1/brain-model/start").raise_for_status()
                    children = [p for p in psutil.Process(server.pid).children() if "gamma.brain.model_worker" in p.cmdline()]
                    assert len(children) == 1
                    model_child = children[0]
                    evidence["checks"].append("stopped_model_rejects_text_without_lazy_restart_and_core_stays_healthy")
        finally:
            server.terminate()
            server.wait(timeout=15)
            log.close()
            if model_child is not None:
                assert not model_child.is_running(), "API shutdown left model child alive"
                evidence["checks"].append("api_shutdown_reaped_model_child")
            if tts_child is not None:
                assert not tts_child.is_running(), "API shutdown left TTS child alive"
                evidence["checks"].append("api_shutdown_reaped_tts_child")
            if stt_child is not None:
                assert not stt_child.is_running(), "API shutdown left STT child alive"
                evidence["checks"].append("api_shutdown_reaped_stt_child")
    after = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"], text=True)
    assert production == after
    evidence["production_pids_unchanged"] = True
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
