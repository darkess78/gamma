"""Owner controls at RepositoryAgent.ask's published pre-admission wait boundary."""
import json
import os
import threading
import time
from pathlib import Path
from urllib import request

import pytest

from gamma.coding import autonomous, autonomous_store as store, learner


def owner_job(tmp_path):
    source = tmp_path / 'project/src/gamma/memory/example.py'
    source.parent.mkdir(parents=True)
    source.write_text('value = 1\n')
    root = tmp_path / 'jobs'
    record = store.create_job(root, 'Preserve the synthetic source while resources remain busy')
    work = autonomous.RepositoryAgent(project=tmp_path / 'project', root=root,
                                     endpoint='http://127.0.0.1:11434')
    work.record = record
    work.record.update(status='running', stage='planning')
    work.path = store.job_path(root, record['id'])
    work.deadline = time.monotonic() + 10
    return work, source


class BusyTransport:
    def __init__(self):
        self.posts = []
        self.probe_ms = []
    def open(self, req, **kwargs):
        if req.data is not None:
            self.posts.append(req.full_url)
            raise AssertionError('Generation must not dispatch while busy')
        assert req.full_url.endswith('/api/ps')
        transport = self
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, *args):
                started = time.perf_counter()
                data = json.dumps({'models': [{'name': 'synthetic-resident'}]}).encode()
                transport.probe_ms.append((time.perf_counter() - started) * 1000)
                return data
        return Response()


@pytest.mark.parametrize('sample', range(3))
def test_owner_stop_returns_promptly_from_resource_wait(tmp_path, monkeypatch, sample):
    work, source = owner_job(tmp_path)
    transport = BusyTransport()
    monkeypatch.setattr(request, 'build_opener', lambda *args: transport)
    admission_requests = []
    def unexpected_admission(*args, **kwargs):
        admission_requests.append(True)
        raise AssertionError('Busy caller must not request generation admission')
    monkeypatch.setattr(learner, 'backend_slot', unexpected_admission)
    waiting, proceed = threading.Event(), threading.Event()
    event = work.event
    def publish(stage, *args, **kwargs):
        result = event(stage, *args, **kwargs)
        if stage == 'waiting_for_inference':
            # Synchronize on durable, observable work state, independent of sleeps.
            waiting.set()
            assert proceed.wait(5), 'Owner control barrier was not released'
        return result
    monkeypatch.setattr(work, 'event', publish)
    errors = []
    def ask():
        try: work.ask('gpt-oss:20b', 'Synthetic resource wait.', {'synthetic': True})
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=ask)
    thread.start()
    try:
        assert waiting.wait(5), 'Caller did not publish resource-wait state'
        assert store.read_job(work.root, work.record['id'])['stage'] == 'waiting_for_inference'
        issued = time.perf_counter()
        store.control_job(work.root, work.record['id'], 'stop')
        written = time.perf_counter()
        proceed.set()
        thread.join(4)
        returned = time.perf_counter()
        metrics = {'sample': sample, 'stop_to_return_ms': (returned-issued)*1000,
                   'control_write_ms': (written-issued)*1000,
                   'after_control_write_ms': (returned-written)*1000,
                   'availability_probe_ms': transport.probe_ms,
                   'generation_posts': len(transport.posts),
                   'admission_requests': len(admission_requests),
                   'thread_alive': thread.is_alive(), 'target_unchanged': source.read_text() == 'value = 1\n'}
        (tmp_path/'stop-sample.json').write_text(json.dumps(metrics, indent=2))
        output = os.getenv('STOP_METRICS_DIR')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output)/f'stop-{sample}.json').write_text(json.dumps(metrics, indent=2))
        assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], autonomous.Stopped)
        assert not transport.posts and not admission_requests
        assert source.read_text() == 'value = 1\n' and not (work.path/'application').exists()
        assert returned-issued < .5, metrics
    finally:
        proceed.set()
        thread.join(5)