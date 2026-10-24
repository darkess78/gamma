"""Isolated cached Qwen synthesis, verified through real Whisper recognition."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil

from gamma.resources.admission import Admission
from gamma.system.cuda_env import prepend_cuda_library_path
from gamma.voice.stt_client import STTWorkerClient
from gamma.voice.tts_client import TTSWorkerClient
from gamma.voice.tts_runtime import TTSRuntime
from pcm_metrics import pcm_metrics
from transcript_distance import word_error_rate


def command(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--tts-python", type=Path, required=True)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--protected-pid", type=int, action="append", default=[])
    args = parser.parse_args()
    protected = {pid: psutil.Process(pid).create_time() for pid in args.protected_pid}
    production = command("systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID")
    assert int(command("nvidia-smi", "-i", str(args.gpu), "--query-gpu=memory.free", "--format=csv,noheader,nounits")) >= 8192
    assert psutil.virtual_memory().available >= 8 * 1024**3
    source = Path(__file__).resolve().parents[1]
    phrases = ["The quiet garden has seven yellow flowers.", "Tomorrow we will walk beside the river."]
    with tempfile.TemporaryDirectory(prefix="gamma-tts-gpu-") as directory:
        root = Path(directory)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        shutil.copy(source / "pyproject.toml", root)
        scripts = root / "scripts"
        scripts.mkdir()
        for name in ("qwen_tts_server.py", "qwen_jobs.py"):
            shutil.copy(source / "scripts" / name, scripts)
        uploads = root / "data/runtime/uploads"
        uploads.mkdir(parents=True)
        reference = uploads / "reference.wav"
        text = root / "reference.txt"
        text.write_text(" ".join(phrases))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            f"flite=textfile={text}:voice=slt", "-ar", "24000", str(reference)], check=True, timeout=20)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
            CUDA_VISIBLE_DEVICES=str(args.gpu), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
            QWEN_TTS_MODEL=str(args.model.resolve(strict=True)), QWEN_TTS_DTYPE="bfloat16", QWEN_TTS_DEVICE="cuda:0",
            QWEN_TTS_LOCAL_FILES_ONLY="1", QWEN_TTS_HOST="127.0.0.1", QWEN_TTS_PORT=str(port),
            SHANA_TTS_PROVIDER="qwen-tts", SHANA_TTS_PROFILE="isolated_fixture_no_profile", SHANA_RVC_ENABLED="false",
            SHANA_QWEN_TTS_ENDPOINT=base + "/tts", SHANA_QWEN_TTS_REFERENCE_AUDIO=str(reference),
            SHANA_QWEN_TTS_REFERENCE_TEXT=" ".join(phrases), SHANA_QWEN_TTS_LANGUAGE="English",
            SHANA_QWEN_TTS_EXTRA_JSON=json.dumps({"do_sample": False}),
            SHANA_STT_PROVIDER="faster-whisper", SHANA_STT_MODEL=str(args.whisper_model.resolve(strict=True)),
            SHANA_STT_DEVICE="cuda:0", SHANA_STT_DEVICE_INDEX="0", SHANA_STT_COMPUTE_TYPE="float16")
        admission = Admission(root / "admission.db")
        tts = TTSWorkerClient([sys.executable, "-m", "gamma.voice.tts_worker"], cwd=root, env=env,
                             artifact_root=root / "data/runtime/tts-worker-artifacts", timeout=90)
        runtime = TTSRuntime(tts, admission, receipt_root=root / "data/runtime/tts-job-receipts")
        stt = STTWorkerClient([sys.executable, "-m", "gamma.voice.stt_worker"], cwd=root, env=env, timeout=90)
        started = time.perf_counter()
        with (root / "server.log").open("wb") as log:
            server = subprocess.Popen([str(args.tts_python), str(scripts / "qwen_tts_server.py")], cwd=root,
                env=prepend_cuda_library_path(dict(env)), stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=2) as http:
                    deadline = time.monotonic() + 90
                    while True:
                        try:
                            health = http.get("/health")
                            if health.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if server.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError((root / "server.log").read_text()[-6000:])
                        time.sleep(0.1)
                    load_seconds = round(time.perf_counter() - started, 3)
                    assert health.json()["device"] == "cuda:0"
                    results = []
                    for phrase in phrases:
                        started = time.perf_counter()
                        result = runtime.synthesize(phrase)
                        seconds = round(time.perf_counter() - started, 3)
                        metrics = pcm_metrics(result.audio_path)
                        assert 0.2 < metrics["duration_seconds"] < 30 and metrics["rms"] > 0.002
                        audio = uploads / "recognize.wav"
                        shutil.copy(result.audio_path, audio)
                        transcript = stt.transcribe_audio(str(audio))
                        assert stt.status()["runtime"]["device"] == "cuda"
                        error = word_error_rate(phrase, transcript)
                        assert error <= 0.3, (phrase, transcript, error)
                        assert admission.status("tts") is None
                        results.append(dict(seconds=seconds, expected=phrase, transcript=transcript, word_error_rate=error, **metrics))
                    allocations = command("nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory", "--format=csv,noheader,nounits")
                    assert any(row.split(",")[0].strip() == str(server.pid) for row in allocations.splitlines())
            finally:
                tts_pid, stt_pid = tts.status()["pid"], stt.status()["pid"]
                try:
                    runtime.close()
                finally:
                    try:
                        stt.close()
                    finally:
                        server.terminate()
                        try:
                            server.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)
        assert not any(pid and psutil.pid_exists(pid) for pid in (server.pid, tts_pid, stt_pid))
        assert all(psutil.Process(pid).create_time() == birth for pid, birth in protected.items())
        assert production == command("systemctl", "--user", "show", "gamma-shana", "gamma-dashboard", "-p", "MainPID")
        print(json.dumps(dict(scope="isolated cached Qwen GPU synthesis and Whisper GPU readback using a synthetic reference voice",
            server_load_seconds=load_seconds, runs=results, children_reaped=True, production_unchanged=True), indent=2))


if __name__ == "__main__":
    main()
