"""Persistent synthesis child. Only the core may authorize publication."""
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import socket
import psutil

from ..config import settings
from ..delivery.artifacts import snapshot as snapshot_audio
from .tts import TTSService
from .tts_protocol import validate_synthesis
from .tts_receipts import save_receipt


def output_record(result):
    source = Path(result.audio_path)
    if source.is_symlink() or not source.resolve().is_relative_to(settings.audio_output_dir.resolve()):
        raise ValueError("invalid synthesis output")
    if result.content_type not in {"audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "audio/mp4"}:
        raise ValueError("invalid audio type")
    path, digest, size = snapshot_audio(source, settings.data_dir / "runtime/tts-worker-artifacts")
    return {"audio_path": str(path), "digest": digest, "size": size, "content_type": result.content_type}


def serve(input_stream, output_stream, *, service_factory=TTSService):
    service = None
    def emit(payload):
        raw = (json.dumps({"schema_version": 1, "pid": os.getpid(), **payload}) + "\n").encode()
        if len(raw) > 65536:
            raise ValueError("response exceeds limit")
        output_stream.write(raw)
        output_stream.flush()
    emit({"type": "ready", "model_loaded": False})
    while True:
        line = input_stream.readline(65537)
        if not line:
            return
        if len(line) > 65536 or not line.endswith(b"\n"):
            emit({"type": "error", "reason": "invalid_frame"})
            return
        identity = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or type(request.get("schema_version")) is not int or request["schema_version"] != 1:
                raise ValueError("invalid schema")
            identity = request.get("request_id")
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
                identity = None
                raise ValueError("invalid identity")
            if request.get("type") == "shutdown":
                emit({"type": "stopped", "request_id": identity})
                return
            if request.get("type") != "synthesize":
                raise ValueError("invalid request")
            arguments = validate_synthesis(request)
            from ..resources.shared_execution import work_request
            with contextlib.redirect_stdout(sys.stderr), work_request(identity):
                if service is None:
                    service = service_factory()
                job_id = arguments.get("job_id")
                receipt = None
                if job_id:
                    receipt = {"schema_version": 1, "host": socket.gethostname(), "pid": os.getpid(),
                        "birth": psutil.Process().create_time(), "phase": "started",
                        "provider": service._cfg.provider.strip().lower(), "rvc": bool(service._cfg.rvc_enabled),
                        "endpoint": service._cfg.qwen_tts_endpoint}
                    save_receipt(settings.data_dir / "runtime/tts-job-receipts", job_id, receipt)
                result = service.synthesize(**arguments)
                if receipt is not None:
                    receipt["phase"] = "completed"
                    save_receipt(settings.data_dir / "runtime/tts-job-receipts", job_id, receipt)
                record = output_record(result)
            emit({"type": "result", "request_id": identity, **record})
        except Exception as exc:
            from ..resources.shared_execution import is_busy
            emit({"type": "error", "request_id": identity,
                  "reason": "admission_busy" if is_busy(exc) else "synthesis_failed"})


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
