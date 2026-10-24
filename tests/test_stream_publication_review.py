"""Publication is observable only after actual enabled review concludes."""
import asyncio
import json
import threading
from urllib import request

import pytest
from test_conversation_recipient import fixture_state, FACT, EPISODE, PRIVATE_WORK
from gamma.config import settings
from gamma.integrations.discord.delivery import approved_reply
from gamma.llm.base import LLMReply
from gamma.performer.bus import PerformerEventBus
from gamma.safety.llm_reviewer import SpeechLLMReviewer
from gamma.schemas.response import ConversationTurnDraft
from gamma.stream.brain import StreamBrain
from gamma.stream import brain as brain_module
from gamma.stream.models import StreamActor, StreamInputEvent
from gamma.stream.output import StreamOutputDispatcher, PerformerBusOutputAdapter
from gamma.stream.trace import StreamTraceStore
from gamma.voice.tts import TTSResult

TEXT='The distinct publication fixture reply.'
TARGETS=['stream_public','dashboard_monitor','discord_call']


class Provider:
    def __init__(self,delivery,text=TEXT):self.delivery,self.text,self.prompts=delivery,text,[]
    def generate_reply(self,system_prompt,user_text,**kwargs):
        self.prompts.append(system_prompt)
        return LLMReply(text=ConversationTurnDraft(action='reply',requested_delivery=self.delivery,
            final_text=self.text,reason_code='synthetic').model_dump_json())


class Transport:
    def __init__(self,verdict):
        self.verdict=verdict;self.entered=threading.Event();self.release=threading.Event()
        self.finished=threading.Event();self.payloads=[]
    def open(self,req,**kwargs):
        self.payloads.append(json.loads(req.data));self.entered.set()
        try:
            assert self.release.wait(4),'Synthetic review transport not released'
            if self.verdict=='error':raise OSError('Synthetic transport failed')
            raw=json.dumps({'action':self.verdict if self.verdict!='timeout' else 'allow',
                'reason':'synthetic verdict','confidence':1})
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):return json.dumps({'response':raw}).encode()
            return Response()
        finally:self.finished.set()


class TTS:
    def __init__(self,path):self.path=path;self.calls=[];self.prepared=threading.Event()
    def synthesize(self,text,emotion=None,styles=None):
        self.calls.append(text);self.path.write_bytes(b'RIFFsynthetic')
        self.prepared.set()
        return TTSResult('synthetic',text,str(self.path),'audio/wav',{})


def setup(fixture_state,tmp_path,monkeypatch,target,delivery,verdict,*,disabled=None,text=TEXT,missing=False):
    service=fixture_state.service;service._llm=Provider(delivery,text)
    monkeypatch.setattr(settings,'speech_filter_llm_enabled',disabled!='setting')
    monkeypatch.setattr(settings,'speech_filter_llm_model','' if missing else 'synthetic-review')
    monkeypatch.setattr(settings,'local_llm_tagging_model','')
    monkeypatch.setattr(settings,'local_llm_light_model','')
    monkeypatch.setattr(settings,'stream_safety_review_timeout_seconds',.12 if verdict=='timeout' else .8)
    transport=Transport(verdict);monkeypatch.setattr(request,'urlopen',transport.open)
    tts=TTS(tmp_path/'synthetic.wav');service._tts=tts
    bus=PerformerEventBus(state_path=tmp_path/'bus.json')
    brain=StreamBrain(conversation=service,safety_reviewer=SpeechLLMReviewer(),speech_synthesizer=tts,
        output_dispatcher=StreamOutputDispatcher([PerformerBusOutputAdapter(bus)]),
        trace_store=StreamTraceStore(tmp_path/'trace.jsonl'))
    # Local actor keeps these target-isolation tests distinct from Discord's 20s deadline path.
    event=StreamInputEvent(kind='mic_transcript',text='Shana hello',session_id='mixed',
        actor=StreamActor(source='local',platform_id='fixture-actor'),metadata={'public_output':True,
        'output_target_policy':target,'twitch_controls':{'llm_safety_review_enabled':disabled!='event'}})
    return brain,bus,transport,tts,event


def drain(queue):
    items=[]
    while not queue.empty():items.append(queue.get_nowait())
    return items


