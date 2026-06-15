from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_launcher():
    path = Path(__file__).resolve().parents[1] / "scripts" / "start_qwen_tts_server.py"
    spec = importlib.util.spec_from_file_location("gamma_test_qwen_tts_launcher", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QwenTtsLauncherTest(unittest.TestCase):
    def test_qwen_python_honors_explicit_runtime(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            configured = repo_root / "selected-python"
            configured.touch()
            with patch.dict("os.environ", {"QWEN_TTS_PYTHON": str(configured)}, clear=False):
                self.assertEqual(launcher._qwen_python(repo_root), str(configured))

    def test_dependency_result_uses_selected_interpreter_not_launcher_environment(self) -> None:
        launcher = _load_launcher()
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/python", "-c", "import torch, qwen_tts"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with (
            patch.object(launcher, "_is_listening", return_value=False),
            patch.object(launcher, "_qwen_python", return_value="/usr/bin/python"),
            patch.object(launcher.subprocess, "run", return_value=completed),
            patch.object(launcher.subprocess, "Popen") as popen,
            patch.object(launcher.time, "time", side_effect=[0, 0, 91]),
            patch.object(launcher, "_health_ok", return_value=False),
        ):
            process = popen.return_value
            process.poll.return_value = None
            with self.assertRaisesRegex(SystemExit, "did not start within 90 seconds"):
                launcher.main()

        popen.assert_called_once()
