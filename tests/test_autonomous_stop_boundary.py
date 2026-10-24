"""Exercise real coding control arrival at the availability/dispatch boundary."""
import json
import threading
import time

import pytest

from gamma.coding import autonomous, autonomous_store as store


class ControlledAvailability:
    def __init__(self, monkeypatch, *, at_availability=None):
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.idle_calls = 0
        self.dispatches = []
        self.at_availability = at_availability
        boundary = self

        class Learner:
            def __init__(self, **kwargs):
                self.model = kwargs['model']

            def require_idle(self):
                boundary.idle_calls += 1
                # execute's initial availability check is followed by ask's check.
                if boundary.idle_calls == 2:
                    boundary.arrived.set()
                    if not boundary.release.wait(4):
                        raise AssertionError('Availability barrier was not released')
                    if boundary.at_availability:
                        boundary.at_availability()

            def _json(self, path, payload):
                boundary.dispatches.append((path, payload))
                return {'done': True, 'done_reason': 'stop', 'eval_count': 3,
                        'total_duration': 10,
                        'message': {'content': '{"tasks": []}'}}

        monkeypatch.setattr(autonomous, 'LocalPracticeLearner', Learner)


def job(tmp_path, monkeypatch, *, at_availability=None, model='qwen3.8:27b'):
    project = tmp_path / 'project'
    source = project / 'src/gamma/memory/example.py'
    source.parent.mkdir(parents=True)
    source.write_text('value = 1\n')
    root = tmp_path / 'jobs'
    checker = 'qwen3.8:27b' if model == 'gpt-oss:20b' else 'gpt-oss:20b'
    record = store.create_job(root, 'Preserve the example and inspect the goal',
                              model=model, checker_model=checker)
    boundary = ControlledAvailability(monkeypatch, at_availability=at_availability)
    checks = []

    def check(workspace, **options):
        checks.append(options)
        return {'passed': True, 'count': 1}

    agent = autonomous.RepositoryAgent(project=project, root=root,
                                       endpoint='http://127.0.0.1:11434', checks=check)

    def plan(workspace):
        agent.event('planning', 'Synthetic boundary acceptance')
        result = agent.ask(record['model'], 'Inspect this synthetic goal.',
                           {'goal': record['goal']}, tokens=1600)
        assert result == {'tasks': []}
        return []  # No source application or activation in these control tests.

    agent.plan = plan
    return root, record, agent, boundary, checks, source


def start(agent, identifier):
    results, errors = [], []

    def work():
        try:
            results.append(agent.execute(identifier))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, results, errors


def finish(thread, boundary, errors):
    boundary.release.set()
    thread.join(5)
    assert not thread.is_alive(), 'Synthetic worker did not finish'
    assert not errors, errors


def no_generated_artifacts(root, record):
    path = store.job_path(root, record['id'])
    assert not list(path.glob('model-call-*.json'))
    assert not list(path.glob('*-attempt-*.json'))
    assert not (path / 'verified-example.json').exists()
    assert not (path / 'application').exists()


def test_stop_arriving_during_availability_uses_real_terminal_handling(tmp_path, monkeypatch):
    root, record, agent, boundary, checks, source = job(tmp_path, monkeypatch)
    thread, results, errors = start(agent, record['id'])
    try:
        assert boundary.arrived.wait(4)
        assert not boundary.dispatches
        acknowledgement = store.control_job(root, record['id'], 'stop')
        assert acknowledgement == {'id': record['id'], 'requested': 'stop'}
    finally:
        finish(thread, boundary, errors)
    assert results[0]['status'] == 'stopped'
    persisted = store.read_job(root, record['id'])
    assert persisted['status'] == 'stopped' and 'completed_at' in persisted
    assert boundary.dispatches == []
    assert 'model_calls' not in persisted and 'last_transport' not in persisted
    no_generated_artifacts(root, record)
    assert checks == [{'full_suite': True}]
    assert source.read_text() == 'value = 1\n'


