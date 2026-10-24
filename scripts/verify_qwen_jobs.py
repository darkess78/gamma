"""Exercise the real Qwen HTTP handler without loading an inference model."""
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
import importlib.util
import json
from pathlib import Path
import threading
from unittest.mock import patch

import httpx


def main():
    spec = importlib.util.spec_from_file_location("qwen_fixture", Path(__file__).with_name("qwen_tts_server.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("dotenv.load_dotenv"):
        spec.loader.exec_module(module)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def synthesize(body):
        calls.append(body)
        entered.set()
        assert release.wait(10)
        return b"fixture wave bytes"
    module.synthesize = synthesize
    class QuietServer(HTTPServer):
        def handle_error(self, request, address):
            # The client intentionally disconnects while synthesis is active.
            pass
    server = QuietServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base, identity = f"http://127.0.0.1:{server.server_port}", "a" * 32
    try:
        with httpx.Client(base_url=base, trust_env=False, timeout=3) as client:
            generation = client.get("/health").json()["generation"]
            def request():
                try:
                    client.post("/tts", headers={"X-Shana-Job-ID": identity}, json={"text": "fictional text"}, timeout=0.2)
                except httpx.ReadTimeout:
                    return
                raise AssertionError("expected timeout")
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(request)
                assert entered.wait(3)
                future.result(3)
                assert module.JOBS.status(identity)["state"] == "active"
                release.set()
            record = client.get("/jobs/" + identity).json()
            assert record == {"schema_version": 1, "generation": generation, "job_id": identity, "state": "completed"}
            assert client.post("/tts", headers={"X-Shana-Job-ID": identity}, json={"text": "duplicate"}).status_code == 409
            assert len(calls) == 1
            assert client.get("/jobs/" + "b" * 32).json()["state"] == "unknown"
            assert client.get("/jobs/bad").status_code == 400
        print(json.dumps({"scope": "real Qwen HTTP handler with controlled synthesis", "active_after_client_timeout": True,
                          "terminal_evidence_after_completion": True, "duplicate_execution_prevented": True}, indent=2))
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    main()
