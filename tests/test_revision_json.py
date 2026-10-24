import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("revision_json", Path(__file__).resolve().parents[1] / "src/gamma/memory/revision_json.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RevisionJsonTest(unittest.TestCase):
    def test_stale_snapshot_rejected_without_changing_file_or_input(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            first = module.write_state(path, {"mode": "wake"})
            self.assertEqual(first["revision"], 1)
            stale = dict(first)
            saved = module.write_state(path, {**first, "mode": "sleep"})
            before = path.read_bytes()
            with self.assertRaises(module.StaleRevision):
                module.write_state(path, stale)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(saved["revision"], 2)
            self.assertEqual(stale["revision"], 1)

    def test_legacy_and_explicit_unversioned_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            path.write_text('{"mode":"sleep"}')
            saved = module.write_state(path, {"revision": 0, "mode": "wake"})
            self.assertEqual(saved["revision"], 1)
            replaced = module.write_state(path, {"mode": "sleep"})
            self.assertEqual(replaced["revision"], 2)
            self.assertEqual(json.loads(path.read_text()), replaced)

    def test_corruption_and_invalid_revision_never_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            for contents in ('broken', '[]', '{"revision":true}', '{"revision":-1}'):
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    module.write_state(path, {"mode": "sleep"})
                self.assertEqual(path.read_text(), contents)
            path.write_text('{"revision":1}')
            for revision in (True, -1, "1"):
                with self.assertRaises(ValueError):
                    module.write_state(path, {"revision": revision})

    def test_failed_serialization_preserves_old_state_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            saved = module.write_state(path, {"mode": "sleep"})
            before = path.read_bytes()
            with self.assertRaises(TypeError):
                module.write_state(path, {**saved, "invalid": object()})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(root).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
