from unittest.mock import Mock

import pytest

from gamma.brain.queue import BrainQueueFull
from gamma.resources.admission import Admission, AdmissionBusy
from gamma.voice.stt_runtime import STTRuntime
from gamma.voice.tts_runtime import TTSRuntime


def test_bundle_conflict_rolls_back_every_partial_claim(tmp_path):
    admission = Admission(tmp_path / "leases.db")
    held = admission.acquire("z-shared")
    with pytest.raises(AdmissionBusy):
        admission.acquire_many(["a-private", "z-shared"])
    assert admission.status("a-private") is None
    assert admission.status("z-shared")["token"] == held


@pytest.mark.parametrize("operation", ["release_many", "uncertain_many"])
def test_wrong_owner_rolls_back_bundle_update(tmp_path, operation):
    admission = Admission(tmp_path / "leases.db")
    token = admission.acquire_many(["a", "b"])
    admission.release("b", token)
    replacement = admission.acquire("b")
    with pytest.raises(ValueError):
        getattr(admission, operation)(["a", "b"], token)
    assert admission.status("a")["token"] == token and admission.status("a")["state"] == "active"
    assert admission.status("b")["token"] == replacement


def test_stt_cannot_enter_while_tts_holds_shared_capacity(tmp_path):
    admission = Admission(tmp_path / "leases.db")
    recognizer = Mock()
    recognizer.status.return_value = {"running": False}
    stt = STTRuntime(recognizer, admission=admission)
    synthesizer = Mock()
    def synthesize(**kwargs):
        with pytest.raises(BrainQueueFull):
            stt.transcribe_audio("fixture")
        recognizer.transcribe_audio.assert_not_called()
        assert admission.status("stt") is None
        return "audio"
    synthesizer.synthesize.side_effect = synthesize
    assert TTSRuntime(synthesizer, admission).synthesize("fixture") == "audio"
    stt.transcribe_audio("fixture")
    assert admission.status("voice-inference") is None


@pytest.mark.parametrize("running", [True, False])
def test_stt_failure_retains_capacity_until_child_exit_is_known(tmp_path, running):
    admission = Admission(tmp_path / "leases.db")
    client = Mock()
    client.status.return_value = {"running": running}
    client.transcribe_audio.side_effect = RuntimeError("fixture")
    with pytest.raises(RuntimeError):
        STTRuntime(client, admission=admission).transcribe_audio("fixture")
    state = admission.status("voice-inference")
    assert (state is not None) is running
    if running:
        assert state["state"] == "uncertain"
