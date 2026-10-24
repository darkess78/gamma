"""Real persistent TTS process checks using a synthetic tone provider."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import wave

import psutil

from gamma.voice.tts_client import TTSWorkerClient


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gamma-tts-worker-") as directory:
        root = Path(directory)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        shutil.copy(source / "pyproject.toml", root)
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
                   SHANA_TTS_PROVIDER="stub", SHANA_TTS_RVC_ENABLED="false")
        client = TTSWorkerClient([sys.executable, "-m", "gamma.voice.tts_worker"], cwd=root, env=env,
                                artifact_root=root / "data/runtime/tts-worker-artifacts", timeout=30)
        try:
            first = client.synthesize("A fictional synthesis check.")
            pid = client.status()["pid"]
            second = client.synthesize("Another fictional check.")
            assert client.status()["pid"] == pid and psutil.pid_exists(pid)
            for result in (first, second):
                with wave.open(result.audio_path, "rb") as audio:
                    assert audio.getnframes() > 0 and audio.getframerate() > 0
            assert first.metadata["audio_digest"] != second.metadata["audio_digest"]
        finally:
            client.close()
        assert not psutil.pid_exists(pid)
        print(json.dumps({"scope": "real worker process with synthetic tone synthesis", "same_worker_pid": True,
                          "verified_audio_results": 2, "child_reaped": True}, indent=2))


if __name__ == "__main__":
    main()
