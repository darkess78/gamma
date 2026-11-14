"""P5 delivery tests use the actual Core and transactional source/owner fences."""
from concurrent.futures import ThreadPoolExecutor
import io
import threading
import wave

import pytest

from gamma.coordination.audio_policy import likely_playback_echo, speech_chunks
from gamma.coordination.interaction import PrivateInteraction, validate_wave
from gamma.coordination.interaction_models import InteractionObservation
from gamma.coordination.store import OwnershipLost
from gamma.schemas.response import AssistantResponse
from test_coordination_core import make_core


def wav(seconds=.1, *, rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(channels); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(b'\0\0' * int(seconds * rate * channels))
    return output.getvalue()


def observation(request='one', **changes):
    value = dict(audience='owner_private', consumer_id='desktop', request_id=request,
        session_id='private-presence', channel='private_voice', speaker='owner', text='Hello Shana')
    value.update(changes)
    return InteractionObservation(**value)


def setup(root, **changes):
    core = make_core(root, draft=changes.pop('draft', lambda *_: AssistantResponse(
        display_text='The saved result is ready.', delivery_mode='text_only')),
        clock=changes.get('clock', __import__('time').time))
    service = PrivateInteraction(core, synthesize=changes.pop('synthesize', lambda *_:(wav(), 'audio/wav')), **changes)
    service.connect('desktop')
    service.control('voice_enabled', True)
    service.control('speech_enabled', True)
    return core, service


def get_output(service):
    value = service.next_output('desktop')
    assert value['output'] is not None
    return value['output']


def ack(service, output, status, **changes):
    values = {key: output[key] for key in ('consumer_id','generation','output_epoch')}
    values.update(output_id=output['id'], status=status, **changes)
    return service.acknowledge(**values)


def test_generated_offered_attempted_played_are_separate_and_retry_does_not_synthesize(tmp_path):
    calls=[]
    core, service=setup(tmp_path,synthesize=lambda text,emotion:(calls.append((text,emotion)) or (wav(),'audio/wav')))
    try:
        result=service.observe(observation())
        assert result['status']=='generated' and len(calls)==1
        assert service.status('desktop')['outputs'][0]['status']=='generated'
        output=get_output(service)
        assert output['status']=='offered' and output['speech_text']=='The saved result is ready.'
        raw,mime=service.audio(output['id'],'desktop',output['generation'],output['output_epoch'])
        assert raw==wav() and mime=='audio/wav'
        assert service.status('desktop')['outputs'][0]['status']=='offered'
        with pytest.raises(ValueError,match='previously started'):
            ack(service,output,'played')
        assert ack(service,output,'started')['status']=='started'
        assert ack(service,output,'played')['status']=='played'
        assert service.next_output('desktop')['output'] is None
        assert service.observe(observation())==result and len(calls)==1
    finally:service.close();core.close()


def test_text_transport_receipt_is_delivered_without_claiming_playback(tmp_path):
    core,service=setup(tmp_path)
    try:
        service.observe(observation(channel='discord_dm'))
        output=get_output(service)
        assert not output['audio_available'] and not output['speech_text']
        assert ack(service,output,'attempted')['status']=='attempted'
        assert service.next_output('desktop')['output'] is None
        assert ack(service,output,'delivered',receipt_id='message_123')['receipt_id']=='message_123'
        assert service.status('desktop')['outputs'][0]['status']=='delivered'
    finally:service.close();core.close()


def test_audio_and_output_are_bound_to_consumer_generation_and_source_sequence(tmp_path):
    core,service=setup(tmp_path)
    try:
        service.connect('another')
        service.observe(observation())
        output=get_output(service)
        with pytest.raises(ValueError,match='another consumer'):
            service.audio(output['id'],'another',output['generation'],output['output_epoch'])
        with pytest.raises(ValueError,match='stale'):
            service.audio(output['id'],'desktop',output['generation']+1,output['output_epoch'])
        core.input('New current turn.','monitor','newer')
        assert service.next_output('desktop')['output'] is None
        with pytest.raises(ValueError,match='stale'):
            service.audio(output['id'],'desktop',output['generation'],output['output_epoch'])
    finally:service.close();core.close()


@pytest.mark.parametrize('speaker',['other','unknown'])
def test_uncertain_speaker_never_gets_private_context_or_stt(tmp_path,speaker):
    core,service=setup(tmp_path,draft=lambda *_:pytest.fail('private context leaked'),
                       transcribe=lambda _:pytest.fail('unadmitted source transcribed'))
    try:
        value=service.submit_audio(observation(speaker=speaker),wav())
        assert value['reason']=='unverified_speaker' and value['output_id'] is None
        assert core.store.read('turn','one') is None
    finally:service.close();core.close()


def test_voice_disable_is_distinct_from_sleep_and_background_work(tmp_path):
    core,service=setup(tmp_path,transcribe=lambda _:pytest.fail('disabled voice transcribed'))
    try:
        service.control('voice_enabled',False)
        assert service.submit_audio(observation(),wav())['reason']=='voice_disabled'
        assert core.status()['awake'] is True
        assert service.observe(observation('text',channel='desktop_text'))['status']=='generated'
        assert not get_output(service)['audio_available']
    finally:service.close();core.close()


def test_active_echo_is_ignored_but_additions_interrupt_and_enter_same_core(tmp_path):
    seen=[]
    core,service=setup(tmp_path,draft=lambda text,_:(seen.append(text) or AssistantResponse(
        display_text='The saved result is ready.',delivery_mode='text_only')))
    try:
        service.observe(observation())
        first=get_output(service);ack(service,first,'started')
        echo=service.observe(observation('echo',text='The saved result is ready!'))
        assert echo['reason']=='likely_playback_echo' and len(seen)==1
        assert not service.interrupt('desktop','barge_in','The saved result is ready')['interrupted']
        addition=service.observe(observation('barge',text='The saved result is ready but please stop.'))
        assert addition['status']=='generated' and len(seen)==2
        assert core.store.read('interaction_output',first['id'])['status']=='cancelled'
        later=get_output(service)
        assert later['output_epoch']>first['output_epoch']
    finally:service.close();core.close()


def test_reconnect_expires_unconfirmed_output_and_never_replays(tmp_path):
    core,service=setup(tmp_path)
    try:
        service.observe(observation());old=get_output(service)
        service.disconnect('desktop');service.connect('desktop')
        assert service.next_output('desktop')['output'] is None
        with pytest.raises(ValueError,match='stale'):
            ack(service,old,'started')
    finally:service.close();core.close()


def test_interrupt_during_model_prevents_accepted_turn_and_audio(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def draft(*_):
        entered.set();assert release.wait(5)
        return AssistantResponse(display_text='Late text.',delivery_mode='text_only')
    core,service=setup(tmp_path,draft=draft,synthesize=lambda *_:pytest.fail('stale synthesis'))
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation())
            assert entered.wait(5)
            assert service.observe(observation('busy'))['status']=='busy'
            service.interrupt('desktop');release.set()
            assert future.result(5)['status']=='cancelled'
        assert core.store.read('turn','one')['status']=='interrupted'
        assert service.next_output('desktop')['output'] is None
    finally:release.set();service.close();core.close()


def test_interrupt_during_tts_discards_bytes_without_releasing_output(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def synthesize(*_):
        entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,service=setup(tmp_path,synthesize=synthesize)
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation())
            assert entered.wait(5)
            service.interrupt('desktop');release.set()
            assert future.result(5)['status']=='cancelled'
        assert not list(service.root.glob('*.wav'))
    finally:release.set();service.close();core.close()


def test_forget_revokes_playback_and_cached_response_even_after_delivery(tmp_path):
    core,service=setup(tmp_path)
    try:
        service.observe(observation());output=get_output(service)
        ack(service,output,'started');ack(service,output,'played')
        with core.store.transaction(core.token) as db:
            turn=core.store.get(db,'turn','one');turn['redacted']=True
            core.store.put(db,'turn','one',turn)
        assert service.next_output('desktop')['output'] is None
        redacted=service.status('desktop')['outputs'][0]
        assert redacted['display_text']==redacted['speech_text']==''
        assert not list(service.root.glob('*.wav'))
        replay=service.observe(observation())
        assert replay=={'ok':True,'status':'expired','reason':'source_removed','output_id':None}
        with pytest.raises(ValueError,match='stale'):
            ack(service,output,'played')
    finally:service.close();core.close()


def test_forgotten_silent_response_cache_is_removed_without_output_row(tmp_path):
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(delivery_mode='silent',response_action='stay_silent'))
    try:
        assert service.observe(observation())['status']=='silent'
        with core.store.transaction(core.token) as db:
            turn=core.store.get(db,'turn','one');turn['redacted']=True
            core.store.put(db,'turn','one',turn)
        assert service.observe(observation())['reason']=='source_removed'
        assert core.store.read('interaction_request','one')['receipt']['output_id'] is None
    finally:service.close();core.close()


