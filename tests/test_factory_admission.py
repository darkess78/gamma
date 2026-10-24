"""Separate improvement command processes must honor the core's durable lease."""
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from gamma.config import settings
from gamma.errors import ConfigurationError
from gamma.llm.factory import build_llm_adapter
from gamma.resources.admission import Admission


CHILD = r'''
import json, sys
from gamma.llm.factory import build_llm_adapter
from gamma.resources.admission import AdmissionBusy
from gamma.improvement import cli, worker
record = {}
adapter = None
def work(*args, **kwargs):
    global adapter
    adapter = build_llm_adapter()
    record['adapter'] = type(adapter).__name__
    try:
        record['text'] = adapter.generate_reply('system', 'fictional fixture').text
        record['outcome'] = 'completed'
    except AdmissionBusy:
        record['outcome'] = 'occupied'
    record['pid'] = adapter.status()['pid']
    if sys.argv[2] == 'fail':
        raise RuntimeError('controlled command failure')
    return 0
if sys.argv[1] == 'cli':
    cli.run = work
    command = lambda: cli.main([])
else:
    worker.run_worker = work
    command = lambda: worker.main(['--once'])
try:
    command()
except RuntimeError:
    record['command_failed'] = True
record['after'] = adapter.status()
print(json.dumps(record))
'''


def invoke(tmp_path, mode, fail=False):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "config/app.example.toml").write_text("")
    env = dict(os.environ, SHANA_PROJECT_ROOT=str(tmp_path), SHANA_LLM_PROVIDER="mock",
               SHANA_LLM_ROUTER_ENABLED="false", SHANA_BRAIN_MODEL_RUNTIME="worker",
               PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c", CHILD, mode, "fail" if fail else "ok"],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["cli", "worker"])
@pytest.mark.parametrize("state", ["active", "uncertain"])
def test_improvement_factory_cannot_bypass_another_process_lease(tmp_path, mode, state):
    admission = Admission(tmp_path / "data/runtime/model-admission.sqlite3")
    token = admission.acquire("brain-inference")
    if state == "uncertain":
        admission.uncertain("brain-inference", token)
    record = invoke(tmp_path, mode)
    assert record["adapter"] == "ModelRuntime"
    assert record["outcome"] == "occupied" and record["pid"] is None
    assert record["after"]["closed"] and not record["after"]["running"]
    assert admission.status("brain-inference") == {"resource": "brain-inference", "token": token, "state": state}


@pytest.mark.parametrize("mode", ["cli", "worker"])
@pytest.mark.parametrize("fail", [False, True])
def test_improvement_command_reaps_owned_inference_on_exit(tmp_path, mode, fail):
    record = invoke(tmp_path, mode, fail)
    assert record["outcome"] == "completed" and record["text"]
    assert record["pid"] and not psutil.pid_exists(record["pid"])
    assert record["after"]["closed"] and not record["after"]["running"]
    assert record["after"]["admission_state"] is None
    assert record.get("command_failed", False) == fail


def test_factory_rejects_unknown_mode_instead_of_direct_fallback(monkeypatch):
    monkeypatch.setattr(settings, "brain_model_runtime", "misspelled")
    with pytest.raises(ConfigurationError):
        build_llm_adapter()


def test_factory_keeps_router_callbacks_in_core(monkeypatch):
    from gamma.llm.router_adapter import RouterLLMAdapter
    from gamma.brain.model_runtime import RoutedModelAdapter
    monkeypatch.setattr(settings, "brain_model_runtime", "worker")
    monkeypatch.setattr(settings, "llm_router_enabled", True)
    adapter = build_llm_adapter()
    assert isinstance(adapter, RouterLLMAdapter)
    assert isinstance(adapter._build_provider_adapter("local"), RoutedModelAdapter)
