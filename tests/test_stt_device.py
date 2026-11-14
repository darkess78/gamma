"""Choosing the speech-to-text device when the main venv has no torch (ctranslate2 decides)."""
import sys
from types import SimpleNamespace

from gamma.voice import stt


def _with_devices(monkeypatch, count):
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: count))


def test_cuda_index_is_used_when_ctranslate2_sees_it(monkeypatch):
    _with_devices(monkeypatch, 2)
    assert stt._ctranslate2_device("cuda", 1) == ("cuda", 1)
    assert stt._ctranslate2_device("cuda:1", None) == ("cuda", 1)
    assert stt._ctranslate2_device("auto", "") == ("cuda", 0)


def test_missing_gpu_or_cpu_setting_falls_back_to_cpu(monkeypatch):
    _with_devices(monkeypatch, 1)
    assert stt._ctranslate2_device("cuda", 1) == ("cpu", None)  # only device 0 exists
    assert stt._ctranslate2_device("cpu", 1) == ("cpu", None)
    _with_devices(monkeypatch, 0)
    assert stt._ctranslate2_device("cuda", 0) == ("cpu", None)


def test_audio_decodes_with_the_installed_pyav(tmp_path):
    """faster-whisper 1.2.1's own decoder breaks on PyAV 16+; ours must work with what's installed."""
    import math
    import struct
    import wave
    import numpy as np
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<hh", int(9000 * math.sin(i / 5)), 0) for i in range(24000)))
    audio = stt.decode_audio(str(path))
    assert audio.dtype == np.float32 and abs(len(audio) - 16000) < 400  # 1 s resampled to 16 kHz mono
    assert 0.05 < float(np.abs(audio).max()) <= 1.0
