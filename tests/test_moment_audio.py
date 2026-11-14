"""Actual Core moments use the existing bounded checked-channel PCM transport."""
from threading import Event, Thread

import pytest

from gamma.coordination.channel_audio import ChannelAudio
from gamma.coordination.channel_audio_moment_source import request_id
from gamma.coordination.interaction import PrivateInteraction
from gamma.coordination.shared_watch import SharedWatch
from gamma.schemas.response import AssistantResponse
from test_channel_audio import wav, mic_observation
from test_shared_activity import setup, select
from test_shared_moments import moments, metadata, analyzer, IMAGE


@pytest.fixture
def spoken(moments):
    core, activity, service, now, mono = moments
    activity.moments = service
    SharedWatch(service)
    core.shared_activity = activity
    analyzer(activity)
    calls = []
    core.draft = lambda *_: AssistantResponse(display_text='That captured moment looked peaceful.')
    def synth(text, emotion):
        calls.append(text)
        return wav(), 'audio/wav'
    interaction = PrivateInteraction(core, synthesize=synth, clock=lambda: now[0])
    audio = ChannelAudio(interaction)
    audio.connect('listener'); audio.enable('listener', True)
    result = service.share('moment_one', metadata(activity, now), IMAGE, 'image/png')
    assert result['status'] == 'generated'
    yield core, activity, service, interaction, audio, now, mono, result, calls
    audio.close(); interaction.close()


def bind(spoken):
    _, _, service, _, _, _, _, result, _ = spoken
    return service.received('dom_receipt', result['id'], result['text_sha256'], 'listener')


def test_actual_core_moment_text_to_pcm_without_owner_turn_or_global_voice_flags(spoken):
    core, _, service, interaction, audio, _, _, result, calls = spoken
    with pytest.raises(ValueError, match='receipt_required'):
        audio.render_moment(result['id'], 'listener')
    assert calls == []
    bind(spoken)
    receipt = audio.render_moment(result['id'], 'listener')
    assert receipt['status'] == 'generated' and calls == [result['text']]
    output = interaction.next_output('listener')['output']
    assert output['channel'] == 'shared_moment' and output['speech_text'] == result['text']
    assert interaction.audio(output['id'], 'listener', output['generation'], output['output_epoch'])[0] == wav()
    for status in ('started', 'played'):
        interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], status)
    assert audio.render_moment(result['id'], 'listener')['status'] == 'generated' and len(calls) == 1
    with core.store.transaction(core.token) as db:
        assert core.store.list(db, 'turn', None) == []
        assert core.store.list(db, 'run', None) == []
    meta = core.store.read('interaction_meta', 'state')
    assert not meta['voice_enabled'] and not meta['speech_enabled']


def test_foreign_receipt_and_reconnect_cannot_authorize_speech(spoken):
    _, _, service, _, audio, _, _, result, calls = spoken
    bind(spoken)
    audio.connect('other'); audio.enable('other', True)
    with pytest.raises(ValueError, match='receipt_required'):
        audio.render_moment(result['id'], 'other')
    audio.connect('listener'); audio.enable('listener', True)
    with pytest.raises(ValueError, match='receipt_required'):
        audio.render_moment(result['id'], 'listener')
    assert calls == []


def test_monotonic_deadline_blocks_new_claim_even_with_old_dom_receipt(spoken):
    _, _, _, _, audio, _, mono, result, calls = spoken
    bind(spoken); mono[0] += 46
    with pytest.raises(ValueError, match='acceptance_closed'):
        audio.render_moment(result['id'], 'listener')
    assert calls == []


def test_public_lookup_after45_does_not_revoke_or_extend_accepted_audio(spoken):
    core, _, service, interaction, audio, now, mono, result, _ = spoken
    bind(spoken)
    def synth(*_):
        now[0] += 46; mono[0] += 46
        return wav(), 'audio/wav'
    interaction.synthesize = synth
    receipt = audio.render_moment(result['id'], 'listener')
    assert receipt['status'] == 'generated'
    grant = core.store.read('channel_audio_request', request_id(result['id']))
    for _ in range(3):
        closed = service.inspect(result['id'])
        assert closed['text'] == '' and closed['reason'] == 'moment_acceptance_closed'
    assert core.store.read('channel_audio_request', request_id(result['id'])) == grant
    assert core.store.read('channel_review', grant['origin']['review_id'])['status'] == 'completed'
    output = interaction.next_output('listener')['output']; assert output
    assert interaction.audio(output['id'], 'listener', output['generation'], output['output_epoch'])[0] == wav()
    interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], 'started')
    interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], 'played')


