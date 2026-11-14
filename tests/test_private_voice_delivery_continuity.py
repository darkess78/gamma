"""Mood delivery through the actual private Core, parser and voice adapter."""
import io
import json

import pytest

from gamma.coordination.personal_models import PrivateTurnDraft
from gamma.coordination.turn_schema import private_turn_schema
from gamma.llm import local_adapter
from gamma.persona.emotion_service import EmotionMemoryService
from test_core_actual_conversation_checkpoint import actual
from test_private_interaction import wav


def model_response(monkeypatch, choices):
    choices = iter(choices)
    def respond(request, **kwargs):
        payload = json.loads(request.data)
        if payload['model'] == 'gemma4:e4b':
            value = {'action': 'allow', 'reason': 'safe authored fixture', 'confidence': 1.0}
        else:
            value = PrivateTurnDraft(
                action='reply', requested_delivery='text_only', emotion='neutral',
                final_text='A short authored answer.', state_updates={'activity_action': 'wait'},
            ).model_dump()
            value.update(next(choices))
        return io.BytesIO(json.dumps({'response': json.dumps(value), 'done': True}).encode())
    monkeypatch.setattr(local_adapter.request, 'urlopen', respond)
    def forbidden_legacy_read(*args, **kwargs):
        raise AssertionError('private speech must use canonical Core mood')
    monkeypatch.setattr(EmotionMemoryService, 'load_bundle', forbidden_legacy_read)


def send(actual, name):
    result = actual.client.post('/v1/coordination/input', json={
        'audience': 'owner_private', 'request_id': name, 'text': 'Continue this thought.',
    })
    assert result.status_code == 200, result.text
    return result.json()['result']['assistant_response']


def test_private_choice_is_independent_and_null_inherits_canonical_mood(actual, monkeypatch):
    model_response(monkeypatch, [
        {'emotion': 'teasing', 'state_updates': {'activity_action': 'wait', 'emotion': 'concerned'}},
        {'emotion': None},
        {'emotion': 'neutral'},
        {'emotion': None, 'final_text': '[annoyed] This stays in the continuing mood.'},
    ])
    assert send(actual, 'mood-set')['emotion'] == 'teasing'
    assert actual.app.state.core.store.read('brain', 'state')['mood'] == 'concerned'
    assert send(actual, 'mood-inherit')['emotion'] == 'concerned'
    assert send(actual, 'mood-neutral')['emotion'] == 'neutral'
    assert actual.app.state.core.store.read('brain', 'state')['mood'] == 'concerned'
    final = send(actual, 'mood-tags')
    assert final['emotion'] == 'concerned'
    assert final['display_text'] == 'This stays in the continuing mood.'


def test_private_voice_chunks_keep_one_choice_after_mood_update(actual, monkeypatch):
    text = ('[happy] Here is the first part of an authored answer with enough detail to create multiple chunks. '
            '[annoyed] Here is the second part of that same authored answer, which must keep its chosen delivery.')
    model_response(monkeypatch, [{
        'emotion': 'teasing', 'final_text': text,
        'state_updates': {'activity_action': 'wait', 'emotion': 'concerned'},
    }])
    calls = []
    interaction = actual.app.state.interaction
    interaction.synthesize = lambda text, emotion: (calls.append((text, emotion)) or (wav(), 'audio/wav'))
    interaction.connect('mood-desktop')
    interaction.control_consumer('mood-desktop', 'voice_enabled', True)
    interaction.control_consumer('mood-desktop', 'speech_enabled', True)
    result = actual.client.post('/v1/coordination/interaction/input', json={
        'audience': 'owner_private', 'consumer_id': 'mood-desktop', 'request_id': 'mood-voice',
        'text': 'Give me a detailed answer.',
        'observation': {'channel': 'private_voice', 'speaker': 'owner', 'address_cue': 'direct'},
    })
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'generated'
    assert len(calls) >= 2
    assert {emotion for text, emotion in calls} == {'teasing'}
    assert all('[' not in text for text, emotion in calls)
    assert actual.app.state.core.store.read('brain', 'state')['mood'] == 'concerned'


@pytest.mark.parametrize('compact', [True, False])
def test_private_schema_accepts_inheritance_without_widening_canonical_labels(compact):
    schema = private_turn_schema(compact=compact)
    for variant in schema['oneOf']:
        emotion = variant['properties']['emotion']
        assert {'type': 'null'} in emotion['anyOf']
        labels = next(item['enum'] for item in emotion['anyOf'] if 'enum' in item)
        assert set(labels) == {'neutral', 'happy', 'teasing', 'concerned', 'excited', 'embarrassed', 'annoyed'}