@pytest.mark.parametrize('target',TARGETS)
@pytest.mark.parametrize('delivery',['speech','text_only'])
@pytest.mark.parametrize('fast_mode',[True,False])
@pytest.mark.parametrize('verdict',['allow','block','soften','error','timeout'])
def test_held_review_gates_live_replay_and_returned_approval(fixture_state,tmp_path,monkeypatch,
        target,delivery,fast_mode,verdict):
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,target,delivery,verdict)
    async def exercise():
        subscribers={t:await bus.subscribe(target_policy=t) for t in TARGETS}
        task=asyncio.create_task(asyncio.to_thread(brain.handle_event,event,synthesize_speech=True,fast_mode=fast_mode))
        try:
            assert await asyncio.to_thread(transport.entered.wait,2)
            if delivery=='speech':assert await asyncio.to_thread(tts.prepared.wait,2)
            await asyncio.sleep(.025)
            assert not task.done(),'Turn returned while required review remained held'
            assert all(queue.empty() for _,queue in subscribers.values())
            assert bus.recent(limit=100)==[]
            replay_id,replay=await bus.subscribe(target_policy=target,replay_recent=100)
            try:assert replay.empty()
            finally:bus.unsubscribe(replay_id)
            if verdict!='timeout':transport.release.set()
            result=await asyncio.wait_for(task,2)
            await asyncio.sleep(0)
            live={t:drain(q) for t,(_,q) in subscribers.items()}
            recent=[e.model_dump() for e in bus.recent(limit=100,target_policy=target)]
            replay_id,replay=await bus.subscribe(target_policy=target,replay_recent=100)
            try:assert drain(replay)==recent==live[target]
            finally:bus.unsubscribe(replay_id)
            assert approved_reply(result.model_dump())==(TEXT if verdict=='allow' else None)
            assert not result.safety_decision.get('review_pending')
            assert result.safety_decision.get('playback_approved') is (verdict=='allow')
            if verdict=='allow':
                assert result.assistant_response.display_text==TEXT
                if delivery=='speech':
                    assert sum(row['type']=='speech_started' for row in recent)==1
                    assert sum(row['type']=='speech_ended' for row in recent)==1
                    assert any(row['payload'].get('text')==TEXT for row in recent)
                    assert result.assistant_response.audio_path==str(tts.path)
                else:
                    assert [row['type'] for row in recent]==['expression_set']
                    assert TEXT not in json.dumps(recent) and tts.calls==[]
            else:
                assert TEXT not in result.model_dump_json()+json.dumps(recent)
                assert str(tts.path) not in result.model_dump_json()+json.dumps(recent)
                assert all('audio_url' not in row['payload'] for row in recent)
            if target!='stream_public':assert live['stream_public']==[]
            if target!='discord_call':assert live['discord_call']==[]
            assert all(row['target_policy']==target for row in recent)
            for prompt in fixture_state.service._llm.prompts:
                assert all(token not in prompt for token in [FACT,EPISODE,PRIVATE_WORK])
            transport.release.set();assert await asyncio.to_thread(transport.finished.wait,2)
            before=bus.recent(limit=100);await asyncio.sleep(.02)
            assert bus.recent(limit=100)==before and all(q.empty() for _,q in subscribers.values())
        finally:
            transport.release.set()
            if not task.done():await asyncio.wait_for(task,3)
            for subscriber,_ in subscribers.values():bus.unsubscribe(subscriber)
    asyncio.run(exercise())


@pytest.mark.parametrize('target',TARGETS)
@pytest.mark.parametrize('delivery',['speech','text_only'])
@pytest.mark.parametrize('fast_mode',[True,False])
def test_enabled_actual_reviewer_with_missing_model_never_approves(fixture_state,tmp_path,monkeypatch,target,delivery,fast_mode):
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,target,delivery,'allow',missing=True)
    result=brain.handle_event(event,synthesize_speech=True,fast_mode=fast_mode)
    assert transport.payloads==[]
    assert approved_reply(result.model_dump()) is None
    assert result.safety_decision.get('playback_approved') is False
    assert TEXT not in result.model_dump_json()+json.dumps([e.model_dump() for e in bus.recent(limit=100)])