def test_forgotten_source_during_tts_is_not_released(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def synthesize(*_):
        entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,service=setup(tmp_path,synthesize=synthesize)
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation());assert entered.wait(5)
            with core.store.transaction(core.token) as db:
                turn=core.store.get(db,'turn','one');turn['redacted']=True
                core.store.put(db,'turn','one',turn)
            release.set();assert future.result(5)['status']=='cancelled'
        assert service.next_output('desktop')['output'] is None
        assert service.observe(observation())['reason']=='source_removed'
        assert not list(service.root.glob('*.wav'))
    finally:release.set();service.close();core.close()


def test_output_expiry_scrubs_text_and_audio_and_receipt_without_retry(tmp_path):
    now=[1000.]
    core,service=setup(tmp_path,clock=lambda:now[0])
    try:
        service.observe(observation());output=get_output(service)
        assert list(service.root.glob('*.wav'))
        now[0]+=61
        service.connect('desktop')
        assert service.next_output('desktop')['output'] is None
        cached=service.status('desktop')['outputs'][0]
        assert cached['display_text']==cached['speech_text']=='' and not cached['audio_available']
        assert not list(service.root.glob('*.wav'))
        assert service.observe(observation())['reason']=='receipt_expired_no_replay'
    finally:service.close();core.close()


