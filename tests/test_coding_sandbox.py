import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamma.coding.sandbox import build_practice_command


class CodingSandboxTests(unittest.TestCase):
    def test_command_has_no_host_home_network_or_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "candidate"
            venv = root / "trusted"
            workspace.mkdir()
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").touch()
            with patch("gamma.coding.sandbox.sys.platform", "linux"), patch("gamma.coding.sandbox.shutil.which", side_effect=lambda x: "/usr/bin/" + x):
                command = build_practice_command(workspace, module="test_feature", kind="feature", timeout_seconds=10, venv=venv)
        self.assertIn("--unshare-all", command)
        self.assertIn("--new-session", command)
        self.assertIn("--clearenv", command)
        self.assertIn("--die-with-parent", command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("/home", command)
        self.assertNotIn("/dev", command)
        self.assertNotIn("bash", command)
        self.assertNotIn("sh", command)
        self.assertIn("/work", command)
        self.assertIn("--ro-bind", command)
        self.assertIn("--as=1073741824", command)

    def test_missing_sandbox_fails_closed(self):
        with patch("gamma.coding.sandbox.sys.platform", "linux"), patch("gamma.coding.sandbox.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                build_practice_command(Path("."), module="feature", kind="feature", timeout_seconds=10)

    def test_unsafe_module_rejected(self):
        with self.assertRaises(ValueError):
            build_practice_command(Path("."), module="feature; rm", kind="feature", timeout_seconds=10)


if __name__ == "__main__":
    unittest.main()
