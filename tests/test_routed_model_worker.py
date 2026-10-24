"""Router callback and compacted retry through a real child and local HTTP provider."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest

from gamma.config import settings
from gamma.brain import model_runtime
from gamma.brain.route_protocol import validate_route
from gamma.llm.base import LLMCallContext
from gamma.llm.router_adapter import RouterLLMAdapter, RouteDecision, begin_route_trace, take_route_trace
from gamma.resources.admission import Admission
from gamma.voice.worker_client import PersistentWorkerClient


@pytest.mark.parametrize("endpoint", ["http://user:password@localhost", "http://localhost?query=secret",
    "http://localhost#secret", "http://localhost:secret", "file:///tmp/path", "http://unconfigured", "http://@localhost"])
def test_endpoint_validation_rejects_unconfigured_or_credential_routes(endpoint):
    with pytest.raises(ValueError, match="^invalid route$"):
        validate_route("local", endpoint, ["http://localhost"])


@pytest.mark.parametrize("rejection_status", [400, 413, 422, 500])
def test_router_compacts_and_retries_through_same_worker(tmp_path, monkeypatch, rejection_status):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/app.example.toml").write_text("")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            overflow = len(requests) == 1
            body = json.dumps({"error": "maximum context length exceeded"} if overflow else {"response": "fixture reply"}).encode()
            self.send_response(rejection_status if overflow else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings, "brain_model_runtime", "worker")
    monkeypatch.setattr(settings, "llm_router_enabled", True)
    monkeypatch.setattr(settings, "local_llm_endpoint", endpoint)
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), SHANA_LOCAL_LLM_ENDPOINT=endpoint,
               SHANA_LLM_PROVIDER="local", SHANA_LLM_ROUTER_ENABLED="true",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    transport = PersistentWorkerClient([sys.executable, "-m", "gamma.brain.model_worker"], cwd=tmp_path, env=env, timeout=10)
    runtime = model_runtime.ModelRuntime(transport, Admission(tmp_path / "leases.db"))
    monkeypatch.setattr(model_runtime, "get_model_worker", lambda: runtime)
    router = RouterLLMAdapter()
    monkeypatch.setattr(router, "_route_candidates", lambda **kw: [RouteDecision("local", "gpt-oss:20b", "fixture", endpoint=endpoint)])
    monkeypatch.setattr(router, "_active_resource_route_decision", lambda **kw: kw["decision"])
    monkeypatch.setattr(router, "_provider_availability", lambda **kw: {"ok": True})
    budgets = []
    def build(budget):
        budgets.append(budget)
        return f"budget {budget}", "fictional input", {}
    pid = None
    try:
        transport.start()
        pid = transport.status()["pid"]
        begin_route_trace(persist=False)
        if rejection_status == 500:
            with pytest.raises(Exception):
                router.generate_reply("unbuilt", "input", call_context=LLMCallContext(prompt_builder=build))
            assert len(requests) == 1
            assert runtime.admission.status("brain-inference")["state"] == "uncertain"
            with pytest.raises(RuntimeError, match="occupied"):
                runtime.control("restart")
            take_route_trace()
        else:
            reply = router.generate_reply("unbuilt", "input", call_context=LLMCallContext(prompt_builder=build))
            events = take_route_trace()
            assert reply.text == "fixture reply"
            assert len(budgets) == 2 and budgets[1] < budgets[0]
            assert [r["system"] for r in requests] == [f"budget {v}" for v in budgets]
            assert all(r["model"] == "gpt-oss:20b" for r in requests)
            assert [e["status"] for e in events] == ["context_overflow", "ok"]
            assert runtime.admission.status("brain-inference") is None
            assert router.generate_reply("next", "input").text == "fixture reply"
            assert transport.status()["pid"] == pid
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert not psutil.pid_exists(pid)
