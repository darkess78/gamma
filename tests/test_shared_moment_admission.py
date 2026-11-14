"""Actual adapter admission is inherited by both historical-channel calls."""
from contextlib import contextmanager
import json
from threading import Event, Thread
import time
from urllib.parse import urlsplit

from gamma.coordination.shared_vision import local_image_analyzer
from gamma.llm import local_adapter as local
from gamma.llm.backend_admission import backend_slot
from gamma.schemas.response import AssistantResponse
from test_shared_activity import setup
from test_shared_moments import moments, metadata, IMAGE
from test_shared_watch import enable


class HTTPReply:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self): return json.dumps({'response': self.payload}).encode()


def adapters(monkeypatch, core, activity, service, *, admission=None):
    calls = []
    monkeypatch.setattr(local.settings, 'local_llm_supports_vision', True)
    monkeypatch.setattr(local, 'probe_ollama_model_capabilities', lambda **_: {'ok': True, 'supports_vision': True})
    def http(req, **_):
        values = json.loads(req.data)
        calls.append(values)
        return HTTPReply(json.dumps({'summary': 'An authored moving application capture.', 'confidence': .6})
                         if values.get('images') else 'A historical reflection.')
    monkeypatch.setattr(local.request, 'urlopen', http)
    if admission is not None: monkeypatch.setattr(local, 'backend_slot', admission)
    image = local_image_analyzer()
    activity.analyze_image, service.background_image_analyzer = image, image.background
    adapter = local.LocalLLMAdapter()
    core.draft = lambda text, context: AssistantResponse(display_text=adapter.generate_reply(text, context).text)
    return adapter, calls


def test_full_watch_and_manual_core_flow_has_one_slot_per_call_and_thread_local_defaults(moments, monkeypatch):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    slots, checkpoints = [], []
    @contextmanager
    def admission(endpoint, **kwargs):
        slots.append(kwargs)
        if kwargs.get('checkpoint'):
            kwargs['checkpoint'](); checkpoints.append(1)
        yield
    adapter, calls = adapters(monkeypatch, core, activity, service, admission=admission)
    result = watch.share(row['id'], row['lease'], 'watch_moment', metadata(activity, now), IMAGE, 'image/png')
    assert result['moment']['status'] == 'generated'
    assert [s['foreground'] for s in slots] == [False, False]
    assert len(checkpoints) == len(calls) == 2 and all(s.get('checkpoint') for s in slots)
    watch.stop(row['id'], row['lease'])
    now[0] += 10
    assert service.share('manual', metadata(activity, now, 1), IMAGE, 'image/png')['status'] == 'generated'
    assert [s['foreground'] for s in slots] == [False, False, True, True]
    adapter.generate_reply('ordinary', 'outside channel')
    with local.local_inference_admission(foreground=False):
        thread = Thread(target=lambda: adapter.generate_reply('ordinary', 'independent thread'))
        thread.start(); thread.join(5); assert not thread.is_alive()
    assert [s['foreground'] for s in slots[-2:]] == [True, True]
    assert all('checkpoint' not in s for s in slots[-2:])


def test_real_foreground_waiter_wins_before_queued_background(tmp_path, monkeypatch):
    # A unique synthetic origin; no HTTP and no coordination with a live model.
    from gamma.llm import backend_admission as admission
    monkeypatch.setattr(admission, '_coordination_root', lambda: tmp_path / 'admission')
    endpoint = 'http://controlled-admission.invalid:12967/'
    waiting, foreground_ready, release_foreground = Event(), Event(), Event()
    order = []
    def foreground():
        with backend_slot(endpoint, foreground=True, timeout=5):
            order.append('foreground'); foreground_ready.set(); assert release_foreground.wait(5)
    def background():
        with backend_slot(endpoint, foreground=False, timeout=5, checkpoint=waiting.set): order.append('background')
    with backend_slot(endpoint, foreground=False, timeout=5):
        b = Thread(target=background); f = Thread(target=foreground)
        b.start(); assert waiting.wait(5); f.start()
        root, key = admission._coordination_root(), admission._resource_key(endpoint)
        deadline = time.monotonic() + 3
        while not list(root.glob(key + '.wait-*')) and time.monotonic() < deadline: time.sleep(.01)
        assert list(root.glob(key + '.wait-*'))
    try:
        assert foreground_ready.wait(5) and order == ['foreground']
    finally:
        release_foreground.set(); f.join(5); b.join(5)
    assert not f.is_alive() and not b.is_alive() and order == ['foreground', 'background']


def test_queued_watch_checkpoint_observes_actual_core_foreground_without_store_deadlock(moments, monkeypatch, tmp_path):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    from gamma.llm import backend_admission as admission
    monkeypatch.setattr(admission, '_coordination_root', lambda: tmp_path / 'admission')
    monkeypatch.setattr(local.settings, 'local_llm_endpoint', 'http://controlled-admission.invalid:12968')
    queued, foreground_entered, release_foreground = Event(), Event(), Event()
    results, failures = [], []
    @contextmanager
    def observed_slot(endpoint, **kwargs):
        queued.set()
        with backend_slot(endpoint, **kwargs): yield
    adapter, calls = adapters(monkeypatch, core, activity, service, admission=observed_slot)
    def draft(*_):
        foreground_entered.set(); assert release_foreground.wait(5)
        return AssistantResponse(display_text='Actual pending foreground turn.')
    core.draft = draft
    def moment():
        try: results.append(watch.share(row['id'], row['lease'], 'queued', metadata(activity, now), IMAGE, 'image/png'))
        except BaseException as exc: failures.append(exc)
    with backend_slot(local.settings.local_llm_endpoint, foreground=False, timeout=5):
        m = Thread(target=moment); m.start(); assert queued.wait(5)
        f = Thread(target=lambda: core.input('An authored foreground interruption.', 'controlled', 'real_foreground'))
        f.start(); assert foreground_entered.wait(5)
        m.join(3)
        assert not m.is_alive() and not failures and results[0]['moment']['status'] == 'cancelled'
        assert calls == []  # Known queued cancellation, never submitted to HTTP.
        assert activity.status()['state']['enabled']  # No unknown-outcome shutdown.
        release_foreground.set(); f.join(5)
        assert not f.is_alive()
