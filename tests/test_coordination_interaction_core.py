"""P5 observations share the actual private owner and commit boundary."""
import json
from threading import Event, Thread

import pytest

from gamma.coordination.core import Core
from gamma.coordination.store import Store
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def make(root, draft):
    store = Store(root)
    return Core(store, draft=draft, runner=PracticeRunner(store, propose=lambda *_:{}, check=lambda *_:{}))


def observation(**changes):
    return dict(audience='owner_private', consumer_id='test-consumer', channel='private_voice',
                speaker='owner', address_cue='none', confidence=.8, capability_speech_allowed=True, **changes)


def test_voice_uses_same_identity_context_and_finalized_speech(tmp_path):
    contexts=[]
    def draft(text, context):
        contexts.append(json.loads(context))
        return AssistantResponse(display_text='I disagree with that plan.', delivery_mode='text_only')
    core=make(tmp_path, draft)
    try:
        identity=core.personal_state()['identity_id']
        value=core.input('What do you think?', 'voice', 'one', observation=observation())
        response=value['result']['assistant_response']
        assert response['speech_text'] == response['display_text'] == 'I disagree with that plan.'
        assert response['delivery_mode'] == 'speech'
        assert contexts[0]['interaction']['address_cue'] == 'none'
        assert core.personal_state()['identity_id'] == identity
        text=core.input('Still there?', 'text', 'two')['result']['assistant_response']
        assert text['speech_text'] == '' and text['delivery_mode'] == 'text_only'
        assert 'interaction' not in contexts[1]
    finally:
        core.close()


@pytest.mark.parametrize('speaker', ['other','unknown'])
def test_unbound_speaker_never_reaches_private_context(tmp_path,speaker):
    core=make(tmp_path,lambda *_:pytest.fail('unbound speaker received private context'))
    try:
        obs=observation();obs['speaker']=speaker
        with pytest.raises(ValueError,match='bound owner'):
            core.input('Tell me private details','voice','unbound',observation=obs)
        assert core.store.read('turn','unbound') is None
    finally:
        core.close()


def test_request_id_binds_channel_and_owner_source(tmp_path):
    core=make(tmp_path,lambda *_:AssistantResponse(display_text='Here.',delivery_mode='text_only'))
    try:
        core.input('Hello','voice','once',observation=observation())
        with pytest.raises(ValueError,match='request id reused'):
            core.input('Hello','voice','once')
        changed=observation();changed['address_cue']='name'
        with pytest.raises(ValueError,match='request id reused'):
            core.input('Hello','voice','once',observation=changed)
    finally:
        core.close()


def test_silent_and_superseded_voice_cannot_become_speech(tmp_path):
    entered=Event(); release=Event()
    def draft(*_):
        entered.set();assert release.wait(5)
        return AssistantResponse(display_text='Late answer.',delivery_mode='text_only')
    core=make(tmp_path,draft); result=[]
    thread=Thread(target=lambda:result.append(core.input('Hello','voice','slow',observation=observation())))
    try:
        thread.start(); assert entered.wait(5)
        core.control('sleep','sleep');release.set();thread.join(5)
        assert not thread.is_alive()
        response=result[0]['result']['assistant_response']
        assert response['reason_code']=='superseded' and response['speech_text']==''
        assert core.store.read('turn','slow')['status']=='queued'
    finally:
        release.set();thread.join(5);core.close()


def test_sleep_queue_retains_observation_but_never_automatically_plays(tmp_path):
    contexts=[]
    core=make(tmp_path,lambda text,ctx:(contexts.append(json.loads(ctx)) or AssistantResponse(
        delivery_mode='silent',response_action='stay_silent',reason_code='thinking_aloud')))
    try:
        core.control('sleep','sleep')
        result=core.input('Maybe this one, hmm.','voice','queued',observation=observation())
        assert result['result']['assistant_response']['reason_code']=='asleep'
        core.control('wake','wake');core._drain_queue()
        turn=core.store.read('turn','queued')
        assert turn['status']=='completed' and turn['interaction']['channel']=='private_voice'
        assert turn['response']['speech_text']==''
        assert contexts[0]['interaction']['confidence']==.8
    finally:
        core.close()


def test_partial_chunk_receipts_cannot_mean_the_whole_answer_was_heard():
    first={'chunk_index':0,'chunk_count':2,'status':'played'}
    second={'chunk_index':1,'chunk_count':2,'status':'cancelled'}
    assert Core._delivery_summary([first,second])=='partially_consumer_reported_played'
    assert Core._delivery_summary([first])=='partially_consumer_reported_played'
    second['status']='played'
    assert Core._delivery_summary([first,second])=='all_chunks_consumer_reported_played'
