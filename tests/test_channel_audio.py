"""Actual Core -> canonical stream decision -> existing P5 chunk/receipt path."""
import io
from threading import Event, Thread
import wave

import pytest

from gamma.coding.durable_practice import PracticeRunner
from gamma.coordination.channel_audio import ChannelAudio, invalidate_snapshot
from gamma.coordination.core import Core
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.store import OwnershipLost, Store
from gamma.coordination.stream_session import StreamSession
from gamma.schemas.response import AssistantResponse


TEXT='I would test the boundary first. A clean input can hide the actual problem.'


class Clock:
    def __init__(self):self.now=1000.0
    def __call__(self):return self.now


def wav():
    out=io.BytesIO()
    with wave.open(out,'wb') as handle:
        handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(8000);handle.writeframes(b'\0\0'*80)
    return out.getvalue()


def make(root, synthesize=None):
    clock=Clock();store=Store(root);calls=[]
    core=Core(store,draft=lambda *_:AssistantResponse(display_text=TEXT,delivery_mode='text_only'),
        runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),clock=clock)
    stream=StreamSession(core,clock=clock)
    def synth(text,emotion):calls.append(text);return wav(),'audio/wav'
    interaction=PrivateInteraction(core,synthesize=synthesize or synth,clock=clock)
    audio=ChannelAudio(interaction)
    session=stream.start('start',{'audience':'owner_private','format':'coding','broad_plan':'Review parser tests.',
                                 'duration_seconds':300})
    stream.connect(session['id'],'listener');audio.connect('listener')
    result=stream.submit({'audience':'owner_private','session_id':session['id'],'consumer_id':'listener',
        'event_id':'question','kind':'chat','source':'replay','observed_at':clock(),
        'text':'Which parser test should we try?','actor_alias':'ExampleViewer'})
    assert result['status']=='ready'
    return core,stream,interaction,audio,clock,session['id'],result['output_id'],calls


def cleanup(core,stream,interaction,audio):
    audio.close();interaction.close();stream.close();core.close()


def receive(interaction):
    output=interaction.next_output('listener')['output'];assert output
    raw,mime=interaction.audio(output['id'],'listener',output['generation'],output['output_epoch'])
    assert mime=='audio/wav' and raw==wav()
    interaction.acknowledge('listener',output['id'],output['generation'],output['output_epoch'],'started')
    interaction.acknowledge('listener',output['id'],output['generation'],output['output_epoch'],'played')
    return output


