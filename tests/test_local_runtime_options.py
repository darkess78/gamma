"""Real adapter payload seams for finite private runtime profiles."""
from contextlib import contextmanager
from io import BytesIO
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from gamma.config import settings
from gamma.llm.local_adapter import LocalLLMAdapter, local_inference_admission
from gamma.llm.local_runtime import local_runtime_profile, current_local_runtime_profile
from gamma.coordination.turn_adapter import PrivateTurnAdapter
from gamma.safety.llm_reviewer import SpeechLLMReviewer

METRICS = {'total_duration': 800_000_000, 'load_duration': 100_000_000,
           'prompt_eval_duration': 200_000_000, 'eval_duration': 500_000_000,
           'prompt_eval_count': 40, 'eval_count': 25}


def configure(monkeypatch):
    for key, value in {'local_llm_endpoint': 'http://127.0.0.1:11434',
                       'local_llm_keep_alive': '0', 'local_llm_model': 'qwen3.8:27b',
                       'local_llm_enable_routing': False, 'speech_filter_llm_enabled': True,
                       'speech_filter_llm_model': 'gemma4:e4b',
                       'speech_filter_llm_temperature': 0.0}.items():
        monkeypatch.setattr(settings, key, value)


def transport(monkeypatch, module, response):
    import importlib
    target = importlib.import_module(module)
    calls, slots = [], []
    def urlopen(req, **kwargs):
        calls.append({'url': req.full_url, 'payload': json.loads(req.data), 'timeout': kwargs['timeout']})
        value = response() if callable(response) else response
        return BytesIO(json.dumps(value).encode())
    @contextmanager
    def slot(endpoint, **kwargs):
        slots.append((endpoint, kwargs))
        if kwargs.get('checkpoint'):
            kwargs['checkpoint']()
        yield
    monkeypatch.setattr(target.request, 'urlopen', urlopen)
    monkeypatch.setattr(target, 'backend_slot', slot)
    return calls, slots


def test_primary_default_payload_scope_and_numeric_metrics(monkeypatch):
    configure(monkeypatch)
    calls, slots = transport(monkeypatch, 'gamma.llm.local_adapter',
        {'response': 'hello', **METRICS, 'prompt': 'must not leak', 'secret': 'never metadata'})
    adapter = LocalLLMAdapter(think=False, maximum_output_tokens=1024, maximum_context_tokens=8192)
    before = adapter.generate_reply('system', 'input')
    with local_runtime_profile(primary_keep_alive='5m', review_endpoint='http://127.0.0.1:11435',
                               review_keep_alive='5m', review_num_gpu=0):
        scoped = adapter.generate_reply('system', 'input')
    adapter.generate_reply('system', 'input')
    assert calls[0] == calls[2]
    assert {key for key in calls[0]['payload'] if calls[0]['payload'][key] != calls[1]['payload'][key]} == {'keep_alive'}
    assert calls[1]['payload']['keep_alive'] == '5m'
    assert calls[1]['payload']['think'] is False
    assert calls[1]['payload']['options'] == {'num_ctx': 8192, 'num_predict': 1024}
    assert {endpoint for endpoint, _ in slots} == {'http://127.0.0.1:11434'}
    assert before.text == scoped.text == 'hello'
    metrics = scoped.metadata['local_timing']
    assert metrics['load_ms'] == 100.0 and metrics['tokens_per_second'] == 50.0
    assert {'admission_ms', 'http_ms'} <= metrics.keys()
    assert all(type(value) in (int, float) and value >= 0 for value in metrics.values())
    assert 'must not leak' not in repr(scoped.metadata) and 'never metadata' not in repr(scoped.metadata)


def test_private_adapter_constructor_and_context_restore(monkeypatch):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.llm.local_adapter', {'response': 'hello'})
    adapter = PrivateTurnAdapter(keep_alive=300)
    adapter.generate_reply('system', 'input')
    with pytest.raises(RuntimeError):
        with local_runtime_profile(primary_keep_alive='5m'):
            with adapter.interaction_turn(True):
                adapter.generate_reply('system', 'input')
                raise RuntimeError('cancelled')
    adapter.generate_reply('system', 'input')
    assert calls[0] == calls[2]
    assert calls[0]['payload']['keep_alive'] == 300
    assert calls[1]['payload']['keep_alive'] == '5m'
    assert current_local_runtime_profile() == {}


