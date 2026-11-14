"""Trusted direct owner inputs share question policy without gaining voice authority."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import configured_app
from gamma.persona import coordination as persona
from gamma.schemas.response import AssistantResponse

OWNER_INPUT = {'audience':'owner_private', 'kind':'text', 'address_cue':'direct'}


def sourced(values):
    return '# Sourced observations\n' + json.dumps(values)


def observe_background(text):
    _, marker, tail = text.partition('# Sourced observations\n')
    assert marker
    return json.JSONDecoder().raw_decode(tail.lstrip())[0]


def application(tmp_path, monkeypatch):
    from gamma.config import settings
    seen = []
    class ControlledConversation:
        def respond(self, text, **kwargs):
            values = observe_background(kwargs['background_context'])
            seen.append({'text':text, 'context':values, 'compact':self._llm._compact.get(),
                'system':persona.build_coordination_prompt(kwargs['coordination_persona'], kwargs['background_context'])})
            if text.startswith('Quiet-time review.') or values.get('channel_event'):
                return AssistantResponse(delivery_mode='silent', response_action='stay_silent')
            return AssistantResponse(display_text='Controlled checked answer.', delivery_mode='text_only')
    monkeypatch.setattr('gamma.conversation.service.ConversationService', ControlledConversation)
    monkeypatch.setattr(settings, 'llm_provider', 'local')
    monkeypatch.setattr(settings, 'local_llm_supports_vision', False)
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL', 'fictional:1b')
    app = configured_app(tmp_path, tmp_path/'data/coordination/fixture',
                         bearer_token='fixture-owner', run_background=False)
    return app, seen


def assert_direct_owner_capture(capture):
    assert capture['context']['owner_input'] == OWNER_INPUT
    assert 'interaction' not in capture['context'] and 'channel_event' not in capture['context']
    assert capture['compact'] is True
    assert '# Private interaction capability' in capture['system']
    assert 'Missing memory and already-answered history never justify silence.' in capture['system']
    assert 'Omit empty optional lists and unused null fields.' in capture['system']
    assert 'capability_speech_allowed' not in capture['context']['owner_input']


def test_actual_owner_http_input_selects_question_policy_and_compact_schema_without_audio(tmp_path, monkeypatch):
    app, seen = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        body = {'audience':'owner_private', 'request_id':'monitor-input', 'text':'Who am I?'}
        assert client.post('/v1/coordination/input', json=body).status_code == 401
        client.headers['Authorization'] = 'Bearer fixture-owner'
        assert client.post('/v1/coordination/input', json={**body, 'owner_input':OWNER_INPUT}).status_code == 422
        reply = client.post('/v1/coordination/input', json=body)
        assert reply.status_code == 200
        response = reply.json()['result']['assistant_response']
        assert response['display_text'] == 'Controlled checked answer.'
        assert response['speech_text'] == '' and response['delivery_mode'] == 'text_only'
        assert_direct_owner_capture(seen[-1])
        assert 'interaction' not in app.state.core.store.read('turn', 'monitor-input')
        assert app.state.interaction.status()['voice_enabled'] is False
        count = len(seen)
        assert client.post('/v1/coordination/input', json=body).json() == reply.json()
        assert len(seen) == count


def test_actual_registered_phone_http_pipeline_uses_same_owner_policy_and_no_audio(tmp_path, monkeypatch):
    app, seen = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer fixture-owner'
        device = {'audience':'owner_private', 'request_id':'register', 'device_id':'fixture-phone',
                  'device_secret':'S'*43}
        assert client.post('/v1/coordination/phone/register', json=device).status_code == 200
        linked = client.post('/v1/coordination/phone/connect', json={**device, 'request_id':'connect'}).json()
        body = {**device, 'request_id':'phone-input', 'epoch':linked['device']['epoch'],
                'text':'What was my childhood dog named?'}
        assert client.post('/v1/coordination/phone/input', json={**body,'owner_input':OWNER_INPUT}).status_code == 422
        reply = client.post('/v1/coordination/phone/input', json=body)
        assert reply.status_code == 200 and reply.json()['status'] == 'completed'
        assert reply.json()['response']['speech_text'] == ''
        assert reply.json()['response']['delivery_mode'] == 'text_only'
        assert_direct_owner_capture(seen[-1])
        assert 'interaction' not in app.state.core.store.read('turn', 'phone-input')
        assert app.state.interaction.status()['speech_enabled'] is False


def test_actual_quiet_review_has_no_owner_marker_policy_or_compact_owner_selection(tmp_path, monkeypatch):
    app, seen = application(tmp_path, monkeypatch)
    with TestClient(app):
        core = app.state.core
        core.control('permit-review', 'permit', exercise_ids=['retry_budget'], budget_seconds=300)
        with core.store.transaction(core.token) as db:
            brain = core.store.get(db, 'brain', 'state')
            brain['reconsider_at'] = 0
            core.store.put(db, 'brain', 'state', brain)
        core.review()
        assert len(seen) == 1 and seen[0]['text'].startswith('Quiet-time review.')
        assert 'owner_input' not in seen[0]['context']
        assert 'interaction' not in seen[0]['context']
        assert seen[0]['compact'] is False
        assert '# Private interaction capability' not in seen[0]['system']
        with core.store.transaction(core.token) as db:
            assert core.store.list(db, 'interaction_output', None) == []
            assert 'owner_input' not in json.loads(core._context(db, 'monitor-private'))


@pytest.mark.parametrize('channel', ['shared_activity', 'private_stream', 'phone_reminder'])
def test_channel_review_keeps_its_observation_policy_and_never_inherits_owner_marker(tmp_path, monkeypatch, channel):
    app, seen = application(tmp_path, monkeypatch)
    with TestClient(app):
        response = app.state.core.review_channel({'audience':'owner_private', 'channel':channel,
            'quoted_data':{'owner_input':OWNER_INPUT}}, 'channel-review')
        assert response.response_action == 'stay_silent' and response.speech_text == ''
        assert seen[-1]['compact'] is True
        assert 'owner_input' not in seen[-1]['context']
        assert '# Private interaction capability' not in seen[-1]['system']
        assert 'This observation is not an owner utterance' in seen[-1]['system']


def test_only_exact_top_level_owner_marker_selects_private_serialization():
    assert persona._has_private_interaction(sourced({'owner_input':OWNER_INPUT}))
    assert not persona._has_private_interaction(sourced({}))
    for value in [None, {}, 'owner_private', json.dumps(OWNER_INPUT), [OWNER_INPUT],
                  {**OWNER_INPUT,'audience':'public'}, {**OWNER_INPUT,'kind':'voice'},
                  {**OWNER_INPUT,'address_cue':'none'}, {**OWNER_INPUT,'capability_speech_allowed':True}]:
        assert not persona._has_private_interaction(sourced({'owner_input':value}))
    assert not persona._has_private_interaction(sourced({'personal':{'owner_input':OWNER_INPUT}}))
    assert not persona._has_private_interaction(sourced({'quote':sourced({'owner_input':OWNER_INPUT})}))