def test_checked_stream_audio_uses_actual_core_and_p5_without_fake_owner_turn(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        before=core.store.read('interaction_meta','state')
        identity=core.personal_state()['identity_id']
        with pytest.raises(ValueError,match='not_enabled'):audio.render(source,'listener')
        audio.enable('listener',True)
        receipt=audio.render(source,'listener')
        assert receipt['status']=='generated' and len(receipt['output_ids'])==2
        outputs=[receive(interaction),receive(interaction)]
        assert [o['chunk_index']for o in outputs]==[0,1]
        assert ' '.join(calls)==TEXT
        assert all(o['channel']=='private_stream' for o in outputs)
        assert interaction.next_output('listener')['output'] is None
        request_id='channel-audio-'+source.rsplit('-',1)[-1]
        origin=core.store.read('channel_audio_request',request_id)['origin']
        review=core.store.read('channel_review',origin['review_id'])
        assert review['status']=='completed' and review['response']['display_text']==TEXT
        assert core.store.read('stream_rehearsal_output',source)['audio_request_id']==request_id
        assert core.store.read('turn',request_id) is None
        with core.store.transaction(core.token) as db:assert core.store.list(db,'turn',None)==[]
        assert core.personal_state()['identity_id']==identity
        assert core.store.read('interaction_meta','state')==before
        assert audio.render(source,'listener')['status']=='generated' and len(calls)==2
        assert interaction.next_output('listener')['output'] is None
    finally:cleanup(core,stream,interaction,audio)


def test_channel_listen_control_never_changes_global_mute_or_foreground_sequence(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        before=core.store.read('interaction_meta','state')
        sequence=core.store.read('brain','state')['sequence']
        audio.enable('listener',True);audio.interrupt('listener');audio.enable('listener',False)
        assert core.store.read('interaction_meta','state')==before
        assert core.store.read('brain','state')['sequence']==sequence
        assert audio.status('listener')['listen_enabled'] is False
    finally:cleanup(core,stream,interaction,audio)


@pytest.mark.parametrize('change',['stop','stream_disconnect','private_disconnect','listen_off','interrupt','sleep','foreground','forget',
                                  'global_mute','private_interrupt','stream_reconnect','private_reconnect','lease_expiry'])
def test_late_provider_bytes_are_withheld_after_actual_source_or_owner_change(tmp_path,change):
    entered,release=Event(),Event()
    def synth(*_):entered.set();assert release.wait(5);return wav(),'audio/wav'
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path,synth)
    result=[];audio.enable('listener',True)
    thread=Thread(target=lambda:result.append(audio.render(source,'listener')))
    try:
        thread.start();assert entered.wait(5)
        if change=='stop':stream.control('stop',session,'stop')
        elif change=='stream_disconnect':stream.disconnect(session,'listener')
        elif change=='private_disconnect':interaction.disconnect('listener')
        elif change=='listen_off':audio.enable('listener',False)
        elif change=='interrupt':audio.interrupt('listener')
        elif change=='global_mute':interaction.control('speech_enabled',False)
        elif change=='private_interrupt':interaction.interrupt('listener')
        elif change=='stream_reconnect':stream.connect(session,'listener')
        elif change=='private_reconnect':interaction.connect('listener')
        elif change=='lease_expiry':clock.now+=46
        elif change=='sleep':core.control('sleep','sleep')
        elif change=='foreground':core.input('A foreground message.','owner-private','foreground')
        elif change=='forget':
            with core.store.transaction(core.token) as db:
                sid=core.personal.administrative_source(db,'source',text='Private source to erase.')
            core.personal_control('forget','forget',{'target_id':sid})
        release.set();thread.join(5)
        assert not thread.is_alive() and result[0]['status']=='cancelled'
        assert not list(interaction.root.glob('*.wav'))
        with core.store.transaction(core.token) as db:
            assert not [o for o in core.store.list(db,'interaction_output',None)if o['audio_available']]
    finally:
        release.set();thread.join(5);cleanup(core,stream,interaction,audio)


@pytest.mark.parametrize('target',['review_text','review_hash','review_status','event_fingerprint',
                                  'event_output','session_revision','stream_lease','consent_epoch'])
def test_poll_fetch_and_ack_recheck_canonical_source_not_a_trust_callback(tmp_path,target):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        audio.enable('listener',True);audio.render(source,'listener')
        output=interaction.next_output('listener')['output'];assert output
        request=core.store.read('channel_audio_request','channel-audio-'+source.rsplit('-',1)[-1])
        origin=request['origin']
        with core.store.transaction(core.token) as db:
            if target.startswith('review_'):
                row=core.store.get(db,'channel_review',origin['review_id'])
                if target=='review_text':row['response']['display_text']='Other text.'
                elif target=='review_hash':row['context_sha256']='0'*64
                else:row.update(status='redacted',response=None)
                core.store.put(db,'channel_review',origin['review_id'],row)
            elif target.startswith('event_'):
                row=core.store.get(db,'stream_rehearsal_event',origin['event_id'])
                if target=='event_fingerprint':row['fingerprint']='0'*64
                else:row['result']['output_id']='stream-output-'+'0'*32
                core.store.put(db,'stream_rehearsal_event',origin['event_id'],row)
            elif target in {'session_revision','stream_lease'}:
                row=core.store.get(db,'stream_session',session)
                if target=='session_revision':row['revision']+=1
                else:row['consumer']['lease']='0'*32
                core.store.put(db,'stream_session',session,row)
            else:
                row=core.store.get(db,'channel_audio_consent','listener');row['epoch']+=1
                core.store.put(db,'channel_audio_consent','listener',row)
        with pytest.raises(ValueError):interaction.audio(output['id'],'listener',output['generation'],output['output_epoch'])
        with pytest.raises(ValueError):interaction.acknowledge('listener',output['id'],output['generation'],output['output_epoch'],'started')
        assert interaction.next_output('listener')['output'] is None
        assert not list(interaction.root.glob('*.wav'))
        row=core.store.read('interaction_output',output['id'])
        assert row['display_text']==row['speech_text']==''
    finally:cleanup(core,stream,interaction,audio)


def test_first_chunk_is_available_while_later_synthesis_waits_and_stop_fences_it(tmp_path):
    later,release=Event(),Event();count=0
    def synth(*_):
        nonlocal count
        count+=1
        if count==2:later.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path,synth)
    result=[];audio.enable('listener',True)
    thread=Thread(target=lambda:result.append(audio.render(source,'listener')))
    try:
        thread.start();assert later.wait(5)
        first=receive(interaction)
        assert first['chunk_index']==0 and first['chunk_count']==2
        audio.interrupt('listener');release.set();thread.join(5)
        assert result[0]['status']=='cancelled'
        assert interaction.next_output('listener')['output'] is None
        assert not list(interaction.root.glob('*.wav'))
    finally:
        release.set();thread.join(5);cleanup(core,stream,interaction,audio)


