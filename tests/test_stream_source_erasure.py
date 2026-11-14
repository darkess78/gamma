"""Forgotten identity facts must not survive in dependent transport caches."""
import json
from threading import Event, Thread
from time import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.channel_audio import ChannelAudio
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.coordination.interaction import PrivateInteraction
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse
from test_stream_session import make, opened, event, ack
from test_channel_audio import wav


SECRET = 'PRIVATE_SOURCE_ERASURE_VIOLET_7349'


def prepared(tmp_path):
    contexts = []
    def draft(_, raw):
        context = json.loads(raw)
        contexts.append(context)
        fields = context['personal']['identity']['fields']
        return AssistantResponse(display_text=fields.get('description', 'A safe unrelated reply.'), delivery_mode='text_only')
    core, stream, clock = make(tmp_path, draft)
    core.input('A separate ordinary exchange.', 'safe-session', 'safe-turn')
    safe = core.store.read('turn', 'safe-turn')
    core.personal_control('private-description', 'identity', {'fields': {'description': SECRET}})
    source = core.personal_state()['identity']['field_sources']['description']
    session = opened(stream)
    return core, stream, clock, contexts, source, session, safe


def test_forget_physically_redacts_delivered_stream_text_and_cached_owner_catch_up(tmp_path):
    core, stream, clock, contexts, source, session, safe = prepared(tmp_path)
    try:
        original = event(session, clock)
        result = stream.submit(original)
        output = stream.next_output(session, 'owner')['output']
        assert output['text'] == SECRET
        stream.ack(ack(output))
        caught = stream.control('cached-return', session, 'owner_return')
        assert caught['catch_up'][0]['text'] == SECRET
        before = core.store.read('stream_rehearsal_event', original['event_id'])
        core.personal_control('remove-private-description', 'forget', {'target_id': source})
        assert SECRET not in json.dumps(core.personal_state())
        # Inspect persistent bytes before polling can lazily clean anything.
        row = core.store.read('stream_rehearsal_output', result['output_id'])
        assert row['text'] == ''
        assert row['status'] == 'delivered' and row['received_at'] == output['created_at']
        assert row['source_redacted'] is True
        assert SECRET not in json.dumps(core.store.read('stream_control', 'cached-return'))
        assert stream.control('cached-return', session, 'owner_return')['catch_up'] == []
        after = core.store.read('stream_rehearsal_event', original['event_id'])
        assert after['fingerprint'] == before['fingerprint']
        assert core.store.read('turn', 'safe-turn') == safe
        assert stream.submit(original)['status'] != 'ready'
        assert len(contexts) == 2
    finally:
        stream.close(); core.close()


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('audio_status', ['generated', 'played'])
def test_source_forget_immediately_erases_bound_text_and_pcm_but_preserves_safe_receipts(tmp_path, legacy, audio_status):
    core, stream, clock, contexts, source, session, safe = prepared(tmp_path)
    calls = []
    interaction = PrivateInteraction(core, clock=clock, synthesize=lambda text, *_: calls.append(text) or (wav(), 'audio/wav'))
    audio = ChannelAudio(interaction)
    try:
        audio.connect('owner'); audio.enable('owner', True)
        result = stream.submit(event(session, clock))
        identifier = result['output_id']
        row = core.store.read('stream_rehearsal_output', identifier)
        assert row['review_id'].startswith('stream-review-')
        if legacy:
            with core.store.transaction(core.token) as db:
                row.pop('review_id')
                core.store.put(db, 'stream_rehearsal_output', identifier, row)
        receipt = audio.render(identifier, 'owner')
        assert receipt['status'] == 'generated'
        request_id = 'channel-audio-' + identifier.rsplit('-', 1)[-1]
        fingerprint = core.store.read('channel_audio_request', request_id)['fingerprint']
        output = interaction.next_output('owner')['output']
        path = interaction.root / (output['id'] + '.wav')
        assert path.exists() and interaction.audio(output['id'], 'owner', output['generation'], output['output_epoch'])[0] == wav()
        if audio_status == 'played':
            interaction.acknowledge('owner', output['id'], output['generation'], output['output_epoch'], 'started')
            interaction.acknowledge('owner', output['id'], output['generation'], output['output_epoch'], 'played')
        core.personal_control('remove', 'forget', {'target_id': source})
        # No poll/expiry call is needed for SQL payload or physical PCM erasure.
        assert not path.exists()
        cached = core.store.read('interaction_output', output['id'])
        assert cached['display_text'] == cached['speech_text'] == '' and not cached['audio_available']
        assert cached['status'] == ('played' if audio_status == 'played' else 'cancelled')
        assert core.store.read('channel_audio_request', request_id)['fingerprint'] == fingerprint
        assert core.store.read('stream_rehearsal_output', identifier)['text'] == ''
        assert core.store.read('turn', 'safe-turn') == safe
        with pytest.raises(ValueError):
            interaction.audio(output['id'], 'owner', output['generation'], output['output_epoch'])
        assert interaction.next_output('owner')['output'] is None
        assert stream.next_output(session, 'owner')['output'] is None
        assert audio.render(identifier, 'owner')['status'] != 'generated'
        assert len(calls) == 1
    finally:
        audio.close(); interaction.close(); stream.close(); core.close()


