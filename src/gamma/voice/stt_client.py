"""Persistent speech recognition client."""
from .worker_client import PersistentWorkerClient


class STTWorkerClient(PersistentWorkerClient):
    def status(self):
        state = super().status()
        state["runtime"] = getattr(self, "_runtime_info", None) if state["running"] and state["pid"] == getattr(self, "_runtime_pid", None) else None
        return state

    def transcribe_audio(self, path):
        path = str(path)
        if len(path) > 4096:
            raise ValueError("invalid STT path")
        with self._operation():
            try:
                self._start()
                identity = self._send("transcribe", input_path=path)
                result = self._receive("result", identity)
                if not isinstance(result.get("transcript"), str):
                    raise RuntimeError("invalid STT transcript")
                self._runtime_pid = result["pid"]
                self._runtime_info = result.get("runtime") if isinstance(result.get("runtime"), dict) else None
                return result["transcript"]
            except Exception:
                self._terminate()
                raise