def test_provider_failure_has_no_automatic_retry(tmp_path):
    calls=[]
    def broken(*_):
        calls.append(1);raise TimeoutError('test provider unavailable')
    core,service=setup(tmp_path,transcribe=broken)
    try:
        with pytest.raises(TimeoutError):service.submit_audio(observation(),wav())
        receipt=service.submit_audio(observation(),wav())
        assert receipt['reason']=='provider_or_turn_failed_no_replay' and len(calls)==1
        assert core.store.read('turn','one') is None
    finally:service.close();core.close()


def test_close_during_tts_never_publishes_late_bytes(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def synthesize(*_):
        entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,service=setup(tmp_path,synthesize=synthesize)
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation());assert entered.wait(5)
            service.close();release.set()
            assert future.result(5)['status']=='cancelled'
        assert not list(service.root.glob('*.wav'))
    finally:release.set();core.close()


def test_pcm_capture_limits_and_echo_token_boundaries():
    assert validate_wave(wav())==.1
    for raw in (b'',b'bad'*80,wav(12.1),wav()[:-20]):
        with pytest.raises(ValueError):validate_wave(raw)
    assert likely_playback_echo('The saved result is ready','The saved result is ready.')
    assert not likely_playback_echo('stop','Please stop talking.')
    assert not likely_playback_echo('saved ready result','The saved result is ready.')
    assert not likely_playback_echo('The saved result is ready but no','The saved result is ready.')


def test_speech_chunking_preserves_every_word_and_bounds_the_first_chunk():
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    assert len(speech_chunks(text))==2
    long=' '.join('word'+str(i) for i in range(100))
    chunks=speech_chunks(long)
    assert len(chunks)<=4 and len(chunks[0])<=160
    assert ' '.join(chunks)==long
    assert ' '.join(speech_chunks('  First line.\n Another line follows. '))=='First line. Another line follows.'
    with pytest.raises(ValueError,match='text_limit'):speech_chunks('word '*401)


def test_first_chunk_is_released_while_second_synthesis_remains_in_flight(tmp_path):
    entered,release=threading.Event(),threading.Event();calls=[]
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    def synthesize(chunk,*_):
        calls.append(chunk)
        if len(calls)==2:
            entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only'),synthesize=synthesize)
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation());assert entered.wait(5)
            assert not future.done()
            first=get_output(service)
            assert first['chunk_index']==0 and first['chunk_count']==2 and not first['final_chunk']
            assert first['utterance_id']=='one' and first['display_text']==text
            assert first['first_audio_ready_ms'] is not None
            ack(service,first,'started');ack(service,first,'played')
            release.set();receipt=future.result(5)
        second=get_output(service)
        assert second['chunk_index']==1 and second['final_chunk']
        assert second['display_text']==second['speech_text']==calls[1]
        assert receipt['output_id']==first['id'] and receipt['output_ids']==[first['id'],second['id']]
        assert core.store.read('interaction_output',first['id'])['status']=='played'
    finally:release.set();service.close();core.close()


