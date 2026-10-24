from unittest.mock import Mock

import pytest

from gamma.brain.model_runtime import ModelRuntime
from gamma.resources.admission import Admission, AdmissionBusy


def test_completed_jobs_release_capacity_and_closed_rejects(tmp_path):
    client = Mock()
    client.request.return_value = {"text": "fictional reply"}
    admission = Admission(tmp_path / "leases.db")
    runtime = ModelRuntime(client, admission)
    assert runtime.generate_reply("system", "hello").text == "fictional reply"
    assert admission.status("brain-inference") is None
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.generate_reply("system", "second")
    assert client.request.call_count == 1


def test_uncertain_provider_holds_capacity_across_runtime_reconstruction(tmp_path):
    admission = Admission(tmp_path / "leases.db")
    client = Mock()
    client.request.side_effect = TimeoutError("provider may still be computing")
    runtime = ModelRuntime(client, admission)
    with pytest.raises(TimeoutError):
        runtime.generate_reply("system", "hello")
    assert admission.status("brain-inference")["state"] == "uncertain"
    runtime.close()
    replacement = ModelRuntime(Mock(), Admission(tmp_path / "leases.db"))
    replacement.client.status.return_value = {"running": False, "busy": False}
    with pytest.raises(AdmissionBusy):
        replacement.generate_reply("system", "second")
    replacement.client.request.assert_not_called()
    for action in ["start", "restart"]:
        with pytest.raises(RuntimeError, match="occupied"):
            replacement.control(action)
    replacement.control("stop")
    assert admission.status("brain-inference")["state"] == "uncertain"


@pytest.mark.parametrize("kwargs", [{"image_inputs": [object()]}, {"endpoint_override": "http://invalid"},
                                    {"user_text": "\x01" * 12000}])
def test_invalid_or_escaped_oversize_input_acquires_nothing(tmp_path, kwargs):
    client = Mock()
    admission = Admission(tmp_path / "leases.db")
    runtime = ModelRuntime(client, admission)
    with pytest.raises(ValueError):
        runtime.generate_reply(**{"system_prompt": "system", "user_text": "hello", **kwargs})
    assert admission.status("brain-inference") is None
    client.request.assert_not_called()


def test_stop_disables_lazy_restart_and_start_failure_stays_disabled(tmp_path):
    client = Mock()
    client.status.return_value = {"running": False, "busy": False}
    runtime = ModelRuntime(client, Admission(tmp_path / "leases.db"))
    assert runtime.control("stop")["enabled"] is False
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.generate_reply("system", "hello")
    client.request.assert_not_called()
    client.start.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        runtime.control("start")
    assert runtime.status()["enabled"] is False


def test_busy_controls_do_not_wait_or_release_active_job(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, finish = threading.Event(), threading.Event()
    client = Mock()
    def reply(*args, **kwargs):
        entered.set()
        assert finish.wait(3)
        return {"text": "fixture"}
    client.request.side_effect = reply
    admission = Admission(tmp_path / "leases.db")
    runtime = ModelRuntime(client, admission)
    with ThreadPoolExecutor() as pool:
        result = pool.submit(runtime.generate_reply, "system", "hello")
        try:
            assert entered.wait(2)
            for action in ["start", "stop", "restart"]:
                with pytest.raises(RuntimeError, match="busy"):
                    runtime.control(action)
            assert admission.status("brain-inference")["state"] == "active"
            client.close.assert_not_called()
        finally:
            finish.set()
        assert result.result().text == "fixture"
