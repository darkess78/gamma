import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from gamma.brain import model_runtime
from gamma.config import settings
from gamma.resources.admission import Admission
from gamma.safety.llm_reviewer import SpeechLLMReviewer
from gamma.voice.worker_client import PersistentWorkerClient


@pytest.fixture
def reviewer_process(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/app.example.toml").write_text("")
    requests = []
    response = {"text": json.dumps(dict(action="allow", reason="fixture", confidence=1))}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            requests[-1]["request_path"] = self.path
            body = json.dumps({"response": response["text"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings, "brain_model_runtime", "worker")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_model", "review-fixture")
    monkeypatch.setattr(settings, "speech_filter_llm_temperature", 0.25)
    monkeypatch.setattr(settings, "local_llm_endpoint", endpoint)
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), SHANA_LOCAL_LLM_ENDPOINT=endpoint,
               SHANA_LLM_PROVIDER="mock", SHANA_LLM_ROUTER_ENABLED="false",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    client = PersistentWorkerClient([sys.executable, "-m", "gamma.brain.model_worker"], cwd=tmp_path, env=env, timeout=10)
    runtime = model_runtime.ModelRuntime(client, Admission(tmp_path / "leases.db"))
    monkeypatch.setattr(model_runtime, "get_model_worker", lambda: runtime)
    try:
        yield SpeechLLMReviewer(), runtime, requests, response
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not client.status()["running"]


def test_review_uses_existing_inference_process_and_preserves_settings(reviewer_process):
    reviewer, runtime, requests, response = reviewer_process
    assert runtime.generate_provider_reply("mock", "system", "hello").text
    pid = runtime.status()["pid"]
    verdict = reviewer.review("Fictional public speech")
    assert verdict.action == "allow" and verdict.reason == "fixture"
    assert requests[0]["model"] == "review-fixture"
    assert requests[0]["request_path"] == "/api/generate"
    assert requests[0]["options"] == {"temperature": 0.25}
    assert requests[0]["prompt"] == "Fictional public speech"
    assert runtime.status()["pid"] == pid
    assert runtime.admission.status("brain-inference") is None
    response["text"] = "{}"
    verdict = reviewer.review("Another fictional sentence")
    assert verdict.action == "block" and verdict.reason == "llm-reviewer-invalid"
    assert runtime.admission.status("brain-inference") is None


def test_stopped_or_occupied_review_never_uses_direct_http_fallback(reviewer_process):
    reviewer, runtime, requests, response = reviewer_process
    runtime.control("stop")
    assert reviewer.review("fixture").reason == "llm-reviewer-unavailable"
    assert requests == [] and not runtime.status()["running"]
    runtime.control("start")
    token = runtime.admission.acquire("brain-inference")
    runtime.admission.uncertain("brain-inference", token)
    assert reviewer.review("fixture").action == "block"
    assert requests == []
    assert runtime.admission.status("brain-inference")["state"] == "uncertain"


def test_busy_review_rejects_without_waiting_for_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "brain_model_runtime", "worker")
    monkeypatch.setattr(settings, "speech_filter_llm_enabled", True)
    monkeypatch.setattr(settings, "speech_filter_llm_model", "fixture")
    client = Mock()
    runtime = model_runtime.ModelRuntime(client, Admission(tmp_path / "leases.db"))
    monkeypatch.setattr(model_runtime, "get_model_worker", lambda: runtime)
    with runtime._lock:
        verdict = SpeechLLMReviewer().review("fixture")
    assert verdict.action == "block" and verdict.reason == "llm-reviewer-unavailable"
    client.request.assert_not_called()


def test_stopped_review_releases_no_stream_output(reviewer_process, tmp_path):
    from gamma.stream.brain import StreamBrain
    from gamma.stream.models import StreamActor, StreamInputEvent
    from gamma.stream.trace import StreamTraceStore
    from test_stream_brain import _FakeConversation, _RecordingDispatcher, _FakeSpeechSynthesizer
    reviewer, runtime, requests, response = reviewer_process
    event = StreamInputEvent(kind="chat_message", text="Shana say hello",
        actor=StreamActor(source="twitch", platform_id="fixture"),
        metadata={"twitch_controls": {"voice_enabled": True, "subtitles_enabled": True}})
    for stopped in [False, True]:
        if stopped:
            runtime.control("stop")
        synth = Mock(wraps=_FakeSpeechSynthesizer())
        dispatcher = _RecordingDispatcher()
        brain = StreamBrain(conversation=_FakeConversation(), trace_store=StreamTraceStore(tmp_path / f"{stopped}.jsonl"),
                            safety_reviewer=reviewer, speech_synthesizer=synth, output_dispatcher=dispatcher)
        result = brain.handle_event(event, synthesize_speech=True)
        if stopped:
            synth.synthesize.assert_not_called()
            assert dispatcher.calls == [] and not result.output_events
        else:
            synth.synthesize.assert_called_once()
            assert dispatcher.calls and result.output_events


@pytest.mark.parametrize("temperature", [True, float("nan"), float("inf"), -1, 2])
def test_invalid_review_temperature_does_not_reach_provider(monkeypatch, temperature):
    from gamma.safety import review_provider
    transport = Mock()
    monkeypatch.setattr(review_provider.request, "urlopen", transport)
    with pytest.raises(RuntimeError, match="^review provider unavailable$"):
        review_provider.generate_review("system", "fixture", "model", temperature, "http://localhost", 30)
    transport.assert_not_called()


def test_review_provider_bounds_bytes_and_preserves_timeout(monkeypatch):
    import io
    from gamma.safety import review_provider
    transport = Mock(return_value=io.BytesIO(json.dumps({"response": "é" * 8193}).encode()))
    monkeypatch.setattr(review_provider.request, "urlopen", transport)
    with pytest.raises(RuntimeError, match="^review provider unavailable$"):
        review_provider.generate_review("system", "fixture", "model", 0, "http://localhost", 120)
    assert transport.call_args.kwargs["timeout"] == 20
    transport.return_value = io.BytesIO(b"x" * 65537)
    with pytest.raises(RuntimeError, match="^review provider unavailable$"):
        review_provider.generate_review("system", "fixture", "model", 0, "http://localhost", 3)
    assert transport.call_args.kwargs["timeout"] == 3