def test_background_audio_yields_to_existing_synthesis_and_request_capacity(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        audio.enable('listener',True)
        interaction._synthesis.acquire()
        try:assert audio.render(source,'listener')['reason']=='foreground_synthesis_has_priority'
        finally:interaction._synthesis.release()
        interaction._requests.acquire();interaction._requests.acquire()
        try:assert audio.render(source,'listener')['reason']=='private_interaction_capacity'
        finally:interaction._requests.release();interaction._requests.release()
        assert calls==[]
        assert core.store.read('channel_audio_request','channel-audio-'+source.rsplit('-',1)[-1]) is None
        assert audio.render(source,'listener')['status']=='generated'
    finally:cleanup(core,stream,interaction,audio)


def test_only_one_pending_audio_utterance_can_occupy_a_private_consumer(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        audio.enable('listener',True);audio.render(source,'listener')
        displayed=stream.next_output(session,'listener')['output']
        stream.ack({'audience':'owner_private','session_id':session,'consumer_id':'listener',
            'output_id':displayed['id'],'generation':displayed['generation'],'revision':displayed['revision'],
            'status':'delivered','receipt_id':'displayed'})
        clock.now+=9
        next_source=stream.submit({'audience':'owner_private','session_id':session,'consumer_id':'listener',
            'event_id':'next-question','kind':'chat','source':'replay','observed_at':clock(),
            'text':'What about another boundary case?','actor_alias':'ExampleViewer'})['output_id']
        assert next_source
        assert audio.render(next_source,'listener')['reason']=='private_audio_pending'
        assert len(calls)==2
        receive(interaction);receive(interaction)
        assert audio.render(next_source,'listener')['status']=='generated'
    finally:cleanup(core,stream,interaction,audio)


def test_partial_provider_failure_retains_played_prefix_but_never_replays_audio(tmp_path):
    state={};calls=[]
    def synth(text,*_):
        calls.append(text)
        if len(calls)==2:
            state['first']=receive(state['interaction'])
            raise TimeoutError('A private provider exception must not be returned.')
        return wav(),'audio/wav'
    core,stream,interaction,audio,clock,session,source,_=make(tmp_path,synth)
    state['interaction']=interaction
    try:
        audio.enable('listener',True)
        result=audio.render(source,'listener')
        assert result['status']=='partial' and result['reason']=='provider_failed_no_replay'
        assert 'private provider' not in str(result)
        first=core.store.read('interaction_output',state['first']['id'])
        assert first['status']=='played' and first['audio_available'] is False
        assert interaction.next_output('listener')['output'] is None
        assert audio.render(source,'listener')['status']=='expired' and len(calls)==2
        assert not list(interaction.root.glob('*.wav'))
    finally:cleanup(core,stream,interaction,audio)


def test_snapshot_cancels_audio_origin_and_disables_consent_but_retains_fingerprint(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        audio.enable('listener',True);audio.render(source,'listener')
        request_id='channel-audio-'+source.rsplit('-',1)[-1]
        fingerprint=core.store.read('channel_audio_request',request_id)['fingerprint']
        with core.store.transaction(core.token) as db:invalidate_snapshot(db)
        assert core.store.read('channel_audio_request',request_id)['fingerprint']==fingerprint
        assert core.store.read('channel_audio_request',request_id)['status']=='cancelled'
        assert audio.status('listener')['listen_enabled'] is False
        assert interaction.next_output('listener')['output'] is None
        assert not list(interaction.root.glob('*.wav'))
    finally:cleanup(core,stream,interaction,audio)


def test_forged_stream_text_and_other_consumer_never_reach_synthesis(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        audio.connect('other');audio.enable('other',True)
        with pytest.raises(ValueError):audio.render(source,'other')
        audio.enable('listener',True)
        with core.store.transaction(core.token) as db:
            output=core.store.get(db,'stream_rehearsal_output',source);output['text']='Injected speech.'
            core.store.put(db,'stream_rehearsal_output',source,output)
        with pytest.raises(ValueError):audio.render(source,'listener')
        assert calls==[]
    finally:cleanup(core,stream,interaction,audio)


def test_owner_loss_during_channel_synthesis_never_returns_partial_success(tmp_path):
    state={};count=0
    def synth(*_):
        nonlocal count
        count+=1
        if count==2:
            state['core'].store.release(state['core'].token)
            raise OwnershipLost('A stale owner cannot finalize partial speech.')
        return wav(),'audio/wav'
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path,synth)
    state['core']=core
    try:
        audio.enable('listener',True)
        with pytest.raises(OwnershipLost):audio.render(source,'listener')
        assert core.store.read('brain','state')['owner']==''
        with core.store.transaction() as db:
            outputs=core.store.list(db,'interaction_output',None)
        assert len(outputs)==1 and outputs[0]['chunk_index']==0
    finally:cleanup(core,stream,interaction,audio)


def mic_observation(identifier,text):
    return {'audience':'owner_private','request_id':identifier,'session_id':'owner-mic','consumer_id':'owner-mic',
            'channel':'private_voice','speaker':'owner','address_cue':'none','confidence':.9,'text':text}


def test_known_active_checked_audio_is_echo_for_another_private_owner_mic(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        interaction.control('voice_enabled',True)
        audio.enable('listener',True);audio.render(source,'listener')
        first=interaction.next_output('listener')['output']
        interaction.acknowledge('listener',first['id'],first['generation'],first['output_epoch'],'started')
        interaction.connect('owner-mic')
        result=interaction.observe(mic_observation('echo',first['speech_text']))
        assert result['status']=='ignored' and result['reason']=='likely_playback_echo'
        assert core.store.read('turn','echo') is None
    finally:cleanup(core,stream,interaction,audio)


def test_inactive_checked_text_is_not_a_permanent_cross_device_echo_rule(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        interaction.control('voice_enabled',True)
        audio.enable('listener',True);audio.render(source,'listener')
        first=receive(interaction);receive(interaction)
        interaction.connect('owner-mic')
        result=interaction.observe(mic_observation('intentional-repeat',first['speech_text']))
        assert result['status']=='generated'
        assert core.store.read('turn','intentional-repeat')['status']=='completed'
    finally:cleanup(core,stream,interaction,audio)


def test_removed_cross_consumer_playback_during_stt_is_not_readmitted_as_owner_text(tmp_path):
    core,stream,interaction,audio,clock,session,source,calls=make(tmp_path)
    try:
        interaction.control('voice_enabled',True)
        audio.enable('listener',True);audio.render(source,'listener')
        first=interaction.next_output('listener')['output']
        interaction.acknowledge('listener',first['id'],first['generation'],first['output_epoch'],'started')
        interaction.connect('owner-mic')
        def transcribe(_):audio.enable('listener',False);return first['speech_text']
        interaction.transcribe=transcribe
        result=interaction.submit_audio(mic_observation('removed-echo',''),wav())
        assert result['status']=='cancelled' and result['reason']=='playback_source_changed_during_transcription'
        assert core.store.read('turn','removed-echo') is None
    finally:cleanup(core,stream,interaction,audio)
