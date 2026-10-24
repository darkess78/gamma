"""Disposable loopback playback acceptance fixture; never loads local secrets."""
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import wave


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gamma-playback-preview-") as directory:
        root = Path(directory)
        (root / "data" / "audio").mkdir(parents=True)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        shutil.copy(source / "pyproject.toml", root)
        os.environ.update(SHANA_PROJECT_ROOT=str(root), SHANA_DATABASE_URL=f"sqlite:///{root / 'data/fixture.db'}",
            SHANA_API_AUTH_ENABLED="false", SHANA_PROACTIVE_IDLE_ENABLED="false", SHANA_LLM_PROVIDER="mock")
        import uvicorn
        from fastapi import FastAPI
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
        from sqlmodel import Session
        from sqlalchemy import select
        from gamma.api.routes import router
        from gamma.delivery.playback import get_playback_ledger
        from gamma.memory.state_authority import playback_receipts
        from gamma.performer.bus import get_performer_event_bus
        from gamma.performer.models import PerformerOutputEvent
        from gamma.schemas.response import AssistantResponse
        from gamma.presence import presence_state_for_mode, save_presence_state
        save_presence_state(presence_state_for_mode("wake"))
        audio = root / "data/audio/fixture.wav"
        with wave.open(str(audio), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 320000)
        app = FastAPI()
        @app.middleware("http")
        async def fixture_media_auth(request, call_next):
            if request.url.path.startswith("/v1/delivery/") and request.headers.get("authorization") != "Bearer playback-fixture":
                return JSONResponse({"detail": "fixture media auth required"}, status_code=401)
            return await call_next(request)
        app.include_router(router)
        app.mount("/static", StaticFiles(directory=source / "src/gamma/dashboard/static"), name="static")

        @app.get("/fixture.wav")
        def fixture_audio():
            return FileResponse(audio, media_type="audio/wav")

        @app.post("/fixture/start")
        def start():
            response = AssistantResponse(spoken_text="Synthetic playback test", audio_path=str(audio), audio_content_type="audio/wav")
            ledger = get_playback_ledger()
            context = dict(target="dashboard_monitor", audience="private", policy="fixture")
            grant = ledger.issue(response, **context)
            ledger.dispatch(grant, response, **context)
            event = PerformerOutputEvent(type="speech_chunk_ready", turn_id="browser-fixture",
                target_policy="dashboard_monitor", payload={"audio_url": response.tts_metadata["delivery_audio_url"],
                    "delivery_grant": grant, "text": "Synthetic playback test"})
            get_performer_event_bus().publish(event)
            return {"event_id": event.event_id, "grant_id": grant}

        @app.post("/fixture/cancel/{grant_id}")
        def cancel(grant_id: str):
            return {"cancelled": get_playback_ledger().cancel(grant_id)}

        @app.get("/fixture/receipts")
        def read_receipts():
            with Session(get_playback_ledger().engine) as session:
                return [dict(row) for row in session.execute(select(playback_receipts)).mappings()]

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        print(json.dumps({"port": sock.getsockname()[1], "pid": os.getpid()}), flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


if __name__ == "__main__":
    main()
