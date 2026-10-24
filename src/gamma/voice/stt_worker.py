"""Persistent STT process with a bounded, versioned JSON-lines interface."""
import contextlib
import json
import os
import re
import sys
from pathlib import Path

from ..config import settings
from .stt import STTService

MAX_MESSAGE_BYTES = 65536


def runtime_info(service):
    backend = getattr(service, "_backend", None)
    provider = getattr(backend, "provider_name", None)
    model = getattr(getattr(backend, "_model", None), "model", None)
    device = getattr(model, "device", None)
    indices = getattr(model, "device_index", None)
    if type(indices) is int:
        indices = [indices]
    return {"provider": provider if isinstance(provider, str) and len(provider) <= 64 else None,
            "device": device if device in ("cpu", "cuda") else None,
            "device_index": indices if isinstance(indices, list) and len(indices) <= 8 and all(type(i) is int for i in indices) else None}


def validated_source(raw_path):
    """Validate and resolve an input path without touching the model service."""
    if not isinstance(raw_path, str) or len(raw_path) > 4096:
        raise ValueError("invalid input path")
    root = (settings.data_dir / "runtime/uploads").resolve()
    source = Path(raw_path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("invalid input file")
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved.stat().st_size > settings.voice_upload_max_bytes:
        raise ValueError("input outside worker scope")
    return resolved


def transcribe(service, resolved_path):
    """Run STT on an already-validated resolved path."""
    transcript = service.transcribe_audio(str(resolved_path))
    if not isinstance(transcript, str) or len(transcript.encode("utf-8")) > 32768:
        raise ValueError("transcript exceeds worker limit")
    return transcript


def serve(input_stream, output_stream, *, service_factory=STTService):
    service = None
    def emit(payload):
        frame = (json.dumps({"schema_version": 1, "pid": os.getpid(), **payload}, ensure_ascii=False) + "\n").encode()
        if len(frame) > MAX_MESSAGE_BYTES:
            raise ValueError("response frame exceeds limit")
        output_stream.write(frame)
        output_stream.flush()
    emit({"type": "ready", "model_loaded": False})
    while True:
        line = input_stream.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
            emit({"type": "error", "reason": "invalid_frame"})
            return
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or type(request.get("schema_version")) is not int or request["schema_version"] != 1:
                raise ValueError("invalid schema")
            request_id = request.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
                request_id = None
                raise ValueError("invalid request id")
            kind = request.get("type")
            if kind == "shutdown":
                emit({"type": "stopped", "request_id": request_id})
                return
            if kind == "status":
                emit({"type": "status", "request_id": request_id, "model_loaded": service is not None, "runtime": runtime_info(service)})
                continue
            if kind != "transcribe":
                raise ValueError("unsupported request")
            from ..resources.shared_execution import work_request
            with contextlib.redirect_stdout(sys.stderr), work_request(request_id):
                resolved = validated_source(request.get("input_path"))
                if service is None:
                    service = service_factory()
                transcript = transcribe(service, resolved)
            emit({"type": "result", "request_id": request_id, "transcript": transcript, "runtime": runtime_info(service)})
        except Exception as exc:
            from ..resources.shared_execution import is_busy
            emit({"type": "error", "request_id": request_id,
                  "reason": "admission_busy" if is_busy(exc) else type(exc).__name__})


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
