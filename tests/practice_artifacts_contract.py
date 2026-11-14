import importlib.util, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location('artifact', 'src/gamma/coding/practice_artifacts.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d:
    s = m.ArtifactStore(Path(d))
    ref = s.put({'files': {'x.py': 'pass\n'}})
    assert s.get(ref) == {'files': {'x.py': 'pass\n'}}
    assert s.put(s.get(ref)) == ref
    (Path(d)/(ref+'.json')).write_text('{}')
    try: s.get(ref)
    except ValueError: pass
    else: raise AssertionError('tamper accepted')