@pytest.mark.parametrize('target',TARGETS)
@pytest.mark.parametrize('disabled',['setting','event'])
@pytest.mark.parametrize('delivery',['speech','text_only'])
@pytest.mark.parametrize('fast_mode',[True,False])
def test_disabled_optional_reviewer_preserves_privacy_guard(fixture_state,tmp_path,monkeypatch,target,disabled,delivery,fast_mode):
    private='Home address: 41 Synthetic Private Lane; private IP: 10.99.88.77.'
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,target,delivery,'allow',disabled=disabled,text=private)
    result=brain.handle_event(event,synthesize_speech=True,fast_mode=fast_mode)
    assert transport.payloads==[]
    exposed=result.model_dump_json()+json.dumps([e.model_dump() for e in bus.recent(limit=100)])
    assert '10.99.88.77' not in exposed and private not in tts.calls


def observe_cleanup(monkeypatch):
    calls=[];original=brain_module.ThreadPoolExecutor
    class ObservedExecutor(original):
        def shutdown(self,*args,**kwargs):
            calls.append(kwargs.copy());return super().shutdown(*args,**kwargs)
    monkeypatch.setattr(brain_module,'ThreadPoolExecutor',ObservedExecutor)
    return calls


def test_review_deadline_does_not_wait_for_held_tts_or_late_allow(fixture_state,tmp_path,monkeypatch):
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,'stream_public','speech','timeout')
    shutdown=observe_cleanup(monkeypatch)
    tts_entered,tts_release=threading.Event(),threading.Event();original=tts.synthesize
    def held_tts(*args):
        tts_entered.set();assert tts_release.wait(4);return original(*args)
    monkeypatch.setattr(tts,'synthesize',held_tts)
    async def exercise():
        task=asyncio.create_task(asyncio.to_thread(brain.handle_event,event,synthesize_speech=True))
        try:
            assert await asyncio.to_thread(transport.entered.wait,2)
            assert await asyncio.to_thread(tts_entered.wait,2)
            result=await asyncio.wait_for(task,1)
            assert result.safety_decision['review_timeout'] and approved_reply(result.model_dump()) is None
            assert not tts_release.is_set() and bus.recent(limit=100)==[]
            assert shutdown==[{'wait':False,'cancel_futures':True}]
            transport.release.set();tts_release.set()
            assert await asyncio.to_thread(transport.finished.wait,2)
            assert await asyncio.to_thread(tts.prepared.wait,2)
            await asyncio.sleep(.02)
            assert bus.recent(limit=100)==[]
        finally:
            transport.release.set();tts_release.set()
            if not task.done():await asyncio.wait_for(task,3)
    asyncio.run(exercise())


@pytest.mark.parametrize('interruption',[False,True])
def test_reviewer_failure_or_interruption_cleans_executor_without_publication(fixture_state,tmp_path,monkeypatch,interruption):
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,'stream_public','speech','allow')
    shutdown=observe_cleanup(monkeypatch)
    error=KeyboardInterrupt('synthetic cancellation') if interruption else RuntimeError('PRIVATE_EXCEPTION_CANARY')
    def fail(text):raise error
    monkeypatch.setattr(brain._safety_reviewer,'review',fail)
    if interruption:
        with pytest.raises(KeyboardInterrupt) as caught:brain.handle_event(event,synthesize_speech=True)
        assert caught.value is error
    else:
        result=brain.handle_event(event,synthesize_speech=True)
        assert approved_reply(result.model_dump()) is None and result.assistant_response is None
        assert 'PRIVATE_EXCEPTION_CANARY' not in result.model_dump_json()
    assert shutdown==[{'wait':False,'cancel_futures':True}]
    assert bus.recent(limit=100)==[] and transport.payloads==[]


def test_tts_failure_after_review_allow_does_not_publish(fixture_state,tmp_path,monkeypatch):
    brain,bus,transport,tts,event=setup(fixture_state,tmp_path,monkeypatch,'stream_public','speech','allow')
    shutdown=observe_cleanup(monkeypatch);transport.release.set()
    error=OSError('synthetic TTS failure')
    def fail(*args):raise error
    monkeypatch.setattr(tts,'synthesize',fail)
    with pytest.raises(OSError) as caught:brain.handle_event(event,synthesize_speech=True)
    assert caught.value is error and bus.recent(limit=100)==[]
    assert shutdown==[{'wait':False,'cancel_futures':True}]
