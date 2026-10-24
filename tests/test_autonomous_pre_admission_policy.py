"""Deterministic availability cadence and owner controls during busy waits."""
import json
from types import SimpleNamespace
from urllib import request

import pytest

from gamma.coding import autonomous, autonomous_store as store, learner, candidate_resources
from test_autonomous_pre_admission_wait import owner_job


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = lambda: None
    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps += 1
        assert self.sleeps < 1000, 'Bounded deterministic test stalled'
        self.now += seconds
        self.on_sleep()


def busy_environment(tmp_path, monkeypatch, resource):
    work, source = owner_job(tmp_path)
    clock = Clock()
    monkeypatch.setattr(autonomous, 'time', SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: 1000 + clock.now, sleep=clock.sleep))
    work.deadline = 6.25
    probes = {'candidate': [], 'resident': []}
    checkpoints, posts, admissions = [], [], []
    eligible = [float('inf')]
    def candidate_busy(root):
        probes['candidate'].append(clock.now)
        return resource == 'candidate' and clock.now < eligible[0]
    monkeypatch.setattr(candidate_resources, 'candidate_busy', candidate_busy)
    class Opener:
        def open(self, req, **kwargs):
            if req.data is not None:
                posts.append(clock.now)
                raise AssertionError('Owner stop/budget must prevent generation')
            assert req.full_url.endswith('/api/ps')
            probes['resident'].append(clock.now)
            data = {'models': [{'name': 'synthetic-resident'}]
                    if resource == 'resident' and clock.now < eligible[0] else []}
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, *args): return json.dumps(data).encode()
            return Response()
    monkeypatch.setattr(request, 'build_opener', lambda *args: Opener())
    def unexpected_admission(*args, **kwargs):
        admissions.append(clock.now)
        raise AssertionError('No generation admission after terminal control')
    monkeypatch.setattr(learner, 'backend_slot', unexpected_admission)
    actual_checkpoint = work.checkpoint
    def checkpoint():
        checkpoints.append(clock.now)
        return actual_checkpoint()
    monkeypatch.setattr(work, 'checkpoint', checkpoint)
    return work, source, clock, eligible, probes, checkpoints, posts, admissions


def assert_probe_cadence(probes):
    for times in probes.values():
        assert all(later-earlier >= 2-1e-8 for earlier, later in zip(times, times[1:])), probes


@pytest.mark.parametrize('resource', ['resident', 'candidate'])
def test_busy_probe_cadence_and_budget_controls(tmp_path, monkeypatch, resource):
    work, source, clock, _, probes, checkpoints, posts, admissions = busy_environment(tmp_path, monkeypatch, resource)
    with pytest.raises(TimeoutError, match='Authorized time budget ended'):
        work.ask('gpt-oss:20b', 'Synthetic busy resource.', {})
    assert_probe_cadence(probes)
    assert len(probes[resource]) >= 3
    # Behavioral regression bounds, independent of the implementation's sleep size.
    assert clock.now-work.deadline <= .5
    assert max(b-a for a,b in zip(checkpoints, checkpoints[1:])) <= .5
    assert not posts and not admissions and source.read_text() == 'value = 1\n'
    (tmp_path/'cadence.json').write_text(json.dumps({'resource': resource, 'probes': probes,
        'checkpoints': checkpoints, 'budget_deadline': work.deadline, 'returned_at': clock.now}, indent=2))


@pytest.mark.parametrize('resource', ['resident', 'candidate'])
def test_pause_resume_then_stop_prevents_later_generation(tmp_path, monkeypatch, resource):
    work, source, clock, eligible, probes, _, posts, admissions = busy_environment(tmp_path, monkeypatch, resource)
    work.deadline = 12
    actions = {'pause': False, 'resume': False, 'stop': False}
    due = {'resume': float('inf'), 'stop': float('inf')}
    actual_event = work.event
    def event(stage, *args, **kwargs):
        result = actual_event(stage, *args, **kwargs)
        if stage == 'paused': due['resume'] = clock.now + 1.2
        if stage == 'resumed':
            eligible[0] = clock.now + .2
            due['stop'] = clock.now + .3
        return result
    monkeypatch.setattr(work, 'event', event)
    def owner_actions():
        if not actions['pause'] and clock.now >= .3:
            store.control_job(work.root, work.record['id'], 'pause'); actions['pause'] = True
        if not actions['resume'] and clock.now >= due['resume']:
            store.control_job(work.root, work.record['id'], 'resume'); actions['resume'] = True
        if not actions['stop'] and clock.now >= due['stop']:
            store.control_job(work.root, work.record['id'], 'stop'); actions['stop'] = True
    clock.on_sleep = owner_actions
    with pytest.raises(autonomous.Stopped): work.ask('gpt-oss:20b', 'Synthetic busy resource.', {})
    assert all(actions.values()) and clock.now <= due['stop'] + .5
    stages = [item['stage'] for item in store.read_job(work.root, work.record['id'])['events']]
    assert 'paused' in stages and 'resumed' in stages
    assert_probe_cadence(probes)
    assert not posts and not admissions and source.read_text() == 'value = 1\n'


@pytest.mark.parametrize('resource', ['resident', 'candidate'])
def test_expired_budget_cannot_resume_into_generation(tmp_path, monkeypatch, resource):
    work, source, clock, _, probes, _, posts, admissions = busy_environment(tmp_path, monkeypatch, resource)
    work.deadline = 3
    paused = [False]
    def pause():
        if not paused[0] and clock.now >= .3:
            store.control_job(work.root, work.record['id'], 'pause'); paused[0] = True
    clock.on_sleep = pause
    with pytest.raises(TimeoutError, match='Authorized time budget ended'):
        work.ask('gpt-oss:20b', 'Synthetic busy resource.', {})
    assert work.record['status'] == 'paused'
    store.control_job(work.root, work.record['id'], 'resume')
    with pytest.raises(TimeoutError, match='Authorized time budget ended'): work.checkpoint()
    assert_probe_cadence(probes)
    assert not posts and not admissions and source.read_text() == 'value = 1\n'