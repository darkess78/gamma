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


if __name__ == "__main__":
    unittest.main()
