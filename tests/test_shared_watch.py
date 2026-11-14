"""Finite opt-in watching is a sequence of historical captures, not live truth."""
from threading import Event, Thread

import pytest

from gamma.coordination.shared_watch import SharedWatch
from gamma.schemas.response import AssistantResponse
from test_shared_activity import setup, select
from test_shared_moments import moments, metadata, analyzer, IMAGE, CHANGED


def enable(moments, minutes=5):
    core, activity, service, now, mono = moments
    select(activity, 'watch_source_' + str(minutes), seconds=minutes * 60, maximum_reviews=30,
           review_interval_seconds=10, maximum_frames=120)
    analyzer(activity)
    service.background_image_analyzer = activity.analyze_image
    watch = SharedWatch(service)
    state = activity.status()['state']
    row = watch.start('watch_start', state['source_id'], state['source_epoch'], minutes)
    return watch, row


@pytest.mark.parametrize('minutes,quota,cadence', [(5, 10, 30), (15, 20, 45), (30, 30, 60)])
def test_duration_and_sample_budget_are_explicit_and_start_is_idempotent(moments, minutes, quota, cadence):
    _, activity, _, now, _ = moments
    watch, row = enable(moments, minutes)
    assert row['maximum_moments'] == row['remaining_moments'] == quota
    assert row['expires_at'] == now[0] + minutes * 60
    now[0] += 10
    watch.heartbeat(row['id'], row['lease'])
    same = watch.start('watch_start', row['source_id'], row['source_epoch'], minutes)
    assert same['id'] == row['id'] and same['expires_at'] == row['expires_at']
    assert same['consumed_moments'] == 0
    with pytest.raises(ValueError, match='already_active'):
        watch.start('another_request', row['source_id'], row['source_epoch'], minutes)
    with pytest.raises(ValueError, match='invalid'):
        watch.start('long', row['source_id'], row['source_epoch'], 120)


def test_heartbeat_confirms_availability_without_renewing_image_or_fixed_authority(moments):
    _, activity, service, now, _ = moments
    watch, row = enable(moments)
    activity.observe_image(metadata(activity, now), IMAGE, 'image/png')
    before = activity.status()['state']
    now[0] += 20
    renewed = watch.heartbeat(row['id'], row['lease'])
    after = activity.status()
    assert renewed['availability_expires_at'] == now[0] + 45
    assert renewed['expires_at'] == row['expires_at']
    assert after['state']['expires_at'] == before['expires_at']
    assert after['state']['latest_frame'] == before['latest_frame']
    assert after['context']['observation'] is None
    assert renewed['remaining_moments'] == row['remaining_moments']


