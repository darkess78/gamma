from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.config import settings
from gamma.supervisor.manager import ProcessManager


class AudioSidecarRuntimeTest(unittest.TestCase):
    def test_manager_prefers_isolated_audio_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            python_path = project_root / ".venv-audio" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")
            with (
                patch.object(settings, "project_root", project_root),
                patch.object(settings, "audio_understanding_python", None),
            ):
                manager = ProcessManager()
                resolved = manager._service_python("audio-understanding")

        self.assertEqual(resolved, str(python_path))

    def test_manager_honors_explicit_audio_python(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            python_path = Path(temp_dir) / "python"
            python_path.write_text("", encoding="utf-8")
            with patch.object(settings, "audio_understanding_python", str(python_path)):
                manager = ProcessManager()
                resolved = manager._service_python("audio-understanding")

        self.assertEqual(resolved, str(python_path))

    def test_audio_understanding_startup_admission_only_sets_auto_devices(self) -> None:
        manager = ProcessManager()
        with (
            patch.object(settings, "audio_analysis_device", "cpu"),
            patch.object(settings, "speaker_emotion_device", "auto"),
            patch.object(settings, "audio_event_device", "cuda:0"),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._audio_understanding_admission_env()

        admitted.assert_called_once_with(
            provider="audio-understanding",
            kind="audio-understanding",
            modality="audio",
            model=None,
        )
        self.assertEqual(env, {"SHANA_SPEAKER_EMOTION_DEVICE": "cuda:1"})

    def test_audio_understanding_explicit_devices_skip_startup_admission(self) -> None:
        manager = ProcessManager()
        with (
            patch.object(settings, "audio_analysis_device", "cpu"),
            patch.object(settings, "speaker_emotion_device", "cuda:0"),
            patch.object(settings, "audio_event_device", "cpu"),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._audio_understanding_admission_env()

        admitted.assert_not_called()
        self.assertEqual(env, {})

    def test_qwen_startup_admission_sets_auto_device_only(self) -> None:
        manager = ProcessManager()
        with (
            patch.dict("os.environ", {"QWEN_TTS_DEVICE": "auto"}, clear=False),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_called_once_with(provider="qwen-tts", kind="qwen-tts", modality="speech", model="qwen-tts")
        self.assertEqual(env, {"QWEN_TTS_DEVICE": "cuda:1"})

        with (
            patch.dict("os.environ", {"QWEN_TTS_DEVICE": "cuda:0"}, clear=False),
            patch.object(manager, "_admitted_sidecar_device", return_value="cuda:1") as admitted,
        ):
            env = manager._qwen_tts_admission_env()

        admitted.assert_not_called()
        self.assertEqual(env, {})


if __name__ == "__main__":
    unittest.main()
