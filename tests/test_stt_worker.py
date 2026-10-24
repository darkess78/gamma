import io
import json
from unittest.mock import Mock

from gamma.config import settings
from gamma.voice.stt_worker import serve


def test_persistent_recognizer_and_scoped_input(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "runtime/uploads"
    root.mkdir(parents=True)
    source = root / "fixture.wav"
    source.write_bytes(b"fixture")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    provider = Mock()
    provider.transcribe_audio.return_value = "fictional transcript"
    factory = Mock(return_value=provider)
    requests = [
        {"type": "transcribe", "input_path": str(source)},
        {"type": "transcribe", "input_path": str(source)},
        {"type": "transcribe", "input_path": str(outside)},
        {"type": "status"}, {"type": "shutdown"},
    ]
    stream = io.BytesIO(b"".join((json.dumps({"schema_version": 1, "request_id": str(i), **r}) + "\n").encode() for i, r in enumerate(requests)))
    output = io.BytesIO()
    serve(stream, output, service_factory=factory)
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [f["type"] for f in frames] == ["ready", "result", "result", "error", "status", "stopped"]
    factory.assert_called_once()
    assert provider.transcribe_audio.call_count == 2
    assert len({f["pid"] for f in frames}) == 1
    assert frames[-2]["model_loaded"] is True
    assert source.read_bytes() == b"fixture"


def test_oversized_frame_terminates_without_provider():
    factory = Mock()
    output = io.BytesIO()
    serve(io.BytesIO(b"x" * 65537), output, service_factory=factory)
    assert json.loads(output.getvalue().splitlines()[-1])["reason"] == "invalid_frame"
    factory.assert_not_called()


def test_invalid_input_does_not_initialize_recognizer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    request = {"schema_version": 1, "request_id": "invalid", "type": "transcribe", "input_path": str(tmp_path / "missing.wav")}
    factory = Mock()
    output = io.BytesIO()
    serve(io.BytesIO((json.dumps(request) + "\n").encode()), output, service_factory=factory)
    assert json.loads(output.getvalue().splitlines()[-1])["type"] == "error"
    factory.assert_not_called()


def test_escaped_transcript_cannot_exceed_frame_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "runtime/uploads"
    root.mkdir(parents=True)
    source = root / "fixture.txt"
    source.write_text("fixture")
    provider = Mock()
    provider.transcribe_audio.return_value = "\x00" * 32768
    request = {"schema_version": 1, "request_id": "bounded", "type": "transcribe", "input_path": str(source)}
    output = io.BytesIO()
    serve(io.BytesIO((json.dumps(request) + "\n").encode()), output, service_factory=lambda: provider)
    frames = output.getvalue().splitlines()
    assert all(len(frame) < 65536 for frame in frames)
    assert json.loads(frames[-1])["type"] == "error"