def test_quiet_decisions_back_off_with_no_forced_contribution(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    core.draft = lambda *_: AssistantResponse(response_action='stay_silent', delivery_mode='silent')
    first = watch.share(row['id'], row['lease'], 'quiet_1', metadata(activity, now), IMAGE, 'image/png')
    assert first['moment']['status'] == 'silent'
    assert first['watch']['remaining_moments'] == 9 and first['watch']['next_sample_at'] == now[0] + 60
    now[0] += 30; watch.heartbeat(row['id'], row['lease'])
    now[0] += 30
    second = watch.share(row['id'], row['lease'], 'quiet_2', metadata(activity, now, 1), CHANGED, 'image/png')
    assert second['watch']['next_sample_at'] == now[0] + 120
    assert second['watch']['quiet_streak'] == 2 and second['watch']['remaining_moments'] == 8


def test_pending_foreground_yields_without_vision_dispatch_or_budget_consumption(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    entered, release = Event(), Event(); calls = []
    service.background_image_analyzer = lambda *_: calls.append(1) or {'summary': 'Must not run.', 'confidence': .5}
    def draft(*_):
        entered.set(); assert release.wait(5)
        return AssistantResponse(display_text='Controlled foreground reply.')
    core.draft = draft
    thread = Thread(target=lambda: core.input('CONTROLLED authored foreground question.', 'controlled', 'foreground'))
    thread.start()
    try:
        assert entered.wait(5)
        result = watch.share(row['id'], row['lease'], 'yield', metadata(activity, now), IMAGE, 'image/png')
        assert result['moment']['status'] == 'busy' and result['moment']['reason'] == 'foreground_active'
        assert result['watch']['consumed_moments'] == 0 and calls == []
    finally:
        release.set(); thread.join(5)


@pytest.mark.parametrize('withdrawal', ['stop_watch', 'stop_source', 'availability', 'forget'])
def test_actual_control_withdrawal_cancels_pending_capture_and_never_resumes(moments, withdrawal):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    entered, release = Event(), Event(); results, drafts = [], []
    def image(*_):
        entered.set(); assert release.wait(5)
        return {'summary': 'A private historical image.', 'confidence': .6}
    service.background_image_analyzer = image
    core.draft = lambda *_: drafts.append(1) or AssistantResponse(display_text='Must not release.')
    thread = Thread(target=lambda: results.append(watch.share(row['id'], row['lease'], 'pending', metadata(activity, now), IMAGE, 'image/png')))
    thread.start()
    try:
        assert entered.wait(5)
        if withdrawal == 'stop_watch': watch.stop(row['id'], row['lease'])
        elif withdrawal == 'stop_source': activity.control('stop', 'stop', {})
        elif withdrawal == 'availability': now[0] += 45
        else:
            source_id = core.store.read('shared_moment', 'pending')['personal_source_id']
            core.personal_control('forget', 'forget', {'target_id': source_id})
        release.set(); thread.join(5)
        assert results[0]['moment']['status'] == 'cancelled' and not results[0]['moment'].get('text')
        assert results[0]['watch']['status'] == 'stopped' and drafts == []
        with pytest.raises(ValueError, match='lease_changed'):
            watch.heartbeat(row['id'], row['lease'])
        assert watch.inspect(row['id'])['status'] == 'stopped'
    finally:
        release.set(); thread.join(5)


def test_source_selection_does_not_reset_a_running_watch_or_its_consumed_budget(moments):
    _, activity, service, now, _ = moments
    watch, row = enable(moments)
    first = watch.share(row['id'], row['lease'], 'first', metadata(activity, now), IMAGE, 'image/png')
    assert first['watch']['consumed_moments'] == 1
    select(activity, 'new_explicit_source', seconds=300, maximum_reviews=30, maximum_frames=120)
    stopped = watch.inspect(row['id'])
    assert stopped['status'] == 'stopped' and stopped['remaining_moments'] == 9
    same = watch.start('watch_start', row['source_id'], row['source_epoch'], 5)
    assert same['id'] == row['id'] and same['status'] == 'stopped' and same['consumed_moments'] == 1
    state = activity.status()['state']
    explicit_new = watch.start('new_owner_start', state['source_id'], state['source_epoch'], 5)
    assert explicit_new['id'] != row['id'] and explicit_new['consumed_moments'] == 0


def test_availability_pulses_never_extend_original_watch_deadline(moments):
    _, activity, _, now, _ = moments
    watch, row = enable(moments)
    for _ in range(7):
        now[0] += 40
        assert watch.heartbeat(row['id'], row['lease'])['expires_at'] == 1300
    now[0] = 1300
    with pytest.raises(ValueError): watch.heartbeat(row['id'], row['lease'])
    stopped = watch.inspect(row['id'])
    assert stopped['status'] == 'stopped' and stopped['reason'] == 'watch_deadline'


def test_sample_budget_is_not_refilled_by_requests_heartbeats_or_duplicate_start(moments):
    _, activity, service, now, _ = moments
    watch, row = enable(moments)
    for index in range(10):
        if index:
            now[0] += 30; watch.heartbeat(row['id'], row['lease'])
        result = watch.share(row['id'], row['lease'], 'sample_' + str(index), metadata(activity, now, index), IMAGE, 'image/png')
        assert result['moment']['status'] == 'generated'
        assert result['watch']['consumed_moments'] == index + 1
    assert result['watch']['status'] == 'exhausted' and result['watch']['remaining_moments'] == 0
    same = watch.start('watch_start', row['source_id'], row['source_epoch'], 5)
    assert same['status'] == 'exhausted' and same['remaining_moments'] == 0


def test_unknown_provider_error_stops_watch_and_source_without_replay(moments):
    _, activity, service, now, _ = moments
    watch, row = enable(moments)
    calls = []
    def failed(*_): calls.append(1); raise RuntimeError('controlled unknown outcome')
    service.background_image_analyzer = failed
    original = metadata(activity, now)
    with pytest.raises(RuntimeError): watch.share(row['id'], row['lease'], 'unknown', original, IMAGE, 'image/png')
    assert watch.inspect(row['id'])['status'] == 'stopped' and not activity.status()['state']['enabled']
    repeated = watch.share(row['id'], row['lease'], 'unknown', original, IMAGE, 'image/png')
    assert repeated['moment']['status'] == 'failed' and calls == [1]


def test_background_unavailable_never_substitutes_manual_flow(moments):
    _, activity, service, now, _ = moments
    watch, row = enable(moments)
    watch.stop(row['id'], row['lease'])
    service.background_image_analyzer = None
    with pytest.raises(ValueError, match='background_image'):
        watch.start('unavailable', row['source_id'], row['source_epoch'], 5)
    # Explicit manual sharing still uses its configured foreground analyzer.
    result = service.share('manual', metadata(activity, now), IMAGE, 'image/png')
    assert result['status'] == 'generated'


def exhausted(moments):
    core,activity,service,now,_=moments
    watch,row=enable(moments)
    calls=[]
    core.draft=lambda *_:calls.append(1) or AssistantResponse(display_text='A final captured moment.')
    for index in range(10):
        if index:now[0]+=30;watch.heartbeat(row['id'],row['lease'])
        result=watch.share(row['id'],row['lease'],'finite_'+str(index),metadata(activity,now,index),IMAGE,'image/png')
    return watch,row,result,calls


def test_final_sample_allows_receipt_only_and_lost_ack_retry_without_renewal_or_new_inference(moments):
    _,activity,service,now,_=moments
    watch,row,result,calls=exhausted(moments)
    assert result['watch']['status']=='exhausted' and result['moment']['status']=='generated'
    before=watch.inspect(row['id'])
    # The first acknowledgment result is deliberately discarded as if lost.
    service.received('last_received',result['moment']['id'],result['moment']['text_sha256'])
    again=service.received('last_received',result['moment']['id'],result['moment']['text_sha256'])
    assert again['duplicate'] and service.store.read('shared_moment',result['moment']['id'])['text_received_count']==1
    now[0]+=1
    assert watch.heartbeat(row['id'],row['lease'])==before
    no_more=watch.share(row['id'],row['lease'],'extra',metadata(activity,now,10),IMAGE,'image/png')
    assert no_more['moment']['status']=='deferred' and len(calls)==10
    assert no_more['watch']['consumed_moments']==10 and no_more['watch']['remaining_moments']==0
    watch.stop(row['id'],row['lease'])
    assert service.inspect(result['moment']['id'])['status']=='cancelled'


@pytest.mark.parametrize('withdrawal',['watch_stop','source_stop','forget'])
def test_explicit_withdrawal_revokes_exhausted_final_delivery_before_receipt(moments,withdrawal):
    core,activity,service,now,_=moments
    watch,row,result,calls=exhausted(moments)
    if withdrawal=='watch_stop':watch.stop(row['id'],row['lease'])
    elif withdrawal=='source_stop':activity.control('stop','stop',{})
    else:core.personal_control('forget','forget',{'target_id':result['moment']['personal_source_id']})
    with pytest.raises(ValueError,match='unavailable'):
        service.received('late',result['moment']['id'],result['moment']['text_sha256'])
    assert len(calls)==10 and not service.inspect(result['moment']['id'])['text']