def test_stop_during_later_chunk_preserves_played_receipt_and_never_releases_future_audio(tmp_path):
    entered,release=threading.Event(),threading.Event();calls=[]
    text='First complete sentence carries the useful point. Second complete sentence adds detail. Third sentence remains unspoken.'
    def synthesize(chunk,*_):
        calls.append(chunk)
        if len(calls)==2:
            entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only'),synthesize=synthesize)
    try:
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(service.observe,observation());assert entered.wait(5)
            first=get_output(service);ack(service,first,'started');ack(service,first,'played')
            service.interrupt('desktop');release.set()
            receipt=future.result(5)
        assert receipt['status']=='cancelled' and receipt['output_ids']==[first['id']]
        assert len(calls)==2
        assert core.store.read('interaction_output',first['id'])['status']=='played'
        assert service.next_output('desktop')['output'] is None
        assert not list(service.root.glob('*.wav'))
    finally:release.set();service.close();core.close()


def test_new_audio_is_transcribed_during_old_tts_and_invalidates_remaining_chunks(tmp_path):
    entered,release,transcribed=threading.Event(),threading.Event(),threading.Event();calls=[]
    first_text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    def synthesize(chunk,*_):
        calls.append(chunk)
        if len(calls)==2:
            entered.set();assert release.wait(5)
        return wav(),'audio/wav'
    def transcribe(_):
        transcribed.set();return 'Shana, stop that and answer my new question.'
    def draft(text,_):
        return AssistantResponse(display_text=first_text if text=='Hello Shana' else 'I heard the new question.',delivery_mode='text_only')
    core,service=setup(tmp_path,draft=draft,synthesize=synthesize,transcribe=transcribe)
    try:
        with ThreadPoolExecutor(2) as pool:
            old=pool.submit(service.observe,observation());assert entered.wait(5)
            first=get_output(service);ack(service,first,'started')
            newer=pool.submit(service.submit_audio,observation('newer',text=''),wav())
            assert transcribed.wait(5)
            # Wait for the actual Core to accept the new owner utterance while
            # the older TTS provider is still blocked, without another model owner.
            deadline=__import__('time').monotonic()+4
            while __import__('time').monotonic()<deadline:
                turn=core.store.read('turn','newer')
                if turn and turn['status']=='completed':break
                __import__('time').sleep(.01)
            assert turn['status']=='completed' and not old.done()
            assert core.store.read('interaction_output',first['id'])['status']=='cancelled'
            assert len(calls)==2
            assert service.observe(observation('overflow'))['status']=='busy'
            release.set()
            assert old.result(5)['status']=='cancelled'
            assert newer.result(5)['status']=='generated'
        assert get_output(service)['utterance_id']=='newer'
    finally:release.set();service.close();core.close()


def test_whole_utterance_duration_limit_does_not_erase_an_earlier_chunk(tmp_path):
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only'),
                       synthesize=lambda *_:(wav(50),'audio/wav'))
    try:
        receipt=service.observe(observation())
        assert receipt['status']=='partial' and len(receipt['output_ids'])==1
        first=get_output(service)
        assert first['chunk_index']==0 and first['audio_duration_seconds']==50
        assert first['chunk_count']==2 and not first['final_chunk']
    finally:service.close();core.close()


def test_overlong_speech_remains_visible_without_truncating_or_synthesizing(tmp_path):
    text='word '*401
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only'),
                       synthesize=lambda *_:pytest.fail('overlong speech should not be synthesized'))
    try:
        receipt=service.observe(observation())
        assert receipt['speech_limit_reason']=='private_speech_text_limit'
        output=get_output(service)
        assert output['display_text']==text and not output['audio_available'] and output['speech_text']==''
    finally:service.close();core.close()


def test_whole_utterance_bytes_are_bounded_across_individually_valid_chunks(tmp_path):
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    core,service=setup(tmp_path,draft=lambda *_:AssistantResponse(display_text=text,delivery_mode='text_only'),
        synthesize=lambda *_:(wav(20,rate=48000,channels=2),'audio/wav'))
    try:
        receipt=service.observe(observation())
        assert receipt['status']=='partial' and len(receipt['output_ids'])==1
        output=get_output(service)
        raw,_=service.audio(output['id'],'desktop',output['generation'],output['output_epoch'])
        assert len(raw)<4_000_000
    finally:service.close();core.close()


