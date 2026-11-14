"""Authenticated two-app audio bridge and copied-state consent invalidation."""
from time import time
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.maintenance import Maintenance
from gamma.coordination.store import Store
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse
from test_channel_audio import TEXT, cleanup, make, wav


def test_private_checked_audio_proxy_fetch_and_played_receipts(tmp_path):
    calls=[]
    def synth(text,*_):calls.append(text);return wav(),'audio/wav'
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:lambda *_:AssistantResponse(
        display_text=TEXT,delivery_mode='text_only'),run_background=False,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),
        interaction_factory=lambda core:PrivateInteraction(core,synthesize=synth))
    with TestClient(app) as backend:
        def transport(request):
            response=backend.request(request.method,str(request.url),headers=request.headers,content=request.content)
            return httpx.Response(response.status_code,content=response.content,headers={'content-type':'application/json'})
        with patch.object(settings,'api_auth_enabled',True),patch.object(settings,'api_bearer_token','owner'):
            bridge=ShanaApiClient(base_url='http://isolated',transport=httpx.MockTransport(transport))
        with (patch.object(settings,'dashboard_auth_enabled',True),
              patch.object(settings,'dashboard_auth_username','owner'),
              patch.object(settings,'dashboard_auth_password','test-password'),
              patch.object(settings,'dashboard_session_secret','test-secret'),
              TestClient(dashboard_app(bridge)) as client):
            base='/api/coordination';consumer={'audience':'owner_private','consumer_id':'rehearsal-audio'}
            assert client.post(base+'/channel-audio/connect',json=consumer).status_code==401
            client.cookies.set('gamma_dashboard_session',session_cookie_value('owner'))
            session=client.post(base+'/stream/start',json={'audience':'owner_private','request_id':'start',
                'format':'coding','broad_plan':'Review parser boundaries.'}).json()['id']
            stream_consumer={**consumer,'session_id':session}
            assert client.post(base+'/stream/connect',json=stream_consumer).status_code==200
            connected=client.post(base+'/channel-audio/connect',json=consumer)
            assert connected.status_code==200 and not connected.json()['listen_enabled']
            event={**stream_consumer,'event_id':'question','kind':'chat','source':'replay','observed_at':time(),
                   'text':'What should we test?','actor_alias':'ExampleViewer'}
            source=client.post(base+'/stream/event',json=event).json()['output_id']
            payload={**consumer,'stream_output_id':source}
            assert client.post(base+'/channel-audio/render',json=payload).status_code==400
            assert calls==[]
            assert client.post(base+'/channel-audio/enable',json={**consumer,'enabled':True}).json()['listen_enabled']
            receipt=client.post(base+'/channel-audio/render',json=payload)
            assert receipt.status_code==200 and receipt.json()['status']=='generated',receipt.text
            for index in range(2):
                output=client.get(base+'/interaction/poll',params=consumer).json()['output']
                assert output['channel']=='private_stream' and output['chunk_index']==index
                params={**consumer,'generation':output['generation'],'output_epoch':output['output_epoch']}
                audio=client.get(base+'/interaction/audio/'+output['id'],params=params)
                assert audio.status_code==200 and audio.content==wav()
                for status in ('started','played'):
                    ack=client.post(base+'/interaction/ack',json={**params,'output_id':output['id'],'status':status})
                    assert ack.status_code==200 and ack.json()['status']==status
            assert ' '.join(calls)==TEXT
            assert client.get(base+'/interaction/poll',params=consumer).json()['output'] is None
            for invalid in ({**payload,'audience':'stream_public'},{**payload,'text':'Unreviewed injected speech.'}):
                assert client.post(base+'/channel-audio/render',json=invalid).status_code==422
            assert len(calls)==2
            global_status=client.get(base+'/interaction/status',params=consumer).json()
            assert not global_status['voice_enabled'] and not global_status['speech_enabled']
            with app.state.core.store.transaction(app.state.core.token) as db:
                assert app.state.core.store.list(db,'turn',None)==[]


def test_live_backup_invalidates_channel_audio_operational_rows_only_in_copy(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path/'live')
    try:
        audio.enable('listener',True);audio.render(source,'listener')
        request_id='channel-audio-'+source.rsplit('-',1)[-1]
        original=core.store.read('channel_audio_request',request_id)
        service=Maintenance(core);receipt=service.backup('with-channel-audio')
        copied=Store(service.root/receipt['id'])
        assert copied.read('channel_audio_consent','listener')['enabled'] is False
        assert copied.read('channel_audio_request',request_id)['status']=='cancelled'
        assert copied.read('channel_audio_request',request_id)['fingerprint']==original['fingerprint']
        assert core.store.read('channel_audio_request',request_id)==original
        assert audio.status('listener')['listen_enabled'] is True
    finally:cleanup(core,stream,interaction,audio)