@pytest.mark.parametrize('change', ['stop', 'goal', 'mode', 'listen_off', 'interrupt', 'disconnect', 'reconnect', 'foreground', 'sleep', 'forget', 'capture_tamper', 'review_tamper'])
def test_actual_source_change_during_tts_withholds_late_bytes(spoken, change):
    core, activity, _, interaction, audio, _, _, result, _ = spoken
    bind(spoken)
    entered, release = Event(), Event()
    def synth(*_):
        entered.set(); assert release.wait(5)
        return wav(), 'audio/wav'
    interaction.synthesize = synth
    receipts = []
    thread = Thread(target=lambda: receipts.append(audio.render_moment(result['id'], 'listener')))
    thread.start()
    try:
        assert entered.wait(5)
        if change == 'stop': activity.control('stop_source', 'stop', {})
        elif change == 'goal': activity.control('change_goal', 'goal', {'text': 'A different shared goal.'})
        elif change == 'mode': activity.control('change_mode', 'mode', {'mode': 'quiet'})
        elif change == 'listen_off': audio.enable('listener', False)
        elif change == 'interrupt': audio.interrupt('listener')
        elif change == 'disconnect': audio.disconnect('listener')
        elif change == 'reconnect': audio.connect('listener')
        elif change == 'foreground': core.input('A new owner message.', 'owner', 'new_turn')
        elif change == 'sleep': core.control('sleep', 'sleep')
        elif change == 'forget': core.personal_control('forget_capture', 'forget', {'target_id': result['personal_source_id']})
        else:
            with core.store.transaction(core.token) as db:
                row = core.store.get(db, 'shared_moment', result['id'])
                if change == 'capture_tamper':
                    row['capture']['captured_at'] -= 1
                    core.store.put(db, 'shared_moment', row['id'], row)
                else:
                    review = core.store.get(db, 'channel_review', row['review_id'])
                    review['response']['display_text'] = 'Unreviewed replacement.'
                    core.store.put(db, 'channel_review', review['id'], review)
        release.set(); thread.join(5)
        assert not thread.is_alive() and receipts[0]['status'] == 'cancelled'
        assert not list(interaction.root.glob('*.wav'))
        if change == 'disconnect':
            with pytest.raises(ValueError, match='disconnected'):
                interaction.next_output('listener')
        else:
            assert interaction.next_output('listener')['output'] is None
    finally:
        release.set(); thread.join(5)


@pytest.mark.parametrize('change', ['stop', 'goal', 'forget'])
def test_explicit_revoke_eagerly_unlinks_released_wav(spoken, change):
    core, activity, service, interaction, audio, _, _, result, _ = spoken
    bind(spoken); audio.render_moment(result['id'], 'listener')
    output = interaction.next_output('listener')['output']; assert list(interaction.root.glob('*.wav'))
    if change == 'stop': activity.control('stop_source', 'stop', {})
    elif change == 'goal': activity.control('change_goal', 'goal', {'text': 'A new shared goal.'})
    else: core.personal_control('forget_capture', 'forget', {'target_id': result['personal_source_id']})
    assert not list(interaction.root.glob('*.wav'))
    with pytest.raises(ValueError): interaction.audio(output['id'], 'listener', output['generation'], output['output_epoch'])
    with pytest.raises(ValueError): interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], 'started')


def test_real_exhausted_watch_keeps_only_accepted_final_grant_then_explicit_stop_revokes(spoken):
    core, activity, service, interaction, audio, now, mono, _, _ = spoken
    select(activity, 'watch_source', seconds=300, maximum_reviews=10, maximum_frames=10, review_interval_seconds=10)
    service.background_image_analyzer = activity.analyze_image
    watch = SharedWatch(service)
    state = activity.status()['state']
    row = watch.start('start_watch', state['source_id'], state['source_epoch'], 5)
    # Controlled quota-edge fixture keeps the actual finite watch/current-source joins.
    with core.store.transaction(core.token) as db:
        stored = core.store.get(db, 'shared_watch', row['id'])
        stored['remaining_moments'] = 1
        core.store.put(db, 'shared_watch', row['id'], stored)
    result = watch.share(row['id'], row['lease'], 'final_moment', metadata(activity, now, 1), IMAGE + b'x', 'image/png')['moment']
    assert watch.inspect(row['id'])['status'] == 'exhausted'
    service.received('final_receipt', result['id'], result['text_sha256'], 'listener')
    def synth(*_):
        now[0] += 46; mono[0] += 46
        return wav(), 'audio/wav'
    interaction.synthesize = synth
    assert audio.render_moment(result['id'], 'listener')['status'] == 'generated'
    before = core.store.read('shared_watch', row['id'])
    assert watch.inspect(row['id'])['status'] == 'exhausted'
    assert core.store.read('shared_watch', row['id']) == before
    assert service.inspect(result['id'])['text'] == ''
    output = interaction.next_output('listener')['output']; assert output
    watch.stop(row['id'], row['lease'])
    assert not list(interaction.root.glob('*.wav'))
    assert interaction.next_output('listener')['output'] is None