def test_forget_during_actual_chunk_render_withholds_late_pcm_and_never_regenerates(tmp_path):
    core, stream, clock, contexts, source, session, safe = prepared(tmp_path)
    entered, release = Event(), Event()
    def synth(*_):
        entered.set(); assert release.wait(5)
        return wav(), 'audio/wav'
    interaction = PrivateInteraction(core, clock=clock, synthesize=synth)
    audio = ChannelAudio(interaction)
    results = []
    thread = None
    try:
        audio.connect('owner'); audio.enable('owner', True)
        result = stream.submit(event(session, clock))
        thread = Thread(target=lambda: results.append(audio.render(result['output_id'], 'owner')))
        thread.start(); assert entered.wait(5)
        core.personal_control('remove', 'forget', {'target_id': source})
        release.set(); thread.join(5)
        assert not thread.is_alive() and results[0]['status'] == 'cancelled'
        assert list(interaction.root.glob('*.wav')) == []
        assert interaction.next_output('owner')['output'] is None
        assert stream.next_output(session, 'owner')['output'] is None
        with core.store.transaction(core.token) as db:
            assert core.store.list(db, 'interaction_output', None) == []
        assert audio.render(result['output_id'], 'owner')['status'] != 'generated'
    finally:
        release.set()
        if thread: thread.join(5)
        audio.close(); interaction.close(); stream.close(); core.close()


def test_authenticated_two_app_source_forget_removes_ready_text_and_audio_without_late_offer(tmp_path):
    def draft(_, raw):
        context = json.loads(raw)
        return AssistantResponse(display_text=context['personal']['identity']['fields'].get('description', 'Safe reply.'),
                                 delivery_mode='text_only')
    app = build_app(tmp_path, bearer_token='test-owner', draft_factory=lambda: draft, run_background=False,
        runner_factory=lambda store: PracticeRunner(store, propose=lambda *_: {}, check=lambda *_: {}),
        interaction_factory=lambda core: PrivateInteraction(core, synthesize=lambda *_: (wav(), 'audio/wav')))
    with TestClient(app) as backend:
        def transport(request):
            response = backend.request(request.method, str(request.url), headers=request.headers, content=request.content)
            return httpx.Response(response.status_code, content=response.content, headers={'content-type': 'application/json'})
        with patch.object(settings, 'api_auth_enabled', True), patch.object(settings, 'api_bearer_token', 'test-owner'):
            bridge = ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(transport))
        with (patch.object(settings, 'dashboard_auth_enabled', True), patch.object(settings, 'dashboard_auth_username', 'owner'),
              patch.object(settings, 'dashboard_auth_password', 'test-password'), patch.object(settings, 'dashboard_session_secret', 'test-session'),
              TestClient(dashboard_app(bridge)) as client):
            client.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
            prefix = '/api/coordination'
            def personal(request_id, action, values):
                response = client.post(prefix + '/personal', json={'audience': 'owner_private', 'request_id': request_id,
                    'action': action, 'values': values})
                assert response.status_code == 200, response.text
                return response.json()
            personal('identity', 'identity', {'fields': {'description': SECRET}})
            source = app.state.core.personal_state()['identity']['field_sources']['description']
            session = client.post(prefix + '/stream/start', json={'audience': 'owner_private', 'request_id': 'start',
                'format': 'coding', 'broad_plan': 'Review a disposable parser.'}).json()['id']
            consumer = {'audience': 'owner_private', 'consumer_id': 'http-owner'}
            scope = {**consumer, 'session_id': session}
            assert client.post(prefix + '/stream/connect', json=scope).status_code == 200
            assert client.post(prefix + '/channel-audio/connect', json=consumer).status_code == 200
            assert client.post(prefix + '/channel-audio/enable', json={**consumer, 'enabled': True}).status_code == 200
            result = client.post(prefix + '/stream/event', json={**scope, 'event_id': 'question', 'kind': 'chat',
                'source': 'replay', 'observed_at': time(), 'text': 'What do you think?', 'actor_alias': 'FixtureViewer'}).json()
            assert result['status'] == 'ready'
            assert client.post(prefix + '/channel-audio/render', json={**consumer, 'stream_output_id': result['output_id']}).status_code == 200
            output = client.get(prefix + '/interaction/poll', params=consumer).json()['output']
            params = {**consumer, 'generation': output['generation'], 'output_epoch': output['output_epoch']}
            assert client.get(prefix + '/interaction/audio/' + output['id'], params=params).content == wav()
            personal('forget-source', 'forget', {'target_id': source})
            assert app.state.core.store.read('stream_rehearsal_output', result['output_id'])['text'] == ''
            assert not (app.state.interaction.root / (output['id'] + '.wav')).exists()
            assert client.get(prefix + '/stream/next', params=scope).json()['output'] is None
            assert client.get(prefix + '/interaction/poll', params=consumer).json()['output'] is None
            assert client.get(prefix + '/interaction/audio/' + output['id'], params=params).status_code == 400


def test_forgotten_stream_contribution_cannot_reenter_future_core_recent_context(tmp_path):
    core, stream, clock, contexts, source, session, safe = prepared(tmp_path)
    try:
        stream.submit(event(session, clock))
        stream.ack(ack(stream.next_output(session, 'owner')['output']))
        core.personal_control('remove', 'forget', {'target_id': source})
        clock.advance(9)
        stream.heartbeat(session, 'owner')
        result = stream.submit(event(session, clock, 'next-question'))
        assert result['status'] == 'ready'
        assert SECRET not in json.dumps(contexts[-1])
        assert contexts[-1]['channel_event']['recent_received_contributions'] == []
        assert SECRET not in json.dumps(stream.control('new-return', session, 'owner_return'))
    finally:
        stream.close(); core.close()