@pytest.mark.parametrize('extra_words', ['', ' But wait, I have a different question.'])
def test_playback_at_audio_intake_still_filters_echo_after_played_ack_during_stt(tmp_path,extra_words):
    seen=[];outputs=[]
    def transcribe(_):
        ack(service,outputs[0],'played')
        return 'The saved result is ready.'+extra_words
    core,service=setup(tmp_path,transcribe=transcribe,draft=lambda text,_:(seen.append(text) or
        AssistantResponse(display_text='The saved result is ready.',delivery_mode='text_only')))
    try:
        service.observe(observation());outputs.append(get_output(service));ack(service,outputs[0],'started')
        value=service.submit_audio(observation('captured',text=''),wav())
        if extra_words:
            assert value['status']=='generated' and len(seen)==2
        else:
            assert value['status']=='ignored' and value['reason']=='likely_playback_echo'
            assert len(seen)==1 and core.store.read('turn','captured') is None
        assert core.store.read('interaction_output',outputs[0]['id'])['status']=='played'
    finally:service.close();core.close()


def test_playback_source_removed_during_stt_cancels_intake_without_readmitting_echo(tmp_path):
    seen=[]
    def transcribe(_):
        with core.store.transaction(core.token) as db:
            turn=core.store.get(db,'turn','one');turn['redacted']=True
            core.store.put(db,'turn','one',turn)
        return 'The saved result is ready.'
    core,service=setup(tmp_path,transcribe=transcribe,draft=lambda text,_:(seen.append(text) or
        AssistantResponse(display_text='The saved result is ready.',delivery_mode='text_only')))
    try:
        service.observe(observation());output=get_output(service);ack(service,output,'started')
        value=service.submit_audio(observation('captured',text=''),wav())
        assert value['status']=='cancelled' and value['reason']=='playback_source_changed_during_transcription'
        assert len(seen)==1 and core.store.read('turn','captured') is None
        assert service.status('desktop')['outputs'][0]['speech_text']==''
    finally:service.close();core.close()


@pytest.mark.parametrize('ownership_lost',[False,True])
def test_actual_http_reports_partial_generation_without_erasing_played_prefix(tmp_path,ownership_lost):
    from fastapi.testclient import TestClient
    from gamma.coordination.app import build_app
    from gamma.coding.durable_practice import PracticeRunner
    text='First complete sentence carries the useful point. Second complete sentence adds a little detail.'
    calls=[]
    def synthesize(chunk,*_):
        calls.append(chunk)
        if len(calls)==2:
            service=app.state.interaction;first=service.next_output('desktop')['output']
            ack(service,first,'started');ack(service,first,'played')
            if ownership_lost:
                service.store.release(service.core.token)
                raise OwnershipLost('old owner must not return a partial receipt')
            raise TimeoutError('a deliberately private provider detail must not appear in response')
        return wav(),'audio/wav'
    app=build_app(tmp_path,bearer_token='test-owner',draft_factory=lambda:lambda *_:AssistantResponse(
        display_text=text,delivery_mode='text_only'),run_background=False,
        runner_factory=lambda store:PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}),
        interaction_factory=lambda core:PrivateInteraction(core,synthesize=synthesize))
    with TestClient(app,raise_server_exceptions=False) as client:
        client.headers['Authorization']='Bearer test-owner';base='/v1/coordination/interaction'
        owner={'audience':'owner_private','request_id':'connect','consumer_id':'desktop'}
        client.post(base+'/connect',json=owner)
        for action in ('voice_enabled','speech_enabled'):
            client.post(base+'/control',json={**owner,'request_id':action,'action':action,'value':True})
        response=client.post(base+'/input',json={**owner,'request_id':'partial','text':'Hello Shana',
            'observation':{'channel':'private_voice','speaker':'owner'}})
        if ownership_lost:
            assert response.status_code==409,response.text
            assert response.json()=={'detail':'coordinator ownership changed; result withheld'}
            return
        assert response.status_code==200,response.text
        receipt=response.json()
        assert receipt['ok'] is False and receipt['status']=='partial'
        assert receipt['reason']=='provider_or_turn_failed_no_replay' and len(receipt['output_ids'])==1
        assert 'private provider detail' not in response.text
        first=app.state.core.store.read('interaction_output',receipt['output_ids'][0])
        assert first['status']=='played' and first['chunk_index']==0 and first['chunk_count']==2
