"""TTS adapter over the shared owned-process transport."""
from pathlib import Path

from ..delivery.artifacts import verified_bytes
from .tts import TTSResult
from .tts_protocol import validate_synthesis
from .worker_client import PersistentWorkerClient


class TTSWorkerClient(PersistentWorkerClient):
    def __init__(self, command, *, artifact_root, **kwargs):
        super().__init__(command, **kwargs)
        self.artifact_root = Path(artifact_root).resolve()

    def synthesize(self, text, emotion=None, styles=None, job_id=None):
        fields = validate_synthesis(dict(text=text, emotion=emotion, styles=styles, job_id=job_id))
        result = self.request("synthesize", **fields)
        path = Path(result["audio_path"])
        if path.is_symlink() or not path.resolve().is_relative_to(self.artifact_root):
            raise RuntimeError("TTS output outside artifact scope")
        if result.get("content_type") not in {"audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "audio/mp4"}:
            raise RuntimeError("invalid TTS audio type")
        verified_bytes(path, result["digest"], result["size"])
        return TTSResult(provider="worker", text=text, audio_path=str(path), content_type=result["content_type"],
                         metadata={"worker_pid": result["pid"], "audio_digest": result["digest"]})
