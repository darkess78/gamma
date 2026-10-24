import json
from pathlib import Path

import pytest

from gamma.coding.candidate_data import collect, pure_source
from gamma.coding.curriculum import discord_foundations
from gamma.coding.practice import _digest
from gamma.coding.candidate_resources import candidate_busy
from gamma.coding.autonomous_store import execution_lock


def test_rejects_execution_and_introspection():
    for source in ['import os', "open('/etc/passwd')", "getattr(1,'__class__')", 'x.__class__']:
        with pytest.raises(ValueError):
            pure_source(source)
    assert pure_source('def add(a,b):\n return a+b\n')


def test_dataset_requires_immutable_fixture_and_reexecuted_success(tmp_path):
    task = discord_foundations()[0]
    files = dict(task.files)
    files['solution.py'] = 'def should_retry(error,attempt,maximum_attempts):\n return False\n'
    sample = {'task_id': task.id, 'source_files': files, 'source_sha256': _digest(files)}
    directory = tmp_path / 'practice-run'; directory.mkdir()
    path = directory / 'candidate-solutions.json'
    path.write_text(json.dumps({'version': 1, 'run_id': 'practice-run', 'samples': [sample]}))
    calls = []
    def failed(workspace, spec):
        calls.append(spec)
        return {'name': spec.name, 'kind': spec.kind, 'passed': False, 'exit_code': 1,
                'infrastructure_error': False, 'output_sha256': 'f' * 64, 'output_tail': 'failed'}
    assert collect(tmp_path, check=failed) == []
    assert calls
    files['test_regression.py'] = 'print("fake pass")'
    sample['source_sha256'] = _digest(files)
    path.write_text(json.dumps({'version': 1, 'run_id': 'practice-run', 'samples': [sample]}))
    calls.clear()
    assert collect(tmp_path, check=failed) == []
    assert calls == []


def test_candidate_lock_blocks_coding_until_released(tmp_path):
    assert candidate_busy(tmp_path) is False
    with execution_lock(tmp_path / 'candidates'):
        assert candidate_busy(tmp_path) is True
    assert candidate_busy(tmp_path) is False


def test_dataset_refuses_symlink_storage(tmp_path):
    target = tmp_path / 'real'; target.mkdir()
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    with pytest.raises(ValueError):
        collect(linked)
