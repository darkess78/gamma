"""Disposable HTTP/SQLite acceptance for authority-owned publication and edits."""
import json
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


def main():
    source = Path(__file__).resolve().parents[1]
    service_command = ["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"]
    production = subprocess.check_output(service_command, text=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix="gamma-publication-acceptance-") as directory:
        root = Path(directory)
        (root / "src").symlink_to(source / "src", target_is_directory=True)
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        path = root / "data/memory/state.db"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.execute("create table prior_fixture(value text)")
            db.execute("insert into prior_fixture values ('preserve')")
        token = secrets.token_hex(24)
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
            SHANA_DATABASE_URL=f"sqlite:///{path}", SHANA_LLM_PROVIDER="mock",
            SHANA_LLM_ROUTER_ENABLED="false", SHANA_SPEECH_FILTER_LLM_ENABLED="false",
            SHANA_BRAIN_MODEL_RUNTIME="worker", SHANA_STT_PROVIDER="stub", SHANA_STT_RUNTIME="worker",
            SHANA_TTS_PROVIDER="stub", SHANA_TTS_RUNTIME="worker", SHANA_AUDIO_UNDERSTANDING_ENABLED="false",
            SHANA_PROACTIVE_IDLE_ENABLED="false", SHANA_API_AUTH_ENABLED="true", SHANA_API_BEARER_TOKEN=token,
            GAMMA_LIFECYCLE_MANAGER="")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        with (root / "api.log").open("wb") as log:
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "gamma.main:app", "--host", "127.0.0.1",
                "--port", str(port), "--no-access-log"], cwd=root, env=env, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=25,
                        headers={"Authorization": f"Bearer {token}"}) as client:
                    deadline = time.monotonic() + 25
                    while True:
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        if time.monotonic() >= deadline or server.poll() is not None:
                            raise AssertionError("isolated API failed to start")
                        time.sleep(.1)
                    text = client.post("/v1/conversation/respond", json={"user_text": "A fictional text fixture.",
                        "session_id": "text-fixture", "fast_mode": True})
                    text.raise_for_status()
                    voice = client.post("/v1/voice/roundtrip", data={"session_id": "voice-fixture", "synthesize_speech": "false"},
                        files={"audio_file": ("fixture.txt", b"A fictional voice fixture.", "text/plain")})
                    voice.raise_for_status()
                    with sqlite3.connect(path) as db:
                        rows = db.execute("select p.session_id,p.exchange_id,o.status,g.status "
                            "from gamma_publication p join gamma_decision_outbox o on o.exchange_id=p.exchange_id "
                            "join gamma_delivery_grant g on g.id=p.grant_id order by p.sequence").fetchall()
                        assert [row[0] for row in rows] == ["text-fixture", "voice-fixture"], rows
                        assert all(row[2:] == ("dispatched", "dispatched") for row in rows)
                        assert text.json()["route_trace_id"] == rows[0][1]
                    checks.append("real_text_and_voice_publish_from_owned_commits")
                    edit = client.post("/v1/memory/items", json={"kind": "profile_fact", "summary": "Fictional operator correction"})
                    edit.raise_for_status()
                    with sqlite3.connect(path) as db:
                        assert db.execute("select count(*) from gamma_delivery_grant where status!='cancelled'").fetchone()[0] == 0
                        assert db.execute("select count(*) from gamma_decision_outbox where status!='cancelled_operator'").fetchone()[0] == 0
                        assert db.execute("select count(*) from gamma_publication").fetchone()[0] == 2
                    checks.append("operator_edit_atomically_revokes_grants_and_preserves_publication_evidence")
                    client.delete("/v1/conversation/sessions/text-fixture/continuity").raise_for_status()
                    with sqlite3.connect(path) as db:
                        assert db.execute("select count(*) from gamma_publication where session_id='text-fixture'").fetchone()[0] == 0
                    checks.append("session_deletion_removes_its_publication_content")
                    backups = list((path.parent / "backups").glob("*.db"))
                    assert backups
                    with sqlite3.connect(backups[0]) as db:
                        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
                        assert db.execute("select value from prior_fixture").fetchone()[0] == "preserve"
                    checks.append("verified_backup_preserves_pre_migration_state")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    assert production == subprocess.check_output(service_command, text=True)
    checks.append("production_service_pids_unchanged")
    print(json.dumps({"source": str(source), "provider": "mock", "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