def test_reviewer_default_payload_scope_and_independent_model(monkeypatch):
    configure(monkeypatch)
    value = {'response': json.dumps({'action': 'allow', 'reason': 'ordinary', 'confidence': 0.95}), **METRICS}
    calls, slots = transport(monkeypatch, 'gamma.safety.llm_reviewer', value)
    reviewer = SpeechLLMReviewer()
    first = reviewer.review('Hello.')
    with local_runtime_profile(primary_keep_alive='5m', review_endpoint='http://127.0.0.1:11435',
                               review_keep_alive='5m', review_num_gpu=0):
        second = reviewer.review('Hello.')
    reviewer.review('Hello.')
    assert calls[0] == calls[2]
    assert calls[0]['payload']['keep_alive'] == 0
    assert calls[0]['payload']['options'] == {'temperature': 0.0, 'num_ctx': 8192, 'num_predict': 128}
    assert calls[0]['url'] == 'http://127.0.0.1:11434/api/generate'
    assert calls[1]['url'] == 'http://127.0.0.1:11435/api/generate'
    scoped_payload = calls[1]['payload']
    assert scoped_payload['model'] == calls[0]['payload']['model'] == 'gemma4:e4b'
    assert scoped_payload['system'] == calls[0]['payload']['system']
    assert scoped_payload['keep_alive'] == '5m' and scoped_payload['options']['num_gpu'] == 0
    assert slots[1][0] == 'http://127.0.0.1:11435'
    assert first.action == second.action == 'allow'
    assert second.metadata['load_ms'] == 100.0
    assert {'review_ms', 'admission_ms', 'http_ms'} <= second.metadata.keys()
    assert all(type(v) in (int, float) and v >= 0 for v in second.metadata.values())


def test_reviewer_explicit_constructor_and_checkpoint_priority(monkeypatch):
    configure(monkeypatch)
    calls, slots = transport(monkeypatch, 'gamma.safety.llm_reviewer',
        {'response': json.dumps({'action': 'block', 'reason': 'harm', 'confidence': 1.0})})
    reviewer = SpeechLLMReviewer(endpoint='http://127.0.0.1:11435/', num_gpu=0, keep_alive=300)
    checkpoints = []
    with local_inference_admission(foreground=False, checkpoint=lambda: checkpoints.append('checked')):
        decision = reviewer.review('unsafe fixture')
    assert decision.action == 'block' and checkpoints == ['checked']
    assert slots[0][0] == 'http://127.0.0.1:11435'
    assert slots[0][1]['foreground'] is False
    assert calls[0]['payload']['options']['num_gpu'] == 0 and calls[0]['payload']['keep_alive'] == 300


def test_reviewer_checkpoint_failure_propagates_and_does_not_send(monkeypatch):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.safety.llm_reviewer', {})
    problem = RuntimeError('ownership lost')
    def checkpoint():
        raise problem
    with local_runtime_profile(review_endpoint='http://127.0.0.1:11435', review_num_gpu=0):
        with local_inference_admission(foreground=True, checkpoint=checkpoint):
            with pytest.raises(RuntimeError) as caught:
                SpeechLLMReviewer().review('Hello.')
    assert caught.value is problem and calls == []
    assert current_local_runtime_profile() == {}


@pytest.mark.parametrize('response', [
    {}, {'response': 'not json'}, {'response': '{"action":"allow","confidence":true}'},
    {'response': '{"action":"allow","confidence":1.1}'},
    {'response': '{"action":"allow","confidence":NaN}'},
])
def test_reviewer_invalid_response_still_softens_without_fallback(monkeypatch, response):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.safety.llm_reviewer', response)
    with local_runtime_profile(review_endpoint='http://127.0.0.1:11435', review_num_gpu=0):
        decision = SpeechLLMReviewer().review('Hello.')
    assert decision.action == 'soften' and decision.reason == 'llm-reviewer-unavailable'
    assert len(calls) == 1 and calls[0]['url'].startswith('http://127.0.0.1:11435/')
    assert decision.metadata['review_ms'] >= 0


def test_reviewer_metadata_isolated_between_threads(monkeypatch):
    configure(monkeypatch)
    import gamma.safety.llm_reviewer as module
    reviewer = SpeechLLMReviewer()
    barrier = threading.Barrier(2)
    def urlopen(req, **kwargs):
        count = int(json.loads(req.data)['prompt'])
        barrier.wait(timeout=5)
        return BytesIO(json.dumps({'response': '{"action":"allow","reason":"safe","confidence":1}',
                                  'eval_count': count}).encode())
    @contextmanager
    def slot(*args, **kwargs):
        yield
    monkeypatch.setattr(module.request, 'urlopen', urlopen)
    monkeypatch.setattr(module, 'backend_slot', slot)
    def run(count):
        with local_runtime_profile(review_endpoint='http://127.0.0.1:11435', review_num_gpu=0):
            result = reviewer.review(str(count))
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions = list(pool.map(run, [12, 29]))
    assert [v.metadata['eval_count'] for v in decisions] == [12, 29]
    assert decisions[0].metadata is not decisions[1].metadata
    assert current_local_runtime_profile() == {}


@pytest.mark.parametrize('factory, kwargs', [
    (LocalLLMAdapter, {'keep_alive': -1}),
    (LocalLLMAdapter, {'keep_alive': True}),
    (SpeechLLMReviewer, {'keep_alive': '31m'}),
    (SpeechLLMReviewer, {'endpoint': 'http://evil.example:11435'}),
    (SpeechLLMReviewer, {'endpoint': 'http://127.0.0.1:11435?x=1'}),
    (SpeechLLMReviewer, {'num_gpu': True}),
    (SpeechLLMReviewer, {'num_gpu': 1}),
])
def test_invalid_constructor_options_are_rejected(factory, kwargs):
    with pytest.raises(ValueError):
        factory(**kwargs)


