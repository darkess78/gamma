"""Actual configured Core draft -> ConversationService -> safety admission."""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Event, Thread
import time

import pytest

from gamma.coordination import app as coordination_app
from gamma.coordination.shared_vision import local_image_analyzer
from gamma.coordination.turn_adapter import PrivateTurnAdapter
from gamma.llm import local_adapter as local
from gamma.llm.backend_admission import backend_slot
from gamma.safety import llm_reviewer as safety
from gamma.safety.speech_filter import SpeechSafetyFilter
from gamma.schemas.conversation import RecipientContext
from gamma.schemas.response import AssistantResponse
from test_conversation_recipient import fixture_state
from test_shared_activity import setup
from test_shared_moments import moments, metadata, IMAGE
from test_shared_watch import enable


def configured_draft(conversation):
    # Compile the actual app closure unchanged, without constructing a second
    # production ConversationService/ContinuityService or starting real hosts.
    tree = ast.parse(Path(coordination_app.__file__).read_text())
    draft = next(node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name=='draft')
    namespace = {**vars(coordination_app), 'conversation':conversation, 'RecipientContext':RecipientContext,
                 'runtime_label':'owner-private isolated candidate'}
    exec(compile(ast.Module(body=[draft],type_ignores=[]),'<actual-configured-channel-draft>','exec'),namespace)
    return namespace['draft']


class Reply:
    def __init__(self, content): self.content=content
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def read(self): return json.dumps({'response':json.dumps(self.content)}).encode()


def install(moments, fixture_state, monkeypatch, *, safety_slot=None):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    calls, slots = [], []
    monkeypatch.setattr(local.settings,'local_llm_supports_vision',True)
    monkeypatch.setattr(local.settings,'speech_filter_llm_enabled',True)
    monkeypatch.setattr(local.settings,'speech_filter_llm_model','controlled-safety')
    monkeypatch.setattr(local.settings,'local_llm_model','controlled-primary')
    monkeypatch.setattr(local,'probe_ollama_model_capabilities',lambda **_: {'ok':True,'supports_vision':True})
    def http(req, **_):
        payload=json.loads(req.data)
        kind='vision' if payload.get('images') else 'safety' if payload['model']=='controlled-safety' else 'semantic'
        calls.append(kind)
        if kind=='vision': return Reply({'summary':'An authored historical application frame.','confidence':.5})
        if kind=='safety': return Reply({'action':'allow','reason':'controlled safe reply','confidence':.9})
        return Reply({'action':'reply','requested_delivery':'text_only','final_text':'A safe reflection on that captured moment.',
            'emotion':'neutral','state_updates':{'activity_action':'wait'},'reason_code':'authored'})
    monkeypatch.setattr(local.request,'urlopen',http)
    @contextmanager
    def observed(endpoint, **kwargs):
        slots.append(kwargs)
        if kwargs.get('checkpoint'): kwargs['checkpoint']()
        yield
    monkeypatch.setattr(local,'backend_slot',observed)
    monkeypatch.setattr(safety,'backend_slot',safety_slot or observed)
    image=local_image_analyzer()
    activity.analyze_image,service.background_image_analyzer=image,image.background
    conversation=fixture_state.service
    conversation._llm=PrivateTurnAdapter()
    conversation._speech_filter=SpeechSafetyFilter(local.settings.speech_filter_level)
    core.draft=configured_draft(conversation)
    return watch,row,calls,slots


def test_actual_configured_conversation_and_safety_all_inherit_watch_background(moments,fixture_state,monkeypatch):
    core,activity,service,now,_=moments
    watch,row,calls,slots=install(moments,fixture_state,monkeypatch)
    result=watch.share(row['id'],row['lease'],'three_calls',metadata(activity,now),IMAGE,'image/png')['moment']
    assert result['status']=='generated' and result['text']=='A safe reflection on that captured moment.'
    assert calls==['vision','semantic','safety']
    assert len(slots)==3 and all(slot['foreground'] is False and slot.get('checkpoint') for slot in slots)
    watch.stop(row['id'],row['lease']); now[0]+=10
    manual=service.share('manual_three',metadata(activity,now,1),IMAGE,'image/png')
    assert manual['status']=='generated' and [slot['foreground'] for slot in slots[-3:]]==[True,True,True]
    assert core.store.read('turn','three_calls') is None


@pytest.mark.parametrize('withdrawal',['stop','foreground'])
def test_actual_safety_queue_checks_stop_or_pending_foreground_before_extra_http(moments,fixture_state,monkeypatch,tmp_path,withdrawal):
    from gamma.llm import backend_admission as admission
    core,activity,service,now,_=moments
    monkeypatch.setattr(admission,'_coordination_root',lambda:tmp_path/'admission')
    endpoint='http://controlled-safety-queue.invalid:12969'
    monkeypatch.setattr(local.settings,'local_llm_endpoint',endpoint)
    queued,release_foreground,foreground_entered=Event(),Event(),Event()
    @contextmanager
    def queued_safety(endpoint,**kwargs):
        assert kwargs['foreground'] is False and kwargs.get('checkpoint')
        queued.set()
        with backend_slot(endpoint,**kwargs):yield
    watch,row,calls,_=install(moments,fixture_state,monkeypatch,safety_slot=queued_safety)
    outputs,failures=[],[]
    def share():
        try:outputs.append(watch.share(row['id'],row['lease'],'safety_pending',metadata(activity,now),IMAGE,'image/png'))
        except BaseException as exc:failures.append(exc)
    with backend_slot(endpoint,foreground=False,timeout=5):
        thread=Thread(target=share);thread.start();assert queued.wait(5)
        foreground=None
        if withdrawal=='stop':watch.stop(row['id'],row['lease'])
        else:
            def draft(*_):
                foreground_entered.set();assert release_foreground.wait(5)
                return AssistantResponse(display_text='A real pending foreground question.')
            core.draft=draft
            foreground=Thread(target=lambda:core.input('Authored foreground while safety queues.','controlled','foreground_safety'))
            foreground.start();assert foreground_entered.wait(5)
        try:
            thread.join(3)
            assert not thread.is_alive() and failures==[]
            assert outputs[0]['moment']['status']=='cancelled' and not outputs[0]['moment']['text']
            assert calls==['vision','semantic']  # No safety HTTP after queued cancellation.
            assert activity.status()['state']['enabled']
        finally:
            release_foreground.set();thread.join(5)
            if foreground:foreground.join(5)


def test_safety_default_and_scoped_checkpoint_identity_are_preserved(monkeypatch):
    monkeypatch.setattr(local.settings,'speech_filter_llm_enabled',True)
    monkeypatch.setattr(local.settings,'speech_filter_llm_model','controlled-safety')
    calls=[]
    @contextmanager
    def observed(endpoint,**kwargs):
        calls.append(kwargs)
        if kwargs.get('checkpoint'):kwargs['checkpoint']()
        yield
    monkeypatch.setattr(safety,'backend_slot',observed)
    monkeypatch.setattr(safety.request,'urlopen',lambda *_a,**_k:Reply({'action':'allow','reason':'controlled','confidence':.9}))
    reviewer=safety.SpeechLLMReviewer()
    assert reviewer.review('A safe ordinary sentence.').action=='allow'
    assert calls[-1]['foreground'] is True and 'checkpoint' not in calls[-1]
    sentinel=RuntimeError('Known admission cancellation')
    def checkpoint():raise sentinel
    with local.local_inference_admission(foreground=False,checkpoint=checkpoint):
        with pytest.raises(RuntimeError) as raised:reviewer.review('Safe but withdrawn.')
    assert raised.value is sentinel
