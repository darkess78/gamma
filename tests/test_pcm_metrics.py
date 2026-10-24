import importlib.util
from pathlib import Path
import struct
import wave

import pytest

spec = importlib.util.spec_from_file_location("pcm_metrics", Path(__file__).resolve().parents[1] / "scripts/pcm_metrics.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_fixture(path, samples):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))


def test_known_amplitude_and_silence(tmp_path):
    path = tmp_path / "fixture.wav"
    write_fixture(path, [16384, -16384] * 8000)
    metrics = module.pcm_metrics(path)
    assert metrics == {"duration_seconds": 1, "rms": 0.5, "peak": 0.5, "sample_rate": 16000}
    write_fixture(path, [0] * 160)
    assert module.pcm_metrics(path)["rms"] == 0


def test_truncated_wave_is_not_accepted_as_valid_audio(tmp_path):
    path = tmp_path / "fixture.wav"
    write_fixture(path, [1000] * 160)
    path.write_bytes(path.read_bytes()[:-2])
    with pytest.raises(ValueError, match="sample count"):
        module.pcm_metrics(path)
