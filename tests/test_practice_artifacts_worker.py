"""Independent storage contract, also runnable in the worker's stdlib sandbox."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'src/gamma/coding/practice_artifacts.py'
spec = importlib.util.spec_from_file_location('practice_artifacts', MODULE)
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


class ArtifactTests(unittest.TestCase):
    def test_round_trip_immutable_and_detect_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            store = artifacts.ArtifactStore(Path(folder))
            value = {'files': {'solution.py': 'print("\u263a")\n'}, 'stage': 'candidate_saved'}
            ref = store.put(value)
            self.assertEqual(ref, store.put(value))
            self.assertEqual(store.get(ref), value)
            self.assertRegex(ref, r'^[a-f0-9]{64}$')
            target = Path(folder) / (ref + '.json')
            target.write_text(json.dumps({'changed': True}), encoding='utf-8')
            with self.assertRaises(ValueError):
                store.get(ref)
            with self.assertRaises(ValueError):
                store.put(value)

    def test_rejects_paths_bad_types_and_oversize(self):
        with tempfile.TemporaryDirectory() as folder:
            store = artifacts.ArtifactStore(Path(folder))
            for ref in ['../other', 'a' * 63, 'A' * 64, None]:
                with self.assertRaises(ValueError):
                    store.get(ref)
            for value in [[], None, {'x': float('nan')}, {'huge': 'a' * 262145}]:
                with self.assertRaises(ValueError):
                    store.put(value)

    def test_symlinks_never_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'real').mkdir()
            try:
                (base / 'alias').symlink_to(base / 'real', target_is_directory=True)
            except OSError:
                self.skipTest('symlinks unavailable')
            with self.assertRaises(ValueError):
                artifacts.ArtifactStore(base / 'alias' / 'child')
            store = artifacts.ArtifactStore(base / 'real')
            ref = store.put({'x': 1})
            target = base / 'real' / (ref + '.json')
            content = target.read_bytes()
            target.unlink()
            (base / 'external').write_bytes(content)
            target.symlink_to(base / 'external')
            with self.assertRaises(ValueError):
                store.get(ref)


if __name__ == '__main__':
    unittest.main()
