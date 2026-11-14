"""Finite missing-input escalation without guessed implementation or new authority."""
from copy import deepcopy

import pytest

from gamma.coordination.store import Store
from gamma.coordination.work import WorkRunner
from test_practical_work import spec, check, admit, SOLUTION


def test_missing_input_question_is_source_bound_charged_once_and_never_replayed(tmp_path):
    store = Store(tmp_path); calls = []
    def propose(*_):
        calls.append(1)
        return {'plan': ['Inspect the supplied tracker requirements.', 'Ask for the missing input before editing.'],
                'edits': {}, 'blocker': {'kind': 'missing_input', 'question': 'Which date format should the imported task file use?'}}
    runner = WorkRunner(store, propose=propose, check=check)
    identifier = admit(store, runner); before = store.read('run', identifier)
    runner.run(identifier); runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'needs_checker' and run['reason'] == 'needs_owner_input'
    assert cp['stage'] == 'done' and cp['attempt_count'] == 1 and calls == [1]
    assert cp['source_ref'] == cp['original_source_ref']
    assert cp['blocker']['source_sha256'] == cp['source_sha256']
    assert cp['blocker']['contract'] == cp['contract']
    assert cp['blocker']['question'] == 'Which date format should the imported task file use?'
    for key in ('expires_at', 'maximum_attempts', 'budget_seconds', 'created_at'):
        assert run[key] == before[key]
    with pytest.raises(ValueError, match='blocker'):
        runner.verify({**run, 'status': 'completed'})


@pytest.mark.parametrize('blocker', [None, {}, {'kind': 'grant_authority', 'question': 'May I?'},
    {'kind': 'missing_input', 'question': ''}, {'kind': 'missing_input', 'question': ' '},
    {'kind': 'missing_input', 'question': 'x'*501}, {'kind': 'missing_input', 'question': True},
    {'kind': 'missing_input', 'question': 'Which file?', 'authority': 'apply'}])
def test_malformed_blockers_cannot_be_accepted(tmp_path, blocker):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Ask first.'], 'edits': {}, 'blocker': blocker}, check=check)
    identifier = admit(store, runner, attempts=1); runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'needs_checker' and run['reason'] == 'attempt_limit'
    assert cp.get('blocker') is None and cp['source_ref'] == cp['original_source_ref']


def test_blocker_cannot_accompany_candidate_edits(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Ask first.'], 'edits': {'tool.py': SOLUTION},
        'blocker': {'kind': 'scope_decision', 'question': 'Should this submit externally?'}}, check=check)
    identifier = admit(store, runner, attempts=1); runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'needs_checker' and run['reason'] == 'attempt_limit'
    assert cp.get('blocker') is None and cp['source_ref'] == cp['original_source_ref']


@pytest.mark.parametrize('field,value', [('source_sha256', '0'*64), ('contract', '0'*64),
    ('kind', 'apply'), ('question', '')])
def test_tampered_blocker_evidence_is_rejected(tmp_path, field, value):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Ask first.'], 'edits': {},
        'blocker': {'kind': 'missing_capability', 'question': 'Can you provide an offline data sample?'}}, check=check)
    identifier = admit(store, runner); runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    damaged = deepcopy(cp); damaged['blocker'][field] = value
    runner._save(identifier, damaged)
    with pytest.raises(ValueError, match='blocker'):
        runner.verify(store.read('run', identifier))
