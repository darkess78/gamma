"""Acceptance scenarios for the coordination core's first milestone.

Private owner text plus offline practice: conversation during work, switching
without losing place, sleep with workers continuing, catch-up from real
outcomes, restart recovery, and single-owner effects.
"""
import os
import threading
import time

from fastapi.testclient import TestClient
import pytest

from gamma.coding.durable_practice import PracticeRunner, PracticeYield
from gamma.coding.practice import _digest
from gamma.coordination.app import build_core, create_app
from gamma.coordination.core import Core
from gamma.coordination.decide import Decision, parse_decision
from gamma.coordination.store import OwnershipLost, Store


SOLUTION = '''def should_retry(error, attempt, maximum_attempts):
    return isinstance(error, str) and error in ('busy', 'rate_limit') and type(attempt) is int \\
        and type(maximum_attempts) is int and 1 <= attempt < maximum_attempts
'''


def good_proposal(*_args):
    return {'solution.py': SOLUTION}


def fake_check(workspace, spec):
    passed = (workspace / 'solution.py').read_text() == SOLUTION
    return {'name': spec.name, 'kind': spec.kind, 'passed': passed, 'exit_code': 0 if passed else 1,
            'output_tail': 'ok' if passed else 'public check failed'}


def fake_evaluate(_task, files):
    return {'source_sha256': _digest(files), 'passed': True, 'status': 'passed'}


class Script:
    """A stand-in for the model: returns queued decisions and records each situation."""

    def __init__(self, *decisions, default=None):
        self.decisions = list(decisions)
        self.default = default or Decision(say='Mm.')
        self.calls = []

    def __call__(self, situation, mode):
        self.calls.append((mode, situation))
        return self.decisions.pop(0) if self.decisions else self.default


def make_core(root, decide, *, propose=good_proposal, check=fake_check, evaluate=fake_evaluate, **options):
    store = Store(root)
    runner = PracticeRunner(store, propose=propose, check=check, evaluate=evaluate)
    return Core(store, decide=decide, runner=runner, practice_ids=['retry_budget', 'message_chunks'],
                **options)


