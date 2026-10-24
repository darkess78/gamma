import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gamma.voice import tts_worker
from gamma.voice.tts_protocol import validate_synthesis
from gamma.voice.tts_client import TTSWorkerClient


@pytest.mark.parametrize("fields", [{"text": " "}, {"text": "x" * 16385}, {"text": "ok", "styles": "bad"},
    {"text": "ok", "styles": ["x"] * 17}, {"text": "ok", "emotion": False}, {"text": "ok", "path": "/secret"}])
def test_invalid_arguments_rejected_before_provider(fields):
    factory = Mock()
    request = {"schema_version": 1, "request_id": "fixture", "type": "synthesize", **fields}
    output = io.BytesIO()
    tts_worker.serve(io.BytesIO((json.dumps(request) + "\n").encode()), output, service_factory=factory)
    factory.assert_not_called()
    assert json.loads(output.getvalue().splitlines()[-1])["type"] == "error"


def test_persistent_provider_returns_verified_snapshot_without_text(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_worker.settings, "audio_output_dir", tmp_path / "audio")
    monkeypatch.setattr(tts_worker.settings, "data_dir", tmp_path)
    source = tmp_path / "audio/fixture.wav"
    source.parent.mkdir()
    source.write_bytes(b"fictional audio")
    service = Mock()
    service.synthesize.return_value = SimpleNamespace(audio_path=str(source), content_type="audio/wav")
    factory = Mock(return_value=service)
    requests = [dict(schema_version=1, request_id=str(i), type="synthesize", text="private fixture") for i in range(2)]
    output = io.BytesIO()
    tts_worker.serve(io.BytesIO("".join(json.dumps(r) + "\n" for r in requests).encode()), output, service_factory=factory)
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [f["type"] for f in frames] == ["ready", "result", "result"]
    factory.assert_called_once()
    assert service.synthesize.call_count == 2
    assert b"private fixture" not in output.getvalue()
    source.write_bytes(b"changed provider output")
    client = TTSWorkerClient(["unused"], cwd=tmp_path, artifact_root=tmp_path / "runtime/tts-worker-artifacts")
    client.request = Mock(return_value=frames[1])
    result = client.synthesize("private fixture")
    from pathlib import Path
    assert Path(result.audio_path).read_bytes() == b"fictional audio"
    Path(result.audio_path).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        client.synthesize("private fixture")


def test_provider_output_outside_scope_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_worker.settings, "audio_output_dir", tmp_path / "allowed")
    source = tmp_path / "outside.wav"
    source.write_bytes(b"secret")
    with pytest.raises(ValueError):
        tts_worker.output_record(SimpleNamespace(audio_path=str(source), content_type="audio/wav"))


def test_unicode_limits_are_bytes_and_text_is_preserved():
    assert validate_synthesis({"text": "  exact words  "})["text"] == "  exact words  "
    with pytest.raises(ValueError):
        validate_synthesis({"text": "界" * 6000})
