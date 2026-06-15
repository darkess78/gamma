from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile

from .config import settings
from .schemas.voice import VoiceInputContext
from .voice.audio_understanding import AudioUnderstandingService


_service: AudioUnderstandingService | None = None


def _get_service() -> AudioUnderstandingService:
    global _service
    if _service is None:
        _service = AudioUnderstandingService(allow_remote=False)
    return _service


def preload_models() -> None:
    global _service
    _service = AudioUnderstandingService(allow_remote=False)
    _service.preload()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    preload_models()
    yield


app = FastAPI(title="Gamma Audio Understanding", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    torch_status: dict[str, object]
    try:
        import torch

        torch_status = {
            "version": torch.__version__,
            "cuda_build": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_count": torch.cuda.device_count(),
        }
    except Exception as exc:
        torch_status = {"error": str(exc)}
    return {
        "ok": True,
        "speaker_emotion_provider": settings.speaker_emotion_provider,
        "speaker_emotion_model": settings.speaker_emotion_model,
        "speaker_emotion_device": settings.speaker_emotion_device,
        "audio_event_provider": settings.audio_event_provider,
        "audio_event_model": settings.audio_event_model,
        "audio_event_device": settings.audio_event_device,
        "models_initialized": _service is not None,
        "torch": torch_status,
    }


@app.post("/analyze", response_model=VoiceInputContext)
async def analyze(
    audio_file: UploadFile = File(...),
    transcript: str = Form(default=""),
) -> VoiceInputContext:
    runtime_dir = settings.data_dir / "runtime" / "audio_understanding"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(audio_file.filename or "").suffix or ".bin"
    path = runtime_dir / f"request-{uuid4().hex}{suffix}"
    started_at = time.perf_counter()
    try:
        path.write_bytes(await audio_file.read())
        result = _get_service().analyze_path(path, transcript=transcript)
        timing = dict(result.timing_ms)
        timing["sidecar_total_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        return result.model_copy(update={"timing_ms": timing})
    finally:
        path.unlink(missing_ok=True)
