"""Mid-wait stop regression using owner arrival offsets, never sleep inspection."""
import json
import os
import threading
import time
from pathlib import Path
from urllib import request

import pytest

from gamma.coding import autonomous, autonomous_store as store, learner
from test_autonomous_pre_admission_wait import owner_job, BusyTransport


@pytest.mark.parametrize('offset_ms', [50, 150, 350])
def test_owner_stop_during_busy_wait(tmp_path, monkeypatch, offset_ms):
    work, source = owner_job(tmp_path)
    transport = BusyTransport()
    probes, admissions = [], []
    def open_probe(req, **kwargs):
        if req.data is None: probes.append(time.perf_counter())
        return transport.open(req, **kwargs)
    monkeypatch.setattr(request, 'build_opener', lambda *args: type('Opener', (), {'open': staticmethod(open_probe)})())
    def unexpected_admission(*args, **kwargs):
        admissions.append(True)
        raise AssertionError('Busy resource must prevent admission')
    monkeypatch.setattr(learner, 'backend_slot', unexpected_admission)
    waiting = threading.Event()
    actual_event = work.event
    def publish(stage, *args, **kwargs):
        result = actual_event(stage, *args, **kwargs)
        if stage == 'waiting_for_inference': waiting.set()
        return result
    monkeypatch.setattr(work, 'event', publish)
    errors = []
    def ask():
        try: work.ask('gpt-oss:20b', 'Synthetic busy wait.', {})
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=ask)
    thread.start()
    try:
        assert waiting.wait(5), 'Caller did not publish its resource wait'
        arrival = time.perf_counter()
        time.sleep(offset_ms / 1000)
        issued = time.perf_counter()
        store.control_job(work.root, work.record['id'], 'stop')
        written = time.perf_counter()
        thread.join(4)
        returned = time.perf_counter()
        metrics = {'offset_ms': offset_ms, 'actual_offset_ms': (issued-arrival)*1000,
                   'stop_to_return_ms': (returned-issued)*1000, 'control_write_ms': (written-issued)*1000,
                   'after_control_write_ms': (returned-written)*1000,
                   'availability_probe_count': len(probes), 'availability_probe_ms': transport.probe_ms,
                   'generation_posts': len(transport.posts), 'admission_requests': len(admissions),
                   'thread_alive': thread.is_alive(), 'target_unchanged': source.read_text() == 'value = 1\n'}
        (tmp_path/'midwait-sample.json').write_text(json.dumps(metrics, indent=2))
        output = os.getenv('STOP_METRICS_DIR')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output)/f'midwait-{offset_ms}.json').write_text(json.dumps(metrics, indent=2))
        assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], autonomous.Stopped)
        assert not transport.posts and not admissions and len(probes) == 1
        assert source.read_text() == 'value = 1\n' and not (work.path/'application').exists()
        assert returned-issued < .5, metrics
    finally:
        thread.join(5)