@pytest.mark.parametrize('value', [True, False, 0, 17, -1, 8.0, '8'])
def test_invalid_reviewer_cpu_threads_rejected(value):
    from gamma.llm.local_runtime import validate_review_num_threads
    with pytest.raises(ValueError):
        validate_review_num_threads(value)
    with pytest.raises(ValueError):
        SpeechLLMReviewer(num_threads=value)
    with pytest.raises(ValueError):
        with local_runtime_profile(review_num_threads=value):
            pass
    assert current_local_runtime_profile() == {}


def test_scoped_cpu_threads_preserve_independent_reviewer_and_restore(monkeypatch):
    configure(monkeypatch)
    value = {'response': '{"action":"allow","reason":"ordinary","confidence":1}'}
    calls, _ = transport(monkeypatch, 'gamma.safety.llm_reviewer', value)
    reviewer = SpeechLLMReviewer(num_threads=4)
    reviewer.review('Hello.')
    with local_runtime_profile(review_endpoint='http://127.0.0.1:11435',
                               review_num_gpu=0, review_num_threads=8, review_keep_alive='30m'):
        reviewer.review('Hello.')
    reviewer.review('Hello.')
    assert calls[0] == calls[2]
    assert calls[0]['payload']['options']['num_thread'] == 4
    assert calls[1]['payload']['options']['num_thread'] == 8
    assert calls[1]['payload']['options']['num_gpu'] == 0
    assert calls[1]['payload']['keep_alive'] == '30m'
    assert calls[1]['payload']['model'] == 'gemma4:e4b'
    assert calls[1]['url'] == 'http://127.0.0.1:11435/api/generate'
    from gamma.llm.local_runtime import validate_review_num_threads
    assert [validate_review_num_threads(v) for v in [1, 8, 16]] == [1, 8, 16]


@pytest.mark.parametrize('value', [True, False, -1, 1, 16, 24, 31, 33, 64, 32.0, '32'])
def test_unmeasured_reviewer_offload_rejected(value):
    from gamma.llm.local_runtime import validate_review_num_gpu
    with pytest.raises(ValueError):
        validate_review_num_gpu(value)
    with pytest.raises(ValueError):
        SpeechLLMReviewer(num_gpu=value)


def test_measured_32_layer_reviewer_profile_is_explicit_and_keeps_model(monkeypatch):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.safety.llm_reviewer',
        {'response': '{"action":"allow","reason":"ordinary","confidence":1}'})
    with local_runtime_profile(review_endpoint='http://127.0.0.1:11435',
                               review_num_gpu=32, review_num_threads=8, review_keep_alive='30m'):
        result = SpeechLLMReviewer().review('Hello.')
    assert result.action == 'allow'
    assert calls[0]['payload']['options']['num_gpu'] == 32
    assert calls[0]['payload']['model'] == 'gemma4:e4b'
    assert calls[0]['url'] == 'http://127.0.0.1:11435/api/generate'
    assert current_local_runtime_profile() == {}


@pytest.mark.parametrize('value', [True, False, -0.01, 1.01, 10**400, float('nan'), float('inf'), -float('inf'), '0.2'])
def test_invalid_primary_temperatures_rejected(value):
    from gamma.llm.local_runtime import validate_primary_temperature
    with pytest.raises(ValueError):
        validate_primary_temperature(value)
    with pytest.raises(ValueError):
        LocalLLMAdapter(temperature=value)
    with pytest.raises(ValueError):
        with local_runtime_profile(primary_temperature=value):
            pass
    assert current_local_runtime_profile() == {}


def test_primary_temperature_is_scoped_and_defaults_are_unchanged(monkeypatch):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.llm.local_adapter', {'response': 'hello'})
    adapter = LocalLLMAdapter()
    adapter.generate_reply('system', 'input')
    with local_runtime_profile(primary_temperature=.2):
        adapter.generate_reply('system', 'input')
    adapter.generate_reply('system', 'input')
    assert calls[0] == calls[2]
    assert 'temperature' not in calls[0]['payload'].get('options', {})
    assert calls[1]['payload']['options']['temperature'] == .2
    explicit = LocalLLMAdapter(temperature=0)
    explicit.generate_reply('system', 'input')
    assert calls[3]['payload']['options']['temperature'] == 0
    from gamma.llm.local_runtime import validate_primary_temperature
    assert [validate_primary_temperature(v) for v in [0, .2, 1]] == [0, .2, 1]


def test_primary_temperature_does_not_change_independent_review_sampling(monkeypatch):
    configure(monkeypatch)
    calls, _ = transport(monkeypatch, 'gamma.safety.llm_reviewer',
        {'response': '{"action":"allow","reason":"ordinary","confidence":1}'})
    with local_runtime_profile(primary_temperature=.2):
        result = SpeechLLMReviewer().review('Hello.')
    assert result.action == 'allow'
    assert calls[0]['payload']['options']['temperature'] == 0.0
