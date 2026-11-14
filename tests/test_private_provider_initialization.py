"""Real private factory provider caching, with no model or network inference."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest

import gamma.config as configuration
import gamma.coordination.interaction as interaction
from gamma.coordination.interaction_routes import local_interaction_factory
import gamma.voice.stt as stt
import gamma.voice.tts as tts
import gamma.voice.voice_profiles as profiles


@pytest.mark.parametrize('kind', ['stt', 'tts'])
def test_concurrent_cold_provider_initializes_once_and_warm_calls_are_not_serialized(tmp_path, monkeypatch, kind):
    output = tmp_path / 'data' / 'audio'
    output.mkdir(parents=True)
    settings = SimpleNamespace(stt_provider='stub', tts_provider='stub',
                               project_root=tmp_path, audio_output_dir=output)
    monkeypatch.setattr(configuration, 'settings', settings)
    monkeypatch.setattr(profiles, 'resolve_tts_config', lambda:SimpleNamespace(provider='stub'))
    monkeypatch.setattr(interaction, 'PrivateInteraction',
        lambda core, **callbacks:SimpleNamespace(**callbacks))
    first_constructor, second_constructor, release = threading.Event(), threading.Event(), threading.Event()
    guard, warm_barrier = threading.Lock(), threading.Barrier(2)
    state = {'constructors':0, 'warm':False, 'calls':0}
    class Provider:
        def __init__(self):
            with guard:
                state['constructors'] += 1
                event = first_constructor if state['constructors'] == 1 else second_constructor
                event.set()
            assert release.wait(3)
        def enter_callback(self):
            with guard:
                state['calls'] += 1
            if state['warm']:
                warm_barrier.wait(timeout=1)
        def transcribe_audio(self, path):
            self.enter_callback()
            return Path(path).read_bytes().decode()
        def synthesize(self, text, emotion):
            self.enter_callback()
            path = output / (uuid.uuid4().hex + '.wav')
            path.write_bytes(text.encode())
            return SimpleNamespace(audio_path=path, content_type='audio/wav')
    monkeypatch.setattr(stt, 'STTService', Provider)
    monkeypatch.setattr(tts, 'TTSService', Provider)
    factory = local_interaction_factory(tmp_path / 'voice-state')
    callbacks = factory(None)
    assert state['constructors'] == 0, 'Provider was eagerly loaded.'
    call = (lambda text:callbacks.transcribe(text.encode())) if kind == 'stt' else (
        lambda text:callbacks.synthesize(text, 'neutral')[0].decode())
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(call, 'first')
        assert first_constructor.wait(1)
        second = pool.submit(call, 'second')
        try:
            assert not second_constructor.wait(.1), 'Concurrent first inputs loaded the same model twice.'
        finally:
            release.set()
        assert first.result(2) == 'first' and second.result(2) == 'second'
        assert state['constructors'] == 1
        state['warm'] = True
        warm = [pool.submit(call, text) for text in ('third', 'fourth')]
        assert [future.result(2) for future in warm] == ['third', 'fourth']
    assert state['constructors'] == 1 and state['calls'] == 4
    assert not list(output.iterdir())
    assert not list((tmp_path / 'voice-state' / 'voice-tmp').iterdir())