def test_started_actual_moment_audio_is_known_echo_without_a_fake_owner_turn(spoken):
    core, _, _, interaction, audio, _, _, result, _ = spoken
    bind(spoken); audio.render_moment(result['id'], 'listener')
    output = interaction.next_output('listener')['output']
    interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], 'started')
    interaction.control('voice_enabled', True); interaction.connect('owner-mic')
    receipt = interaction.observe(mic_observation('echo', output['speech_text']))
    assert receipt['status'] == 'ignored' and receipt['reason'] == 'likely_playback_echo'
    assert core.store.read('turn', 'echo') is None


@pytest.mark.parametrize('target', ['review_context','review_text','review_id','moment_capture','moment_snapshot','moment_dependencies','receipt_consumer','receipt_lease','origin_fingerprint','origin_owner','origin_deadline','meta_goal'])
def test_fetch_and_ack_revalidate_sealed_exact_origin(spoken, target):
    core, _, _, interaction, audio, _, _, result, _ = spoken
    bind(spoken); audio.render_moment(result['id'], 'listener')
    output = interaction.next_output('listener')['output']
    with core.store.transaction(core.token) as db:
        moment = core.store.get(db, 'shared_moment', result['id'])
        if target.startswith('review_'):
            if target == 'review_id':
                moment['review_id'] = 'moment-review-' + '0' * 32
                core.store.put(db, 'shared_moment', moment['id'], moment)
            else:
                row = core.store.get(db, 'channel_review', moment['review_id'])
                if target == 'review_context': row['context_sha256'] = '0' * 64
                else: row['response']['display_text'] = 'Other text.'
                core.store.put(db, 'channel_review', row['id'], row)
        elif target.startswith('origin_'):
            row = core.store.get(db, 'channel_audio_request', request_id(result['id']))
            if target == 'origin_fingerprint': row['fingerprint'] = '0' * 64
            elif target == 'origin_owner': row['origin']['owner'] = '0' * 32
            else: row['origin']['expires_at'] += 1
            core.store.put(db, 'channel_audio_request', row['id'], row)
        elif target == 'meta_goal':
            row = core.store.get(db, 'shared_meta', 'state'); row['goal'] = 'Changed goal.'
            core.store.put(db, 'shared_meta', 'state', row)
        else:
            if target == 'moment_capture': moment['capture']['content_sha256'] = '0' * 64
            elif target == 'moment_snapshot': moment['snapshot']['label'] = 'Changed selected source.'
            elif target == 'moment_dependencies': moment['source_ids'] = []
            elif target == 'receipt_consumer': moment['audio_text_receipt']['consumer_id'] = 'other'
            else: moment['audio_text_receipt']['consumer_lease'] = '0' * 32
            core.store.put(db, 'shared_moment', moment['id'], moment)
    with pytest.raises(ValueError): interaction.audio(output['id'], 'listener', output['generation'], output['output_epoch'])
    with pytest.raises(ValueError): interaction.acknowledge('listener', output['id'], output['generation'], output['output_epoch'], 'started')
    assert not list(interaction.root.glob('*.wav'))
    assert interaction.next_output('listener')['output'] is None


def test_unknown_tts_failure_is_not_automatically_replayed(spoken):
    _, _, _, interaction, audio, _, _, result, calls = spoken
    bind(spoken)
    def broken(text, *_): calls.append(text); raise RuntimeError('Synthetic unknown provider result')
    interaction.synthesize = broken
    assert audio.render_moment(result['id'], 'listener')['status'] == 'failed'
    assert audio.render_moment(result['id'], 'listener')['status'] == 'expired'
    assert len(calls) == 1 and not list(interaction.root.glob('*.wav'))


def test_busy_yields_without_claiming_and_oversized_checked_text_is_honest(spoken):
    core, _, _, interaction, audio, _, _, result, calls = spoken
    bind(spoken)
    interaction._synthesis.acquire()
    try: assert audio.render_moment(result['id'], 'listener')['status'] == 'busy'
    finally: interaction._synthesis.release()
    assert core.store.read('channel_audio_request', request_id(result['id'])) is None and calls == []
    import hashlib
    with core.store.transaction(core.token) as db:
        moment = core.store.get(db, 'shared_moment', result['id'])
        review = core.store.get(db, 'channel_review', moment['review_id'])
        text = 'a' * 1601
        review['response']['display_text'] = moment['result']['text'] = text
        moment['result']['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        core.store.put(db, 'shared_moment', moment['id'], moment)
        core.store.put(db, 'channel_review', review['id'], review)
    receipt = audio.render_moment(result['id'], 'listener')
    assert receipt['status'] == 'unavailable' and receipt['reason'] == 'moment_speech_limit'
    assert calls == [] and core.store.read('channel_audio_request', request_id(result['id'])) is None
