"""Bounded real Whisper GPU check using generated fictional speech."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import psutil

from gamma.voice.stt_client import STTWorkerClient
from transcript_distance import word_error_rate


def command(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--gpu", type=int, default=0)
    args = parser.parse_args()
    model = args.model.resolve(strict=True)
    assert (model / "model.bin").is_file()
    production = command("systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID")
    free = int(command("nvidia-smi", "-i", str(args.gpu), "--query-gpu=memory.free", "--format=csv,noheader,nounits"))
    assert free >= 2048, "insufficient free GPU memory for bounded base.en check"
    gpu_name = command("nvidia-smi", "-i", str(args.gpu), "--query-gpu=name,uuid", "--format=csv,noheader")
    source = Path(__file__).resolve().parents[1]
    phrase = "The quiet garden has seven yellow flowers. Tomorrow we will walk beside the river."
    with tempfile.TemporaryDirectory(prefix="gamma-stt-gpu-") as directory:
        root = Path(directory)
        uploads = root / "data/runtime/uploads"
        uploads.mkdir(parents=True)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        shutil.copy(source / "pyproject.toml", root)
        text = root / "fixture.txt"
        text.write_text(phrase)
        audio = uploads / "fixture.wav"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            f"flite=textfile={text}:voice=slt", "-ar", "16000", str(audio)], check=True, timeout=20)
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
            SHANA_STT_PROVIDER="faster-whisper", SHANA_STT_MODEL=str(model),
            SHANA_STT_DEVICE="cuda:0", SHANA_STT_DEVICE_INDEX="0", SHANA_STT_COMPUTE_TYPE="float16",
            CUDA_VISIBLE_DEVICES=str(args.gpu), HF_HUB_OFFLINE="1")
        client = STTWorkerClient([sys.executable, "-m", "gamma.voice.stt_worker"], cwd=root, env=env, timeout=90)
        results = []
        try:
            for label in ("first_request_including_load", "repeated_request"):
                start = time.perf_counter()
                transcript = client.transcribe_audio(str(audio))
                duration = time.perf_counter() - start
                pid = client.status()["pid"]
                runtime = client.status()["runtime"]
                assert runtime["provider"] == "faster-whisper" and runtime["device"] == "cuda", runtime
                allocations = command("nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory", "--format=csv,noheader,nounits")
                owned = [row.strip() for row in allocations.splitlines() if row.split(",")[0].strip() == str(pid)]
                assert owned and any(int(row.split(",")[-1].strip()) > 0 for row in owned), "no GPU allocation for actual worker"
                error = word_error_rate(phrase, transcript)
                assert error <= 0.25, (transcript, error)
                results.append(dict(run=label, seconds=round(duration, 3), transcript=transcript,
                                    word_error_rate=round(error, 4), pid=pid, runtime=runtime, gpu_allocations=owned))
            assert results[0]["pid"] == results[1]["pid"]
        finally:
            pid = client.status()["pid"]
            client.close()
        assert pid is not None and not psutil.pid_exists(pid)
        assert production == command("systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID")
        print(json.dumps(dict(scope="real cached Whisper base.en GPU inference on generated speech",
            gpu=gpu_name, free_mib_before=free, model_sha256=hashlib.sha256((model / "model.bin").read_bytes()).hexdigest(),
            runs=results, child_reaped=True, production_pids_unchanged=True), indent=2))


if __name__ == "__main__":
    main()
