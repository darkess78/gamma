"""Authenticated dashboard proxy -> actual Core -> private rehearsal receipt."""
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gamma.coding.durable_practice import PracticeRunner
from gamma.config import settings
from gamma.coordination.app import build_app
from gamma.coordination.dashboard import build_app as dashboard_app
from gamma.dashboard.auth import session_cookie_value
from gamma.dashboard.shana_client import ShanaApiClient
from gamma.schemas.response import AssistantResponse


def test_authenticated_dashboard_stream_flow_and_no_public_output(tmp_path):
    drafts=[]
    def draft(text,context):
        drafts.append((text,context))
        return AssistantResponse(display_text='Try the boundary case before changing the parser.',delivery_mode='text_only')
    app=build_app(tmp_path,bearer_token='private-owner',draft_factory=lambda:draft,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),run_background=False)
    with TestClient(app) as backend:
        assert backend.get('/v1/coordination/stream/status?audience=owner_private').status_code==401
        paths=[]
        def transport(request):
            paths.append(request.url.path)
            response=backend.request(request.method,str(request.url),headers=request.headers,content=request.content)
            return httpx.Response(response.status_code,content=response.content,headers={'content-type':'application/json'})
        with patch.object(settings,'api_auth_enabled',True),patch.object(settings,'api_bearer_token','private-owner'):
            bridge=ShanaApiClient(base_url='http://isolated',transport=httpx.MockTransport(transport))
        with (patch.object(settings,'dashboard_auth_enabled',True),
              patch.object(settings,'dashboard_auth_username','owner'),
              patch.object(settings,'dashboard_auth_password','test-password'),
              patch.object(settings,'dashboard_session_secret','test-secret'),
              TestClient(dashboard_app(bridge)) as client):
            prefix='/api/coordination/stream'
            start={'audience':'owner_private','request_id':'start','format':'coding','broad_plan':'Review parser boundary tests.'}
            assert client.post(prefix+'/start',json=start).status_code==401
            assert paths==[]
            client.cookies.set('gamma_dashboard_session',session_cookie_value('owner'))
            for invalid in ({**start,'audience':'stream_public'},{**start,'publish':True}):
                assert client.post(prefix+'/start',json=invalid).status_code==422
            assert paths==[]
            response=client.post(prefix+'/start',json=start)
            assert response.status_code==200,response.text
            assert response.headers['cache-control']=='no-store'
            session=response.json()['id']
            consumer={'audience':'owner_private','session_id':session,'consumer_id':'review-browser'}
            assert client.post(prefix+'/connect',json=consumer).status_code==200
            from time import time
            event={**consumer,'event_id':'parser-question','kind':'chat','source':'private_adapter',
                   'observed_at':time(),'text':'Shana, what should we test next?','actor_alias':'viewer'}
            result=client.post(prefix+'/event',json=event)
            assert result.status_code==200,result.text
            assert result.json()['status']=='ready'
            output=client.get(prefix+'/next',params=consumer).json()['output']
            assert output['text']=='Try the boundary case before changing the parser.'
            assert output['publication_authorized'] is False
            receipt={**consumer,'output_id':output['id'],'generation':output['generation'],
                     'revision':output['revision'],'status':'delivered','receipt_id':'received'}
            assert client.post(prefix+'/ack',json=receipt).json()['status']=='delivered'
            assert client.get(prefix+'/next',params=consumer).json()['output'] is None
            assert client.get(prefix+'/status',params={'audience':'owner_private'}).json()['active']['received_count']==1
            assert len(drafts)==1
            assert set(paths)<= {'/v1/coordination/stream/'+name for name in
                                ('start','connect','event','next','ack','status')}
            assert client.post(prefix+'/control',json={'audience':'owner_private','request_id':'stop',
                'session_id':session,'action':'stop'}).status_code==200


def test_backend_stream_queries_and_controls_validate_scope(tmp_path):
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:lambda *_:AssistantResponse(
        delivery_mode='silent',response_action='stay_silent'),
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),run_background=False)
    with TestClient(app) as backend:
        headers={'authorization':'Bearer owner'}
        prefix='/v1/coordination/stream'
        assert backend.get(prefix+'/status',headers=headers).status_code==422
        assert backend.get(prefix+'/status?audience=stream_public',headers=headers).status_code==422
        assert backend.get(prefix+'/next',headers=headers,params={
            'audience':'owner_private','session_id':'../../private','consumer_id':'owner'}).status_code==422
        assert backend.post(prefix+'/start',headers=headers,json={'audience':'owner_private','request_id':'start',
            'format':'coding','broad_plan':'Test','duration_seconds':True}).status_code==422
