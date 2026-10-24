import base64
import io
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from gamma.brain.image_payloads import image_root, load_images, staged_images
from gamma.brain.image_protocol import validate_manifest
from gamma.brain.model_runtime import ModelRuntime
from gamma.brain.model_worker import serve
from gamma.config import settings
from gamma.llm.base import LLMImageInput
from gamma.resources.admission import Admission
from gamma.voice.worker_client import PersistentWorkerClient
from gamma.vision.service import VisionService, VisionImage

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=")


def test_private_snapshot_hash_validation_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    image = LLMImageInput(PNG, "image/png", "private-name.png")
    with staged_images([image]) as manifest:
        assert "private-name" not in json.dumps(manifest)
        path = image_root() / (manifest[0]["id"] + ".bin")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert load_images(manifest)[0].data == PNG
        path.write_bytes(b"x" * len(PNG))
        with pytest.raises(ValueError):
            load_images(manifest)
    assert not list(image_root().iterdir())


def test_symlink_and_oversized_manifests_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with staged_images([LLMImageInput(PNG, "image/png")]) as manifest:
        path = image_root() / (manifest[0]["id"] + ".bin")
        path.unlink()
        outside = tmp_path / "outside"
        outside.write_bytes(PNG)
        path.symlink_to(outside)
        with pytest.raises(ValueError):
            load_images(manifest)
        with pytest.raises(ValueError):
            validate_manifest(manifest * 2)
        for replacement in [{"id": "../../outside"}, {"size": True}, {"size": 8000001}, {"media_type": "text/plain"}]:
            with pytest.raises(ValueError):
                validate_manifest([{**manifest[0], **replacement}])
    assert outside.read_bytes() == PNG


def test_tampered_image_never_constructs_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "local_llm_supports_vision", True)
    with staged_images([LLMImageInput(PNG, "image/png")]) as manifest:
        manifest[0]["sha256"] = "0" * 64
        request = dict(schema_version=1, request_id="fixture", type="generate", provider="local",
                       system_prompt="system", user_text="fixture", images=manifest)
        factory = Mock()
        output = io.BytesIO()
        serve(io.BytesIO((json.dumps(request) + "\n").encode()), output, adapter_factory=factory)
        assert json.loads(output.getvalue().splitlines()[-1])["type"] == "error"
        factory.assert_not_called()


def test_invalid_image_releases_presubmit_capacity_and_timeout_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "local_llm_supports_vision", True)
    monkeypatch.setattr(settings, "llm_provider", "local")
    client = Mock()
    runtime = ModelRuntime(client, Admission(tmp_path / "leases.db"))
    with pytest.raises(ValueError):
        runtime.generate_reply("system", "fixture", [LLMImageInput(b"", "image/png")])
    client.request.assert_not_called()
    assert runtime.admission.status("brain-inference") is None
    client.request.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        runtime.generate_reply("system", "fixture", [LLMImageInput(PNG, "image/png")])
    assert not list(image_root().iterdir())
    assert runtime.admission.status("brain-inference")["state"] == "uncertain"


def test_real_child_passes_exact_image_to_capable_provider(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/app.example.toml").write_text("")
    requests = []
    capability = {"vision": True}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            payload = {"capabilities": ["vision"] if capability["vision"] else []} if self.path == "/api/show" else {
                "response": json.dumps({"image_type": "drawing", "summary": "A synthetic pixel", "confidence": 1})}
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "local_llm_endpoint", endpoint)
    monkeypatch.setattr(settings, "local_llm_supports_vision", True)
    monkeypatch.setattr(settings, "llm_provider", "local")
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), SHANA_LOCAL_LLM_ENDPOINT=endpoint,
               SHANA_LLM_PROVIDER="local", SHANA_LOCAL_LLM_SUPPORTS_VISION="true",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    client = PersistentWorkerClient([sys.executable, "-m", "gamma.brain.model_worker"], cwd=tmp_path, env=env, timeout=10)
    runtime = ModelRuntime(client, Admission(tmp_path / "leases.db"))
    pid = None
    try:
        analysis = VisionService().analyze_image(llm_adapter=runtime, image=VisionImage(PNG, "image/png"))
        assert analysis.summary == "A synthetic pixel"
        pid = client.status()["pid"]
        assert [path for path, _ in requests] == ["/api/show", "/api/generate"]
        assert base64.b64decode(requests[-1][1]["images"][0]) == PNG
        assert not list(image_root().iterdir())
        assert runtime.admission.status("brain-inference") is None
        capability["vision"] = False
        with pytest.raises(Exception):
            VisionService().analyze_image(llm_adapter=runtime, image=VisionImage(PNG, "image/png"))
        assert [path for path, _ in requests] == ["/api/show", "/api/generate", "/api/show"]
        assert not list(image_root().iterdir())
        assert runtime.admission.status("brain-inference") is None
        assert runtime.generate_reply("system", "ordinary text still works").text
        assert client.status()["pid"] == pid
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert not psutil.pid_exists(pid)
