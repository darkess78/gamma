"""Actual private HTTP adapters, bounded audio, and strict channel admission."""
import base64
import io
import wave

from fastapi.testclient import TestClient

from gamma.coordination.app import build_app
from gamma.coordination.interaction import PrivateInteraction
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def wav():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(b'\0\0'*1600)
    return stream.getvalue()


def test_authenticated_http_voice_to_same_core_to_audio_and_actual_ack(tmp_path):
    drafts=[];synth=[]
    def draft(text,context):
        drafts.append(text)
        return AssistantResponse(display_text='Checked reply.',delivery_mode='text_only')
    app=build_app(tmp_path,bearer_token='test-owner',draft_factory=lambda:draft,run_background=False,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),
        interaction_factory=lambda core:PrivateInteraction(core,transcribe=lambda _: 'Hello Shana.',
            synthesize=lambda text,emotion:(synth.append((text,emotion)) or (wav(),'audio/wav'))))
    with TestClient(app) as client:
        base='/v1/coordination/interaction'
        owner={'audience':'owner_private','request_id':'connect','consumer_id':'desktop'}
        assert client.post(base+'/connect',json=owner).status_code==401
        client.headers['Authorization']='Bearer test-owner'
        assert client.post(base+'/connect',json={**owner,'audience':'public'}).status_code==422
        assert client.post(base+'/connect',json=owner).status_code==200
        for action in ('voice_enabled','speech_enabled'):
            assert client.post(base+'/control',json={**owner,'request_id':action,'action':action,'value':True}).status_code==200
        body={**owner,'request_id':'audio','audio_base64':base64.b64encode(wav()).decode(),
            'observation':{'channel':'private_voice','speaker':'owner','confidence':.6}}
        assert client.post(base+'/audio-input',json={**body,'capability_speech_allowed':True}).status_code==422
        assert client.post(base+'/audio-input',json={**body,'observation':{**body['observation'],'capability_speech_allowed':True}}).status_code==422
        reply=client.post(base+'/audio-input',json=body)
        assert reply.status_code==200,reply.text
        assert reply.json()['status']=='generated'
        assert drafts==['Hello Shana.'] and synth==[('Checked reply.','neutral')]
        poll=client.get(base+'/poll',params={'audience':'owner_private','consumer_id':'desktop'}).json()
        output=poll['output']
        assert output['display_text']=='Checked reply.' and output['audio_available']
        params={'audience':'owner_private','consumer_id':'desktop','generation':poll['generation'],'output_epoch':poll['output_epoch']}
        audio=client.get(base+'/audio/'+output['id'],params=params)
        assert base64.b64decode(audio.json()['audio_base64'])==wav()
        ack={**params,'output_id':output['id'],'status':'played'}
        assert client.post(base+'/ack',json=ack).status_code==400
        ack['status']='started'
        assert client.post(base+'/ack',json=ack).status_code==200
        ack['status']='played'
        assert client.post(base+'/ack',json=ack).json()['status']=='played'
        assert client.post(base+'/audio-input',json=body).json()==reply.json()
        assert len(drafts)==len(synth)==1
        client.post('/v1/coordination/control',json={'audience':'owner_private','request_id':'sleep','action':'sleep'})
        assert client.get(base+'/audio/'+output['id'],params=params).status_code==400
        assert client.get(base+'/poll',params={'audience':'owner_private','consumer_id':'desktop'}).json()['output'] is None


def test_unknown_source_and_malformed_audio_never_reach_private_draft(tmp_path):
    drafts=[]
    app=build_app(tmp_path,bearer_token='owner',draft_factory=lambda:lambda *_:drafts.append('called'),run_background=False,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer owner'
        base='/v1/coordination/interaction'
        owner={'audience':'owner_private','request_id':'connect','consumer_id':'desktop'}
        client.post(base+'/connect',json=owner)
        payload={**owner,'request_id':'other','text':'Tell me private notes.',
            'observation':{'channel':'desktop_text','speaker':'other'}}
        result=client.post(base+'/input',json=payload)
        assert result.status_code==200 and result.json()['reason']=='unverified_speaker'
        assert app.state.core.store.read('turn','other') is None and not drafts
        invalid={**owner,'request_id':'bad','audio_base64':'not a WAV',
            'observation':{'channel':'private_voice','speaker':'owner'}}
        assert client.post(base+'/audio-input',json=invalid).status_code==400
        assert not drafts
