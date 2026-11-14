"""Twenty real recovery cycles must not depend on cyclic GC to close SQLite."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux proc descriptor accounting')
def test_actual_recovery_cycles_close_connections_with_delayed_gc():
    source = '''
import gc
from pathlib import Path
import tempfile
from gamma.coordination.store import Store
from gamma.coordination.transfer import export_snapshot,restore_snapshot
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    store=Store(root/'state')
    with store.transaction() as db:
        store.put(db,'personal_meta','state',{'identity_id':'1'*32})
    gc.collect()
    before=len(list(Path('/proc/self/fd').iterdir()))
    gc.disable()
    try:
        for index in range(20):
            export_snapshot(root/'state',root/f'bundle-{index}')
            restore_snapshot(root/f'bundle-{index}',root/f'restore-{index}')
        after=len(list(Path('/proc/self/fd').iterdir()))
        assert after<=before+2,(before,after)
    finally:
        gc.enable()
        gc.collect()
'''
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable,'-c',source],cwd=root,
        env={**os.environ,'PYTHONPATH':str(root/'src'),'TMPDIR':'/tmp'},
        capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
