"""Private candidate controls; no model activation route is provided."""
import json
import re
import sys
from pathlib import Path

from .autonomous_store import safe_root, list_jobs, ACTIVE
from .candidate_resources import candidate_busy


def list_candidates(coding):
    records = []
    for directory in sorted(safe_root(coding / 'candidates').glob('candidate-*'))[-100:]:
        path = directory / 'state.json'
        if directory.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 64000:
            continue
        records.append(json.loads(path.read_text()))
    return records


def start_trial(coding, project):
    from ..supervisor.manager import ProcessManager
    if not sys.platform.startswith('linux'):
        raise ValueError('Adapter trials run on Gamma PC')
    if candidate_busy(coding) or any(j['status'] in ACTIVE for j in list_jobs(coding / 'autonomous')):
        raise ValueError('Assigned coding work or another candidate has priority')
    python = project / '.venv-training' / 'bin' / 'python'
    if not python.is_file():
        raise ValueError('Separate training environment unavailable')
    class CandidateManager(ProcessManager):
        def _resolve_background_python(self):
            return str(python)
    return CandidateManager().start_module('coding_candidate_trial', 'gamma.coding.candidate_trial')


def stop_trial(coding, identifier):
    if not re.fullmatch(r'candidate-[0-9a-f]{32}', identifier):
        raise ValueError('Invalid candidate ID')
    path = safe_root(coding / 'candidates') / identifier
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Invalid candidate storage')
    stop = path / 'stop'
    if stop.is_symlink():
        raise ValueError('Invalid candidate control')
    stop.touch(mode=0o600, exist_ok=True)
    return {'requested': 'stop', 'id': identifier}
