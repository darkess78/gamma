"""Prove an adapter timeout retains admission while remote synthesis continues."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

from gamma.resources.admission import Admission, AdmissionBusy
from gamma.voice.tts_client import TTSWorkerClient
from gamma.voice.tts_runtime import TTSRuntime


def main():
    source = Path(__file__).resolve().parents[1]
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed_ids = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            identity = self.path.rsplit("/", 1)[-1]
            state = ("failed" if finished.is_set() else "active") if identity in observed_ids else "unknown"
            body = json.dumps({"schema_version": 1, "generation": "b" * 32, "job_id": identity, "state": state}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            observed_ids.append(self.headers.get("X-Shana-Job-ID"))
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            try:
                assert release.wait(15)
                try:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"controlled fixture failure")
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                finished.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="gamma-tts-uncertain-") as directory:
            root = Path(directory)
            shutil.copytree(source / "config", root / "config", ignore=shutil.ignore_patterns("*.local.toml"))
            shutil.copy(source / "pyproject.toml", root)
            env = dict(os.environ, SHANA_PROJECT_ROOT=str(root), PYTHONPATH=str(source / "src"),
                       SHANA_TTS_PROVIDER="qwen-tts", SHANA_RVC_ENABLED="false", SHANA_QWEN_TTS_ENDPOINT=f"http://127.0.0.1:{server.server_port}/tts")
            client = TTSWorkerClient([sys.executable, "-m", "gamma.voice.tts_worker"], cwd=root, env=env,
                                    artifact_root=root / "data/runtime/tts-worker-artifacts", timeout=20)
            admission = Admission(root / "admission.db")
            runtime = TTSRuntime(client, admission, receipt_root=root / "data/runtime/tts-job-receipts")
            try:
                client.start()
                client.timeout = 2
                with ThreadPoolExecutor(1) as pool:
                    request = pool.submit(runtime.synthesize, "A fictional remote timeout check.")
                    assert entered.wait(5), "remote fixture never received synthesis"
                    try:
                        request.result(5)
                    except Exception:
                        pass
                    else:
                        raise AssertionError("adapter did not time out")
                assert not finished.is_set() and not client.status()["running"]
                reopened = Admission(root / "admission.db")
                assert reopened.status("tts")["state"] == "uncertain"
                assert observed_ids == [reopened.status("tts")["token"]]
                assert reopened.status("voice-inference")["token"] == observed_ids[0]
                assert reopened.status("voice-inference")["state"] == "uncertain"
                try:
                    runtime.control("reconcile")
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("reconciled active remote work")
                try:
                    reopened.acquire("tts")
                except AdmissionBusy:
                    pass
                else:
                    raise AssertionError("timeout released occupied resource")
                release.set()
                assert finished.wait(5)
                assert reopened.status("tts")["state"] == "uncertain"
                recovered = runtime.control("reconcile")
                assert recovered["admission_state"] is None and recovered["enabled"] is False
                assert reopened.resources_for(observed_ids[0]) == []
                assert reopened.status("voice-inference") is None
            finally:
                release.set()
                runtime.close()
        print(json.dumps({"scope": "real TTS adapter and controlled HTTP synthesis server",
            "adapter_timed_out": True, "remote_work_outlived_adapter": True,
            "durable_lease_retained": True, "automatic_replay_prevented": True,
            "lease_identity_reached_provider": True, "reconciled_only_after_terminal_evidence": True}, indent=2))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    main()
