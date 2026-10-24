import os
import socket

import psutil
import pytest

from gamma.resources.admission import Admission
from gamma.voice import tts_reconcile as recovery
from gamma.voice.tts_receipts import save_receipt, load_receipt


def setup_receipt(tmp_path, **fields):
    admission = Admission(tmp_path / "leases.db")
    token = admission.acquire("tts")
    record = dict(schema_version=1, phase="started", provider="qwen-tts", rvc=False,
                  host=socket.gethostname(), pid=os.getpid(), birth=psutil.Process().create_time())
    record.update(fields)
    save_receipt(tmp_path, token, record)
    return admission, token


def test_live_worker_cannot_be_reconciled_even_with_completed_receipt(tmp_path):
    admission, token = setup_receipt(tmp_path, phase="completed")
    assert not recovery.reconcile(admission, tmp_path)
    assert admission.status("tts")["token"] == token


@pytest.mark.parametrize("fields", [{"rvc": True}, {"provider": "piper"}, {"phase": "unknown"}, {"schema_version": True}])
def test_unresolved_stages_keep_lease(tmp_path, monkeypatch, fields):
    admission, token = setup_receipt(tmp_path, **fields)
    monkeypatch.setattr(recovery, "worker_exited", lambda _: True)
    monkeypatch.setattr(recovery, "remote_completed", lambda *_: True)
    assert not recovery.reconcile(admission, tmp_path)
    assert admission.status("tts")["token"] == token


def test_completed_pipeline_includes_postprocessing(tmp_path, monkeypatch):
    admission, _ = setup_receipt(tmp_path, phase="completed", rvc=True)
    monkeypatch.setattr(recovery, "worker_exited", lambda _: True)
    assert recovery.reconcile(admission, tmp_path)
    assert admission.status("tts") is None


def test_stale_remote_proof_cannot_clear_replacement_lease(tmp_path, monkeypatch):
    admission, token = setup_receipt(tmp_path)
    monkeypatch.setattr(recovery, "worker_exited", lambda _: True)
    def replace(*_):
        admission.release("tts", token)
        admission.acquire("tts")
        return True
    monkeypatch.setattr(recovery, "remote_completed", replace)
    assert not recovery.reconcile(admission, tmp_path)
    assert admission.status("tts")["token"] != token


def test_receipt_rejects_oversize_and_symlink(tmp_path):
    token = "a" * 32
    with pytest.raises(ValueError):
        save_receipt(tmp_path, token, {"large": "x" * 8192})
    source = tmp_path / "source"
    source.write_text("{}")
    (tmp_path / (token + ".json")).symlink_to(source)
    with pytest.raises(ValueError):
        load_receipt(tmp_path, token)


def test_invalid_synthesis_does_not_occupy_admission(tmp_path):
    from unittest.mock import Mock
    from gamma.voice.tts_runtime import TTSRuntime
    admission = Admission(tmp_path / "leases.db")
    client = Mock()
    with pytest.raises(ValueError):
        TTSRuntime(client, admission).synthesize("x" * 16385)
    assert admission.status("tts") is None
    client.synthesize.assert_not_called()