@pytest.mark.parametrize('model', ['qwen3.8:27b', 'gpt-oss:20b'])
def test_normal_dispatch_once_preserves_the_entire_request(tmp_path, monkeypatch, model):
    root, record, agent, boundary, _, _ = job(tmp_path, monkeypatch, model=model)
    boundary.release.set()
    result = agent.execute(record['id'])
    assert result['status'] == 'already_satisfied'
    assert boundary.dispatches == [('/api/chat', {
        'model': model, 'stream': False, 'format': 'json',
        'think': 'low' if model.startswith('gpt-oss') else False, 'keep_alive': 0,
        'options': {'num_ctx': 8192, 'num_predict': 1600, 'temperature': 0.1},
        'messages': [{'role': 'system', 'content': 'Inspect this synthetic goal.'},
                     {'role': 'user', 'content': json.dumps({'goal': record['goal']}, ensure_ascii=False)}]
    })]
    assert store.read_job(root, record['id'])['model_calls'] == 1
    saved = json.loads((store.job_path(root, record['id']) / 'model-call-1.json').read_text())
    assert saved == {'model': model, 'stage': 'planning', 'response': {'tasks': []}}


def test_preexisting_stop_skips_availability_and_generation(tmp_path, monkeypatch):
    root, record, agent, boundary, checks, _ = job(tmp_path, monkeypatch)
    store.control_job(root, record['id'], 'stop')
    assert agent.execute(record['id'])['status'] == 'stopped'
    assert boundary.idle_calls == 0 and boundary.dispatches == [] and checks == []
    assert not (store.job_path(root, record['id']) / 'workspace').exists()
    no_generated_artifacts(root, record)


def test_budget_expiring_during_availability_prevents_dispatch(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(autonomous.time, 'monotonic', lambda: clock[0])
    root, record, agent, boundary, _, _ = job(tmp_path, monkeypatch)
    boundary.at_availability = lambda: clock.__setitem__(0, agent.deadline + 1)
    boundary.release.set()
    result = agent.execute(record['id'])
    assert result['status'] == 'blocked' and result['error'] == 'TimeoutError'
    assert result['reason'] == 'Authorized time budget ended'
    assert boundary.dispatches == []
    no_generated_artifacts(root, record)


def test_preexisting_pause_waits_for_actual_resume(tmp_path, monkeypatch):
    root, record, agent, boundary, _, _ = job(tmp_path, monkeypatch)
    store.control_job(root, record['id'], 'pause')
    boundary.release.set()
    thread, results, errors = start(agent, record['id'])
    try:
        deadline = time.monotonic() + 3
        while store.read_job(root, record['id'])['status'] != 'paused' and thread.is_alive():
            assert time.monotonic() < deadline, 'Pause was not observed'
            time.sleep(0.01)
        assert store.read_job(root, record['id'])['status'] == 'paused'
        assert boundary.dispatches == []
        store.control_job(root, record['id'], 'resume')
    finally:
        if thread.is_alive() and (store.job_path(root, record['id']) / 'control.json').exists():
            store.control_job(root, record['id'], 'resume')
        finish(thread, boundary, errors)
    assert results[0]['status'] == 'already_satisfied'
    assert len(boundary.dispatches) == 1
    stages = [event['stage'] for event in store.read_job(root, record['id'])['events']]
    assert 'paused' in stages and 'resumed' in stages


def test_stop_for_another_job_does_not_change_this_dispatch(tmp_path, monkeypatch):
    root, record, agent, boundary, _, _ = job(tmp_path, monkeypatch)
    # Represent an inherited second queued record without bypassing control_job.
    terminal = {**record, 'status': 'completed'}
    store.write_json(store.job_path(root, record['id']) / 'state.json', terminal)
    other = store.create_job(root, 'Preserve another separately identified goal')
    store.write_json(store.job_path(root, record['id']) / 'state.json', record)
    thread, results, errors = start(agent, record['id'])
    try:
        assert boundary.arrived.wait(4)
        store.control_job(root, other['id'], 'stop')
    finally:
        finish(thread, boundary, errors)
    assert results[0]['status'] == 'already_satisfied'
    assert len(boundary.dispatches) == 1
    assert not (store.job_path(root, record['id']) / 'control.json').exists()
    assert store.read_job(root, other['id'])['status'] == 'queued'
    assert json.loads((store.job_path(root, other['id']) / 'control.json').read_text())['action'] == 'stop'
