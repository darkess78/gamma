"""Independent practical candidate behavior, recovery and effect boundaries."""
from copy import deepcopy
from pathlib import Path
import time
import sys

import pytest

from gamma.coordination.store import Store
from gamma.coordination.work import WorkRunner
from gamma.coordination.work_models import validate_spec
from gamma.coordination.work_learner import LocalWorkProposer


def spec(mode='build'):
    return {'mode': mode, 'goal': 'Provide a useful local greeter CLI that preserves the name.',
        'files': {'tool.py': 'def greet(name):\n    return "hello"\n',
                  'guide.md': 'The owner wants the supplied name preserved.',
                  'check_tool.py': 'from tool import greet\nassert greet("Mira") == "hello Mira"\n'},
        'editable': ['tool.py'] if mode == 'build' else [],
        'checks': [{'name': 'greeter flow', 'module': 'check_tool', 'kind': 'feature'}] if mode == 'build' else []}


SOLUTION = 'def greet(name):\n    return "hello " + name\n'


def check(workspace, check_spec):
    passed = (workspace / 'tool.py').read_text() == SOLUTION
    return {'name': check_spec.name, 'kind': check_spec.kind, 'passed': passed,
            'exit_code': 0 if passed else 1, 'infrastructure_error': False,
            'output_tail': 'name missing' if not passed else 'check_completed'}


def admit(store, runner, *, value=None, attempts=3, identifier='work-one', seconds=60):
    ref = runner.prepare(value or spec())
    now = time.time()
    run = {'id': identifier, 'kind': 'practical', 'checkpoint_ref': ref, 'initial_checkpoint_ref': ref,
        'status': 'queued', 'desired_state': 'running', 'revision': 0, 'reason': 'authorized',
        'created_at': now, 'expires_at': now + seconds, 'budget_seconds': seconds,
        'maximum_attempts': attempts, 'production_verified': False}
    with store.transaction() as db:
        store.put(db, 'run', identifier, run)
    return identifier


def test_baseline_failure_then_actual_candidate_repair_and_bound_receipt(tmp_path):
    store = Store(tmp_path)
    calls = []
    def propose(value, files, feedback):
        calls.append(deepcopy(feedback))
        assert files == {'tool.py': spec()['files']['tool.py']}
        return {'plan': ['Read the name requirement.', 'Preserve the name and run the actual flow.'], 'edits': {'tool.py': SOLUTION}}
    runner = WorkRunner(store, propose=propose, check=check)
    identifier = admit(store, runner)
    runner.run(identifier)
    run = store.read('run', identifier)
    assert run['status'] == 'completed', run
    cp = runner.verify(run)
    assert cp['attempt_count'] == 1 and len(calls) == 1
    assert calls[0][0]['latest_executed_checks'][0]['passed'] is False
    assert cp['checks'][0]['source_sha256'] == cp['source_sha256']
    assert cp['checks'][0]['contract'] == cp['contract']
    assert runner.artifacts.get(cp['source_ref'])['files']['tool.py'] == SOLUTION
    assert runner.artifacts.get(cp['source_ref'])['files']['guide.md'] == spec()['files']['guide.md']
    assert runner.artifacts.get(cp['research_ref'])['network_used'] is False
    assert cp['production_verified'] is False and cp['activation_allowed'] is False
    runner.run(identifier)
    assert len(calls) == 1


def test_saved_candidate_pause_resume_without_generation_or_budget_reset(tmp_path):
    store = Store(tmp_path)
    calls = []
    identifier = None
    def propose(*args):
        calls.append(1)
        with store.transaction() as db:
            run = store.get(db, 'run', identifier); run['desired_state'] = 'pause'; store.put(db, 'run', identifier, run)
        return {'plan': ['Fix the observed missing name.'], 'edits': {'tool.py': SOLUTION}}
    runner = WorkRunner(store, propose=propose, check=check)
    identifier = admit(store, runner)
    original = store.read('run', identifier)
    runner.run(identifier)
    paused = store.read('run', identifier)
    assert paused['status'] == 'paused'
    assert runner.verify(paused)['stage'] == 'candidate_saved'
    second = WorkRunner(store, propose=lambda *_: pytest.fail('saved candidate was regenerated'), check=check)
    with store.transaction() as db:
        run = store.get(db, 'run', identifier); run.update(status='queued', desired_state='running'); store.put(db, 'run', identifier, run)
    second.run(identifier)
    final = store.read('run', identifier)
    assert final['status'] == 'completed'
    assert final['expires_at'] == original['expires_at'] and final['maximum_attempts'] == original['maximum_attempts']
    assert second.verify(final)['attempt_count'] == 1 and len(calls) == 1


def test_uncertain_inference_is_charged_and_not_replayed(tmp_path):
    store = Store(tmp_path); calls = []
    def unavailable(*_):
        calls.append(1); raise TimeoutError('uncertain')
    runner = WorkRunner(store, propose=unavailable, check=check)
    identifier = admit(store, runner)
    runner.run(identifier); runner.run(identifier)
    run = store.read('run', identifier)
    assert run['status'] == 'blocked' and 'no_replay' in run['reason']
    assert runner.verify(run)['attempt_count'] == 1 and len(calls) == 1


