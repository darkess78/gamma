"""Run disposable real browser voice playback with synthetic providers on Gamma."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile


async def run(root, source):
    sockets = []
    for _ in range(2):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    api_port, dashboard_port = [sock.getsockname()[1] for sock in sockets]
    token, password = secrets.token_hex(24), secrets.token_hex(24)
    os.environ.update(SHANA_PROJECT_ROOT=str(root), SHANA_DATABASE_URL=f"sqlite:///{root / 'data/fixture.db'}",
        SHANA_LLM_PROVIDER="mock", SHANA_LLM_ROUTER_ENABLED="false", SHANA_STT_PROVIDER="stub",
        SHANA_TTS_PROVIDER="stub", SHANA_PROACTIVE_IDLE_ENABLED="false", SHANA_AUDIO_UNDERSTANDING_ENABLED="false",
        SHANA_LIVE_VOICE_RUNTIME="core", SHANA_VOICE_UPLOAD_MAX_BYTES="262144", SHANA_API_AUTH_ENABLED="true", SHANA_API_BEARER_TOKEN=token,
        SHANA_BIND_HOST="127.0.0.1", SHANA_PORT=str(api_port), SHANA_DASHBOARD_AUTH_ENABLED="true",
        SHANA_DASHBOARD_AUTH_USERNAME="fixture", SHANA_DASHBOARD_AUTH_PASSWORD=password,
        SHANA_DASHBOARD_SESSION_SECRET=secrets.token_hex(32), SHANA_DASHBOARD_COOKIE_SECURE="false")
    import uvicorn
    from gamma.voice.stt import StubSTTBackend
    from gamma.voice.tts import StubTTSBackend
    # Deliberately synthetic fixtures, confined to this disposable process.
    StubSTTBackend.transcribe_audio = lambda self, source: "Hello Shana, this is a fictional browser test."
    original_tone = StubTTSBackend._write_tone_wave
    StubTTSBackend._write_tone_wave = lambda self, path, duration, sample_rate=16000: original_tone(self, path, 12, sample_rate)
    from gamma.main import app as api
    from gamma.dashboard.main import app as dashboard
    from gamma.presence import save_presence_state, presence_state_for_mode
    save_presence_state(presence_state_for_mode("wake"))
    servers = [uvicorn.Server(uvicorn.Config(app, log_level="error")) for app in (api, dashboard)]
    tasks = [asyncio.create_task(server.serve(sockets=[sock])) for server, sock in zip(servers, sockets)]
    try:
        for _ in range(200):
            if all(server.started for server in servers):
                break
            if any(task.done() for task in tasks):
                raise RuntimeError("fixture server exited")
            await asyncio.sleep(0.05)
        assert all(server.started for server in servers)
        env = dict(os.environ, FIXTURE_BASE=f"http://127.0.0.1:{dashboard_port}",
            FIXTURE_API=f"http://127.0.0.1:{api_port}", FIXTURE_PASSWORD=password, FIXTURE_TOKEN=token)
        process = await asyncio.create_subprocess_exec("node", str(source / "scripts/verify_live_browser.cjs"),
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=70)
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(stderr.decode() + stdout.decode())
        from sqlmodel import Session
        from sqlalchemy import select
        from gamma.delivery.playback import get_playback_ledger
        from gamma.memory.state_authority import playback_receipts
        with Session(get_playback_ledger().engine) as session:
            receipts = [dict(row) for row in session.execute(select(playback_receipts)).mappings()]
        assert {receipt["status"] for receipt in receipts} == {"interrupted", "completed"}, receipts
        print(json.dumps({"stored_receipt_statuses": sorted(receipt["status"] for receipt in receipts)}))
        print(stdout.decode(), flush=True)
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)
        for sock in sockets:
            sock.close()


def main():
    source = Path(__file__).resolve().parents[1]
    before = subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"])
    with tempfile.TemporaryDirectory(prefix="gamma-live-browser-") as directory:
        root = Path(directory)
        (root / "data").mkdir()
        shutil.copy(source / "pyproject.toml", root)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        asyncio.run(run(root, source))
    assert subprocess.check_output(["systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID"]) == before
    print(json.dumps({"production_pids_unchanged": True}))


if __name__ == "__main__":
    main()
