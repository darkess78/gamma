"""Real configured-app conversation wrappers cancel unsent background reviews."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest

from gamma.config import settings
from gamma.coordination.app import configured_app
from gamma.coordination.store import OwnershipLost
import gamma.llm.backend_admission as admission
import gamma.llm.local_adapter as local_adapter
import gamma.safety.llm_reviewer as reviewer_module


@pytest.fixture
def actual_app(monkeypatch, tmp_path):
    values = {'llm_provider':'local', 'local_llm_endpoint':'http://127.0.0.1:11434',
        'local_llm_model':'qwen3.8:27b', 'local_llm_enable_routing':False,
        'llm_router_enabled':False, 'local_llm_keep_alive':'0',
        'speech_filter_llm_enabled':True, 'speech_filter_llm_model':'gemma4:e4b',
        'stt_provider':'stub', 'tts_provider':'stub', 'tts_profile':'',
        'local_llm_supports_vision':False,
        'database_url':'sqlite:///' + str(tmp_path/'auxiliary.sqlite3')}
    for name,value in values.items():
        monkeypatch.setattr(settings,name,value)
    monkeypatch.setenv('GAMMA_PRACTICE_MODEL','qwen3.8:27b')
    monkeypatch.setattr(admission,'_coordination_root',lambda:tmp_path/'admission')
    app=configured_app(Path(__file__).parents[1],tmp_path/'fresh',bearer_token='test-bearer',
        run_background=False,inference_profile={'name':'gamma_warm_local_v1','review_model':'gemma4:e4b'})
    with TestClient(app):
        yield app


def delayed_backend(monkeypatch, blocked_endpoint):
    real_slot=admission.backend_slot
    waiting=threading.Event();calls=[];priorities=[]
    @contextmanager
    def observed(endpoint,**kwargs):
        priorities.append((endpoint,kwargs['foreground']))
        if endpoint==blocked_endpoint:
            waiting.set()
        with real_slot(endpoint,**kwargs):
            yield
    def urlopen(request,**kwargs):
        data=json.loads(request.data);model=data['model'];calls.append(model)
        content=({'action':'allow','reason':'ordinary','confidence':1.0}
                 if model=='gemma4:e4b' else
                 {'action':'reply','requested_delivery':'text_only','final_text':'A brief private comment.',
                  'emotion':'neutral','state_updates':{'activity_action':'wait'},'reason_code':'channel_comment'})
        return BytesIO(json.dumps({'response':json.dumps(content)}).encode())
    monkeypatch.setattr(local_adapter,'backend_slot',observed)
    monkeypatch.setattr(reviewer_module,'backend_slot',observed)
    monkeypatch.setattr(local_adapter.request,'urlopen',urlopen)
    return real_slot,waiting,calls,priorities


@pytest.mark.parametrize('change',['sleep','owner_sequence','record_expiry','audience','shared_source'])
def test_stale_channel_cancels_before_primary_http_through_real_service(actual_app,monkeypatch,change):
    core=actual_app.state.core
    context={'channel':'shared_activity','audience':'owner_private','summary':'A selected interface is visible.'}
    if change=='shared_source':
        with core.store.transaction(core.token) as db:
            context['shared_activity']=actual_app.state.shared_activity.context(db)
    original=json.loads(json.dumps(context))
    real_slot,waiting,calls,priorities=delayed_backend(monkeypatch,'http://127.0.0.1:11434')
    pool=ThreadPoolExecutor(max_workers=1)
    try:
        with real_slot('http://127.0.0.1:11434',foreground=True,timeout=5):
            result=pool.submit(core.review_channel,context,'queued-background')
            assert waiting.wait(5),'Review did not reach the actual held backend slot'
            if change=='sleep':
                core.control('owner-sleep','sleep')
            elif change=='owner_sequence':
                core.control('owner-focus','focus',focus='focused')
            elif change=='record_expiry':
                with core.store.transaction(core.token) as db:
                    record=core.store.get(db,'channel_review','queued-background');record['expires_at']=0
                    core.store.put(db,'channel_review','queued-background',record)
            elif change=='audience':
                context['audience']='public_stream'
            else:
                actual_app.state.shared_activity.control('changed-source-mode','mode',{'mode':'focused'})
            completed=bool(wait([result],timeout=1).done)
        response=result.result(5)
        assert completed,'Stale background work waited for the occupied model instead of cancelling'
        assert calls==[],'Stale background work submitted a real HTTP model request'
        assert priorities[0][1] is False
        assert response.response_action=='defer' and response.reason_code=='channel_context_changed'
        assert core.store.read('channel_review','queued-background')['status']=='stale'
        core.control('owner-wake','wake')
        assert core.review_channel(original,'queued-background').reason_code=='channel_review_not_replayed'
    finally:
        pool.shutdown(wait=True)


def test_stale_channel_cancels_before_independent_review_http_through_real_service(actual_app,monkeypatch):
    core=actual_app.state.core
    context={'channel':'shared_activity','audience':'owner_private','summary':'A selected interface is visible.'}
    real_slot,waiting,calls,priorities=delayed_backend(monkeypatch,'http://127.0.0.1:11435')
    pool=ThreadPoolExecutor(max_workers=1)
    try:
        with real_slot('http://127.0.0.1:11435',foreground=True,timeout=5):
            result=pool.submit(core.review_channel,context,'queued-safety-review')
            assert waiting.wait(5)
            core.control('sleep-before-review','sleep')
            completed=bool(wait([result],timeout=1).done)
        response=result.result(5)
        assert completed,'Stale safety review was not cancelled through ConversationService'
        assert calls==['qwen3.8:27b'],'A stale independent safety review was submitted'
        assert all(priority is False for _,priority in priorities)
        assert response.reason_code=='channel_context_changed'
        assert core.store.read('channel_review','queued-safety-review')['status']=='stale'
    finally:
        pool.shutdown(wait=True)


def test_lost_owner_is_unwrapped_without_stale_record_write(actual_app,monkeypatch):
    core=actual_app.state.core
    context={'channel':'shared_activity','audience':'owner_private','summary':'A selected interface is visible.'}
    real_slot,waiting,calls,_=delayed_backend(monkeypatch,'http://127.0.0.1:11434')
    pool=ThreadPoolExecutor(max_workers=1)
    try:
        with real_slot('http://127.0.0.1:11434',foreground=True,timeout=5):
            result=pool.submit(core.review_channel,context,'old-owner-review')
            assert waiting.wait(5)
            core.store.claim(core.token[0])
            completed=bool(wait([result],timeout=1).done)
        with pytest.raises(OwnershipLost):
            result.result(5)
        assert completed and calls==[]
        assert core.store.read('channel_review','old-owner-review')['status']=='pending'
    finally:
        pool.shutdown(wait=True)


@pytest.mark.parametrize('foreground', [True, False])
def test_source_bound_caller_priority_and_checkpoint_reach_actual_generation_and_review(
        actual_app, monkeypatch, foreground):
    core = actual_app.state.core
    _, _, calls, priorities = delayed_backend(monkeypatch, None)
    checked = []
    context = {'channel': 'shared_activity', 'audience': 'owner_private',
               'summary': 'A source-bound manual capture or watch observation.'}
    with local_adapter.local_inference_admission(
            foreground=foreground, checkpoint=lambda: checked.append(True)):
        response = core.review_channel(context, 'source-bound-review')
    assert response.response_action == 'reply'
    assert calls == ['qwen3.8:27b', 'gemma4:e4b']
    assert [priority for _, priority in priorities] == [foreground, foreground]
    assert len(checked) >= 4


@pytest.mark.parametrize('withdrawal', ['source', 'ownership'])
def test_manual_source_checkpoint_cancels_held_actual_generation_without_http(
        actual_app, monkeypatch, withdrawal):
    from gamma.coordination.shared_moment_policy import MomentInterrupted

    core = actual_app.state.core
    endpoint = 'http://127.0.0.1:11434'
    real_slot, waiting, calls, priorities = delayed_backend(monkeypatch, endpoint)
    withdrawn = threading.Event()
    error = (MomentInterrupted('selected_source_revoked') if withdrawal == 'source'
             else OwnershipLost('source_owner_changed'))

    def checkpoint():
        if withdrawn.is_set():
            raise error

    def review():
        with local_adapter.local_inference_admission(foreground=True, checkpoint=checkpoint):
            return core.review_channel(
                {'channel': 'shared_activity', 'audience': 'owner_private',
                 'summary': 'A manual capture whose selected source can be withdrawn.'},
                'manual-source-withdrawn')

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with real_slot(endpoint, foreground=False, timeout=5):
            result = pool.submit(review)
            assert waiting.wait(5)
            withdrawn.set()
            completed = bool(wait([result], timeout=1).done)
        with pytest.raises(type(error)) as raised:
            result.result(5)
        assert raised.value is error
        assert completed, 'The inherited source fence did not cancel queued generation'
        assert calls == []
        assert priorities == [(endpoint, True)]
    finally:
        pool.shutdown(wait=True)