def tick_until(core, condition, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        core.tick()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError('condition not reached')


def activity(core):
    return core.status()['activities'][0]


def start_practice(core, script, exercise='retry_budget'):
    script.decisions.append(Decision(say='Fine, I will practice.', action='start_practice', exercise_id=exercise))
    result = core.input('Go practice ' + exercise)
    assert result['effect']['activity_id'], result
    return result['effect']['activity_id']


def test_practice_completes_and_outcome_is_recorded_once(tmp_path):
    script = Script()
    core = make_core(tmp_path, script)
    try:
        activity_id = start_practice(core, script)
        tick_until(core, lambda: activity(core)['status'] == 'completed')
        summary = activity(core)
        assert summary['activity_id'] == activity_id
        assert summary['outcome']['status'] == 'completed'
        assert summary['tasks'] == [{'task': 'retry_budget', 'status': 'verified_practice',
                                     'attempts': 1, 'reason': 'independently_verified'}]
        # A late or repeated worker report cannot create a second outcome.
        run_id = core.store.read('activity', activity_id)['run_id']
        run = core.store.read('run', run_id)
        core.store.update_run(run_id, run['checkpoint_ref'], status='completed', reason='duplicate')
        core.reconcile()
        record = core.store.read('activity', activity_id)
        assert record['outcome']['reason'] == 'verified_offline_practice'
    finally:
        core.close()


def test_conversation_stays_available_while_practice_runs(tmp_path):
    release = threading.Event()
    entered = threading.Event()

    def slow_proposal(*args):
        entered.set()
        release.wait(10)
        return good_proposal(*args)

    script = Script()
    core = make_core(tmp_path, script, propose=slow_proposal)
    try:
        start_practice(core, script)
        tick_until(core, entered.is_set)
        started = time.time()
        script.decisions.append(Decision(say='Still working on it, but go on.'))
        reply = core.input('How are you?')
        assert reply['status'] == 'done' and reply['say'] == 'Still working on it, but go on.'
        assert time.time() - started < 2
        # She saw the run as in progress, not finished.
        seen = script.calls[-1][1]['activities'][0]
        assert seen['status'] == 'running' and 'outcome' not in seen
        release.set()
        tick_until(core, lambda: activity(core)['status'] == 'completed')
    finally:
        release.set()
        core.close()


def test_switching_pauses_and_resume_keeps_the_place(tmp_path):
    proposals = []
    gate = threading.Event()

    def counted_proposal(*args):
        proposals.append(1)
        gate.wait(10)
        return good_proposal(*args)

    script = Script()
    core = make_core(tmp_path, script, propose=counted_proposal)
    try:
        activity_id = start_practice(core, script)
        tick_until(core, lambda: proposals)
        script.decisions.append(Decision(say='Pausing it.', action='pause', activity_id=activity_id))
        assert core.input('Stop that for now, help me with homework')['effect']['status'] == 'pausing'
        gate.set()  # The in-flight proposal finishes; the runner stops at its next boundary.
        tick_until(core, lambda: activity(core)['status'] == 'paused')
        checkpoint = core.runner.artifacts.get(core.store.read('run', core.store.read('activity', activity_id)['run_id'])['checkpoint_ref'])
        assert checkpoint['tasks'][0]['stage'] == 'candidate_saved'  # The edit is kept, not lost.
        script.decisions.append(Decision(say='Back to it.', action='resume', activity_id=activity_id))
        core.input('Ok, you can go back to your practice')
        tick_until(core, lambda: activity(core)['status'] == 'completed')
        assert len(proposals) == 1  # Resuming tested the saved edit instead of regenerating it.
    finally:
        gate.set()
        core.close()


def test_sleep_queues_input_workers_continue_and_wake_catches_up(tmp_path):
    gate = threading.Event()
    entered = threading.Event()

    def gated(*args):
        entered.set()
        gate.wait(10)
        return good_proposal(*args)

    script = Script()
    core = make_core(tmp_path, script, propose=gated)
    try:
        start_practice(core, script)
        tick_until(core, entered.is_set)
        core.sleep()
        calls_before = len(script.calls)
        queued = core.input('Are you there?')
        assert queued['status'] == 'queued' and len(script.calls) == calls_before
        gate.set()
        tick_until(core, lambda: activity(core)['status'] == 'completed')
        assert len(script.calls) == calls_before  # No decisions while asleep.

        script.decisions.append(Decision(say='Morning. The retry practice passed while I was out.'))
        woke = core.wake()
        assert [c['outcome'] for c in woke['catch_up']] == [
            {'status': 'completed', 'reason': 'verified_offline_practice', 'while_asleep': True}]
        assert woke['messages_waiting'] == 1
        mode, situation = script.calls[-1]
        assert mode == 'wake' and situation['while_you_slept'] == woke['catch_up']
        # Catch-up is reported once; the queued message is answered after waking.
        assert core.wake()['catch_up'] == []
        tick_until(core, lambda: core.store.read('turn', queued['request_id'])['status'] == 'done')
    finally:
        gate.set()
        core.close()


def test_restart_resumes_from_checkpoint_without_repeating_work(tmp_path):
    calls = []

    def counted(*args):
        calls.append(1)
        return good_proposal(*args)

    def crash_on_evaluation(_task, _files):
        raise KeyboardInterrupt  # Simulates the process dying mid-run.

    script = Script()
    first = make_core(tmp_path, script, propose=counted, evaluate=crash_on_evaluation)
    activity_id = start_practice(first, script)
    first._launch_next_run()
    with pytest.raises(KeyboardInterrupt):
        first._worker[1].result(timeout=10)
    first.close()

    second = make_core(tmp_path, Script(), propose=counted)
    try:
        tick_until(second, lambda: activity(second)['status'] == 'completed')
        assert len(calls) == 1  # The saved candidate was evaluated, not regenerated.
        assert second.store.read('activity', activity_id)['outcome']['status'] == 'completed'
    finally:
        second.close()


def test_only_one_core_owns_the_state(tmp_path):
    core = make_core(tmp_path, Script())
    try:
        with pytest.raises(BlockingIOError):
            make_core(tmp_path, Script())
        # If ownership moves (e.g. a handover), the old core can no longer commit effects.
        core.store.claim()
        with pytest.raises(OwnershipLost):
            core.input('hello')
    finally:
        core._lease.close()


def test_stale_draft_does_not_act(tmp_path):
    first_entered = threading.Event()
    release_first = threading.Event()

    def decide(situation, mode):
        if situation.get('latest_input') == 'first':
            first_entered.set()
            release_first.wait(10)
            return Decision(say='Starting practice.', action='start_practice', exercise_id='retry_budget')
        return Decision(say='Got both messages.')

    core = make_core(tmp_path, decide)
    try:
        results = {}
        worker = threading.Thread(target=lambda: results.setdefault('first', core.input('first')))
        worker.start()
        assert first_entered.wait(5)
        assert core.input('second')['say'] == 'Got both messages.'
        release_first.set()
        worker.join(5)
        assert results['first']['status'] == 'superseded'
        assert core.status()['activities'] == []  # The stale decision started nothing.
    finally:
        release_first.set()
        core.close()


def test_code_enforces_what_decisions_may_do(tmp_path):
    script = Script()
    core = make_core(tmp_path, script)
    try:
        script.decisions.append(Decision(say='Sure.', action='start_practice', exercise_id='rm_rf_home'))
        assert core.input('do something')['effect'] == {'action': 'start_practice', 'refused': 'unknown_exercise'}
        script.decisions.append(Decision(say='Ok.', action='stop', activity_id='made-up'))
        assert core.input('stop it')['effect']['refused'] == 'unknown_activity'
        assert core.status()['activities'] == []
    finally:
        core.close()


def test_quiet_review_only_when_quiet_and_not_while_busy(tmp_path):
    clock = [1000.0]
    script = Script()
    core = make_core(tmp_path, script, clock=lambda: clock[0], quiet_after_seconds=300)
    try:
        script.decisions.append(Decision(say='Hi.'))
        core.input('hi')
        core.tick()
        assert [m for m, _ in script.calls] == ['conversation']  # Too soon after the owner spoke.

        clock[0] += 3600
        script.decisions.append(Decision(say='', action='start_practice', exercise_id='retry_budget'))
        core.tick()
        core._thinking.result(timeout=5)
        assert script.calls[-1][0] == 'quiet'
        assert activity(core)['started_by'] == 'you'

        # A second self-started practice is refused while one is still active.
        with core.store.transaction(core.token) as db:
            effect = core._start_practice(db, 'message_chunks', origin='self', now=clock[0])
        assert effect['refused'] == 'already_busy'
    finally:
        core.close()


def test_busy_gpu_yields_instead_of_failing(tmp_path):
    def busy(*_args):
        raise PracticeYield('resident_model')

    script = Script()
    core = make_core(tmp_path, script, propose=busy)
    try:
        start_practice(core, script)
        core.tick()
        core._worker[1].result(timeout=10)
        core.reconcile()
        record = core.store.read('activity', activity(core)['activity_id'])
        assert record['status'] == 'waiting' and record['next_launch_at'] > time.time() + 30
        assert core.store.read('run', record['run_id'])['status'] == 'queued'
    finally:
        core.close()


def test_parse_decision_accepts_json_or_plain_text():
    assert parse_decision('{"say": "Hm.", "action": "none"}').say == 'Hm.'
    assert parse_decision('Just text.').say == 'Just text.'
    with pytest.raises(ValueError):
        parse_decision('{"action": "launch_missiles"}')


def test_http_routes(tmp_path):
    script = Script(Decision(say='Here.'))
    core = build_core(tmp_path, decide=script, propose=good_proposal, check=fake_check)
    with TestClient(create_app(core, tick_seconds=0.05)) as client:
        reply = client.post('/api/coordination/input', json={'text': 'hello', 'request_id': 'r1'})
        assert reply.status_code == 200 and reply.json()['say'] == 'Here.'
        # The same request id is answered from the record, not re-decided.
        assert client.post('/api/coordination/input', json={'text': 'hello', 'request_id': 'r1'}).json()['say'] == 'Here.'
        assert len(script.calls) == 1
        assert client.post('/api/coordination/sleep').json()['awake'] is False
        assert client.post('/api/coordination/input', json={'text': 'hey'}).json()['status'] == 'queued'
        assert 'catch_up' in client.post('/api/coordination/wake').json()
        assert client.get('/api/coordination/status').status_code == 200


@pytest.mark.skipif(os.name == 'nt', reason='the practice sandbox runs on Gamma PC (Linux)')
def test_real_sandbox_checks_and_evaluation(tmp_path):
    from gamma.coding.sandbox import run_practice_check

    script = Script()
    store = Store(tmp_path)
    runner = PracticeRunner(store, propose=good_proposal, check=run_practice_check)
    core = Core(store, decide=script, runner=runner, practice_ids=['retry_budget'])
    try:
        start_practice(core, script)
        tick_until(core, lambda: activity(core)['status'] in {'completed', 'blocked', 'needs_checker'}, timeout=120)
        assert activity(core)['status'] == 'completed', activity(core)
    finally:
        core.close()
