"""Exercise the actual persistent STT child with fictional text and stub STT."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gamma-stt-worker-") as directory:
        root = Path(directory)
        uploads = root / "data/runtime/uploads"
        uploads.mkdir(parents=True)
        shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
        shutil.copy(source / "pyproject.toml", root)
        audio = uploads / "fictional.txt"
        audio.write_text("A fictional persistent recognition check.")
        requests = [{"schema_version": 1, "request_id": str(i), "type": "transcribe", "input_path": str(audio)} for i in range(2)]
        requests.extend([{"schema_version": 1, "request_id": "status", "type": "status"},
                         {"schema_version": 1, "request_id": "stop", "type": "shutdown"}])
        env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"), SHANA_STT_PROVIDER="stub")
        result = subprocess.run([sys.executable, "-m", "gamma.voice.stt_worker"], cwd=root, env=env,
            input="".join(json.dumps(request) + "\n" for request in requests), text=True, capture_output=True, timeout=20, check=True)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        assert [f["type"] for f in frames] == ["ready", "result", "result", "status", "stopped"], frames
        assert len({f["pid"] for f in frames}) == 1 and frames[0]["pid"] != os.getpid()
        assert frames[1]["transcript"] == frames[2]["transcript"] == audio.read_text()
        assert frames[3]["model_loaded"] is True
        from gamma.voice.stt_client import STTWorkerClient
        import psutil
        client = STTWorkerClient([sys.executable, "-m", "gamma.voice.stt_worker"], cwd=root, env=env, timeout=20)
        try:
            assert client.transcribe_audio(str(audio)) == audio.read_text()
            pid = client.status()["pid"]
            assert client.transcribe_audio(str(audio)) == audio.read_text()
            assert client.status()["pid"] == pid and psutil.pid_exists(pid)
        finally:
            client.close()
        assert not psutil.pid_exists(pid)
        integration = '''
import asyncio, io, json
from unittest.mock import Mock
import psutil
from fastapi import UploadFile
from gamma.voice.roundtrip import VoiceRoundtripService
from gamma.api import routes
service = VoiceRoundtripService(conversation=Mock())
async def verify():
    results = []
    pids = []
    for _ in range(2):
        response = await service.run_transcription(audio_file=UploadFile(filename="fixture.txt", file=io.BytesIO(b"A fictional worker service check.")))
        results.append(response.transcript)
        pids.append(service._stt.status()["pid"])
    assert results == ["A fictional worker service check."] * 2, results
    assert pids[0] == pids[1] and psutil.pid_exists(pids[0])
    routes.voice_roundtrip_service._value = service
    await asyncio.to_thread(routes.stop_voice_transcription)
    assert not psutil.pid_exists(pids[0])
    try:
        await service.run_transcription(audio_file=UploadFile(filename="late.txt", file=io.BytesIO(b"late")))
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed service accepted transcription")
    print(json.dumps({"service_worker_reused": True, "shutdown_reaped_child": True, "closed_service_rejected": True}))
try:
    asyncio.run(verify())
finally:
    service.close()
'''
        app_result = subprocess.run([sys.executable, "-c", integration], cwd=root,
            env=dict(env, SHANA_STT_RUNTIME="worker", SHANA_AUDIO_UNDERSTANDING_ENABLED="false"),
            text=True, capture_output=True, timeout=30)
        assert app_result.returncode == 0, app_result.stderr
        service_evidence = json.loads(app_result.stdout.splitlines()[-1])
        print(json.dumps({"scope": "real child process with synthetic stub recognition", "requests_completed": 2,
            "same_worker_pid": True, "client_requests_completed": 2, "client_child_reaped": True,
            "graceful_shutdown": result.returncode == 0, "application_service": service_evidence}, indent=2))


if __name__ == "__main__":
    main()
