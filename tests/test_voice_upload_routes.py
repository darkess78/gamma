import io
from unittest.mock import patch, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gamma.api import routes
from gamma.config import settings
from gamma.voice.roundtrip import VoiceRoundtripService
from gamma.voice.live_jobs import LiveVoiceJobManager
from gamma.voice.core_live import CoreLiveTurnRuntime


@pytest.mark.parametrize("endpoint", ["/v1/voice/transcribe", "/v1/voice/roundtrip", "/v1/voice/live/start"])
def test_oversized_upload_rejected_before_inference(tmp_path, monkeypatch, endpoint):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_upload_max_bytes", 4)
    conversation = Mock()
    media = VoiceRoundtripService(conversation=conversation)
    core = CoreLiveTurnRuntime(media=media, conversation=conversation, bus=Mock())
    app = FastAPI()
    app.include_router(routes.router)
    with patch.object(routes, "get_voice_roundtrip_service", return_value=media), patch.object(routes, "get_live_turn_runtime", return_value=core):
        response = TestClient(app).post(endpoint, files={"audio_file": ("fixture.wav", b"12345", "audio/wav")})
    assert response.status_code == 413
    conversation.respond.assert_not_called()
    assert not list(tmp_path.rglob("voice-upload-*"))
    assert not core._active


def test_legacy_turn_id_rejected_before_file_creation(tmp_path, monkeypatch):
    import asyncio
    from fastapi import UploadFile
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    manager = LiveVoiceJobManager(performer_bus=Mock())
    with pytest.raises(ValueError, match="turn_id"):
        asyncio.run(manager.start_job(audio_file=UploadFile(filename="fixture.wav", file=io.BytesIO(b"fixture")),
            session_id=None, synthesize_speech=False, turn_id="../../outside"))
    assert not list(tmp_path.rglob("*.wav"))
