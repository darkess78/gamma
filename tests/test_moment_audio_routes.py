"""Authenticated two-application moment receipt and checked speech HTTP path."""
import base64
from time import time
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.shared_activity import SharedActivities
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse
from test_channel_audio import wav
from test_shared_moments import IMAGE


def test_actual_typed_authenticated_moment_dom_receipt_to_wav_and_played(tmp_path):
    calls=[]
    def synth(text,*_): calls.append(text); return wav(),'audio/wav'
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:lambda *_:AssistantResponse(display_text='That past moment looked calm.'),
        run_background=False,runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),
        shared_activity_factory=lambda core:SharedActivities(core,analyze_image=lambda *_:{'summary':'An authored controlled image.','confidence':.6}),
        interaction_factory=lambda core:PrivateInteraction(core,synthesize=synth))
    with TestClient(app) as backend:
        def transport(request):
            response=backend.request(request.method,str(request.url),headers=request.headers,content=request.content)
            return httpx.Response(response.status_code,content=response.content,headers={'content-type':response.headers.get('content-type','application/json')})
        with patch.object(settings,'api_auth_enabled',True),patch.object(settings,'api_bearer_token','owner'):
            bridge=ShanaApiClient(base_url='http://isolated',transport=httpx.MockTransport(transport))
        with (patch.object(settings,'dashboard_auth_enabled',True),patch.object(settings,'dashboard_auth_username','owner'),
              patch.object(settings,'dashboard_auth_password','test-password'),patch.object(settings,'dashboard_session_secret','test-secret'),
              TestClient(dashboard_app(bridge)) as client):
            prefix='/api/coordination';consumer={'audience':'owner_private','consumer_id':'moments_http'}
            speech={**consumer,'moment_id':'http_moment'}
            assert client.post(prefix+'/channel-audio/moment/render',json=speech).status_code==401
            client.cookies.set('gamma_dashboard_session',session_cookie_value('owner'))
            page=client.get('/dashboard/activities'); assert page.status_code==200 and 'Listen to new moments' in page.text
            selected=client.post(prefix+'/shared-activity/control',json={'audience':'owner_private','request_id':'select','action':'select','values':{
                'source_id':'selected_window','label':'Authored private fixture','kind':'osrs_screen','seconds':180}}).json()
            result=client.post(prefix+'/shared-activity/moment',json={'audience':'owner_private','request_id':'http_moment',
                'metadata':{'source_id':'selected_window','source_epoch':selected['source_epoch'],'observation_id':'image_one','sequence':0,'captured_at':time(),'kind':'osrs_screen'},
                'image_base64':base64.b64encode(IMAGE).decode(),'media_type':'image/png'}).json()
            assert result['status']=='generated',result
            client.post(prefix+'/channel-audio/connect',json=consumer)
            client.post(prefix+'/channel-audio/enable',json={**consumer,'enabled':True})
            assert client.post(prefix+'/channel-audio/moment/render',json=speech).status_code==400
            for invalid in ({**speech,'text':'Caller supplied text'},{**speech,'audience':'stream_public'},{**speech,'request_id':'forged'}):
                assert client.post(prefix+'/channel-audio/moment/render',json=invalid).status_code==422
            ack=client.post(prefix+'/shared-activity/moment/received',json={**consumer,'request_id':'dom_http','moment_id':result['id'],'text_sha256':result['text_sha256']})
            assert ack.status_code==200 and ack.json()['audio_consumer_bound']
            rendered=client.post(prefix+'/channel-audio/moment/render',json=speech)
            assert rendered.status_code==200 and rendered.json()['status']=='generated',rendered.text
            output=client.get(prefix+'/interaction/poll',params=consumer).json()['output']
            assert output['channel']=='shared_moment'
            params={**consumer,'generation':output['generation'],'output_epoch':output['output_epoch']}
            raw=client.get(prefix+'/interaction/audio/'+output['id'],params=params)
            assert raw.status_code==200 and raw.content==wav()
            for status in ('started','played'):
                receipt=client.post(prefix+'/interaction/ack',json={**params,'output_id':output['id'],'status':status})
                assert receipt.status_code==200 and receipt.json()['status']==status
            assert calls==[result['text']]