def test_recovered_inference_started_blocks_without_request(tmp_path):
    store = Store(tmp_path); runner = WorkRunner(store, propose=lambda *_: pytest.fail('uncertain request replayed'), check=check)
    identifier = admit(store, runner)
    run = store.read('run', identifier); cp = runner.verify(run)
    cp.update(stage='inference_started', attempt_count=1)
    runner._save(identifier, cp, status='running')
    runner.run(identifier)
    assert store.read('run', identifier)['reason'] == 'uncertain_inference_no_replay'


def test_research_produces_sourced_result_without_implementation(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['guide.md requires a named greeting.', 'tool.py currently omits the supplied name.'], 'edits': {}}, check=lambda *_: pytest.fail('research executed code'))
    identifier = admit(store, runner, value=spec('research'))
    runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'completed' and cp['status'] == 'researched'
    assert cp['source_ref'] == cp['original_source_ref'] and cp['checks'] == []
    assert runner.artifacts.get(cp['plan_ref'])['unverified_interpretation'] is True


def test_research_cannot_accept_edits_or_claim_checked_code(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Implement it'], 'edits': {'tool.py': SOLUTION}}, check=check)
    identifier = admit(store, runner, value=spec('research'), attempts=1)
    runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'needs_checker' and cp['source_ref'] == cp['original_source_ref']


def test_protected_check_edit_blocked_and_useful_attempt_limit(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Change check'], 'edits': {'check_tool.py': 'pass'}}, check=check)
    identifier = admit(store, runner, attempts=2)
    runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    assert run['status'] == 'needs_checker' and cp['attempt_count'] == 2
    assert all(a['reason'] == 'work_proposal_scope_violation' for a in cp['attempts'])
    assert cp['source_ref'] == cp['original_source_ref']


def test_source_mutation_invalidates_completed_evidence(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Fix name'], 'edits': {'tool.py': SOLUTION}}, check=check)
    identifier = admit(store, runner); runner.run(identifier)
    run = store.read('run', identifier); cp = runner.verify(run)
    files = runner.artifacts.get(cp['source_ref'])['files']; files['tool.py'] += '# changed\n'
    cp['source_ref'] = runner.artifacts.put({'files': files})
    runner._save(identifier, cp)
    with pytest.raises(ValueError, match='source_or_scope'):
        runner.verify(store.read('run', identifier))


def test_expired_scope_stops_without_generation(tmp_path):
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: pytest.fail('expired scope generated'), check=check, clock=lambda: time.time() + 3600)
    identifier = admit(store, runner); runner.run(identifier)
    assert store.read('run', identifier)['reason'] == 'budget_exhausted'


@pytest.mark.parametrize('field,value', [('maximum_attempts', True), ('maximum_attempts', 0), ('maximum_attempts', 6), ('expires_at', float('inf')), ('budget_seconds', float('nan'))])
def test_invalid_runtime_budget_rejected(tmp_path, field, value):
    store = Store(tmp_path); runner = WorkRunner(store, propose=lambda *_: {}, check=check)
    identifier = admit(store, runner); run = store.read('run', identifier); run[field] = value
    with pytest.raises(ValueError): runner.verify(run)


def test_local_proposer_oversized_context_stops_before_http_or_admission():
    proposer = LocalWorkProposer(endpoint='http://127.0.0.1:11434', model='fictional:1b')
    class NoHTTP:
        def open(self, *_args, **_kwargs):
            pytest.fail('oversized prompt reached HTTP')
    proposer.opener = NoHTTP()
    value = spec(); value['files']['guide.md'] = 'source ' * 7000
    with pytest.raises(ValueError, match='work_context_too_large'):
        proposer(value, {'tool.py': ''}, [])


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Gamma Linux sandbox required')
def test_real_protected_cli_flow_in_no_network_readonly_sandbox(tmp_path):
    value = {'mode': 'build', 'goal': 'A CLI greeting preserves the supplied name.',
        'files': {'tool.py': 'print("hello")\n',
            'feature_flow.py': 'import subprocess,sys\np=subprocess.run([sys.executable,"-B","tool.py","Mira"],capture_output=True,text=True,check=True)\nassert p.stdout.strip()=="hello Mira"\n'},
        'editable': ['tool.py'], 'checks': [{'name': 'actual_cli_flow', 'module': 'feature_flow', 'kind': 'feature'}]}
    store = Store(tmp_path)
    runner = WorkRunner(store, propose=lambda *_: {'plan': ['Read the CLI input.', 'Print the exact name and run the real CLI.'],
        'edits': {'tool.py': 'import sys\nprint("hello " + sys.argv[1])\n'}})
    identifier = admit(store, runner, value=value)
    runner.run(identifier)
    run = store.read('run', identifier)
    assert run['status'] == 'completed', run
    cp = runner.verify(run)
    assert cp['attempt_count'] == 1 and cp['checks'][0]['passed']
