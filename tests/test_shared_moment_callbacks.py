"""Historical callbacks contain live-provenance observations and received text only."""
import hashlib
import json

import pytest

from gamma.schemas.response import AssistantResponse
from test_shared_activity import setup, select
from test_shared_moments import moments, metadata, IMAGE
from test_shared_watch import enable


def sha(text): return hashlib.sha256(text.encode('utf-8')).hexdigest()


def test_receipt_is_idempotent_no_replay_or_lease_and_context_is_three_bounded_historical_moments(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    contexts, calls = [], []
    core.draft = lambda text, context: contexts.append(json.loads(context)) or calls.append(1) or AssistantResponse(display_text='Own contribution ' + str(len(calls)))
    for index in range(5):
        if index:
            now[0] += 30; watch.heartbeat(row['id'], row['lease'])
        result = watch.share(row['id'], row['lease'], 'moment_' + str(index), metadata(activity, now, index), IMAGE, 'image/png')['moment']
        if index != 2:
            before = watch.inspect(row['id'])
            receipt = service.received('receipt_' + str(index), result['id'], result['text_sha256'])
            duplicate = service.received('receipt_' + str(index), result['id'], result['text_sha256'])
            assert receipt['status'] == 'consumer_reported_received' and duplicate['duplicate']
            assert watch.inspect(row['id']) == before
    prior = contexts[-1]['channel_event']['prior_shared_moments']
    assert len(prior) == 3 and [p['captured_at'] for p in prior] == [1030, 1060, 1090]
    assert all(p['current_state'] == 'unknown' and p['temporal_scope'] == 'historical_capture' for p in prior)
    assert prior[0]['own_text_received_by_browser'] == 'Own contribution 2'
    assert prior[1]['contribution_status'] == 'not_received' and 'own_text_received_by_browser' not in prior[1]
    assert len(calls) == 5
    assert core.store.read('turn', 'moment_4') is None
    with pytest.raises(ValueError, match='mismatch'):
        service.received('wrong', 'moment_4', 'f' * 64)


def test_prior_forget_erases_derived_cache_and_personal_source_even_when_current_capture_is_independent(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    first = watch.share(row['id'], row['lease'], 'first', metadata(activity, now), IMAGE, 'image/png')['moment']
    service.received('received', 'first', first['text_sha256'])
    now[0] += 30; watch.heartbeat(row['id'], row['lease'])
    second = watch.share(row['id'], row['lease'], 'second', metadata(activity, now, 1), IMAGE, 'image/png')['moment']
    derived = core.store.read('shared_moment', 'second')
    assert first['personal_source_id'] in derived['source_ids']
    core.personal_control('forget_prior', 'forget', {'target_id': first['personal_source_id']})
    assert core.store.read('shared_moment', 'second')['redacted']
    assert core.store.read('channel_review', derived['review_id'])['response'] is None
    # Its independently interpreted image remains private evidence; the derived
    # operational reply and its complete callback context have been erased.
    assert core.store.read('personal_source', second['personal_source_id'])['status'] == 'active'
    assert not service.inspect('second')['text']


@pytest.mark.parametrize('boundary', ['goal', 'source', 'watch'])
def test_prior_context_never_crosses_goal_source_or_watch_boundary(moments, boundary):
    core, activity, service, now, _ = moments
    watch, row = enable(moments, 15)
    first = watch.share(row['id'], row['lease'], 'first', metadata(activity, now), IMAGE, 'image/png')['moment']
    service.received('received', 'first', first['text_sha256'])
    now[0] += 30; watch.heartbeat(row['id'], row['lease'])
    now[0] += 15; watch.heartbeat(row['id'], row['lease'])
    if boundary == 'goal': activity.control('goal', 'goal', {'text': 'A different shared goal.'})
    elif boundary == 'source':
        select(activity, 'new_source', seconds=300, maximum_reviews=30, maximum_frames=120)
    else: watch.stop(row['id'], row['lease'])
    if boundary != 'goal':
        state = activity.status()['state']
        row = watch.start('new_watch', state['source_id'], state['source_epoch'], 5)
    contexts = []
    core.draft = lambda text, context: contexts.append(json.loads(context)) or AssistantResponse(display_text='New context.')
    watch.share(row['id'], row['lease'], 'second', metadata(activity, now, 1), IMAGE, 'image/png')
    assert contexts[0]['channel_event']['prior_shared_moments'] == []


def test_lost_result_is_not_received_and_inspection_or_duplicate_never_generates_or_delivers(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    calls = []
    core.draft = lambda *_: calls.append(1) or AssistantResponse(display_text='Generated but lost result.')
    capture = metadata(activity, now)
    first = watch.share(row['id'], row['lease'], 'lost', capture, IMAGE, 'image/png')['moment']
    assert service.inspect('lost')['delivery_status'] == 'not_delivered'
    assert watch.share(row['id'], row['lease'], 'lost', capture, IMAGE, 'image/png')['moment']['delivery_status'] == 'not_delivered'
    assert calls == [1]


def test_forgetting_prior_while_core_is_pending_fences_new_derived_reply(moments):
    from threading import Event, Thread
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    first = watch.share(row['id'], row['lease'], 'first', metadata(activity, now), IMAGE, 'image/png')['moment']
    service.received('received', 'first', first['text_sha256'])
    now[0] += 30; watch.heartbeat(row['id'], row['lease'])
    entered, release = Event(), Event(); outputs = []
    def draft(*_):
        entered.set(); assert release.wait(5)
        return AssistantResponse(display_text='A derived callback that must be erased.')
    core.draft = draft
    thread = Thread(target=lambda: outputs.append(watch.share(row['id'], row['lease'], 'pending', metadata(activity, now, 1), IMAGE, 'image/png')))
    thread.start()
    try:
        assert entered.wait(5)
        core.personal_control('forget', 'forget', {'target_id':first['personal_source_id']})
        release.set(); thread.join(5)
        assert not thread.is_alive() and outputs[0]['moment']['status'] == 'cancelled' and not outputs[0]['moment']['text']
        pending = core.store.read('shared_moment','pending')
        assert pending['redacted'] and core.store.read('channel_review',pending['review_id'])['response'] is None
    finally:
        release.set();thread.join(5)


def test_unicode_callback_context_stays_within_real_core_event_byte_limit(moments):
    core, activity, service, now, _ = moments
    watch, row = enable(moments)
    contexts = []
    service.background_image_analyzer = lambda *_: {'summary':'🎮' * 600,'confidence':.5}
    core.draft = lambda text, context: contexts.append(json.loads(context)) or AssistantResponse(display_text='🎣' * 2400)
    for index in range(4):
        if index: now[0] += 30; watch.heartbeat(row['id'], row['lease'])
        result = watch.share(row['id'],row['lease'],'unicode_' + str(index),metadata(activity,now,index),IMAGE,'image/png')['moment']
        assert result['status'] == 'generated'
        service.received('received_' + str(index),result['id'],result['text_sha256'])
    event = contexts[-1]['channel_event']
    assert len(json.dumps(event['prior_shared_moments'],ensure_ascii=False).encode()) <= 6000
    assert len(json.dumps(event,ensure_ascii=False).encode()) < 16000
    assert event['prior_shared_moments'] and event['prior_shared_moments'][-1]['own_text_is_excerpt']


@pytest.mark.parametrize('pending',[False,True])
def test_actual_identity_source_forget_physically_erases_callback_but_keeps_same_watch_consent(moments,pending):
    from threading import Event,Thread
    core,activity,service,now,_=moments
    core.input('An unrelated ordinary turn before the identity edit.','controlled','unrelated_turn')
    unrelated=core.store.read('turn','unrelated_turn')
    secret='IDENTITY_ONLY_CALLBACK_CANARY'
    core.personal_control('identity','identity',{'fields':{'speech_style':secret}})
    identity_source=core.personal_state()['identity']['field_sources']['speech_style']
    watch,row=enable(moments)
    contexts=[]
    core.draft=lambda text,context:contexts.append(json.loads(context)) or AssistantResponse(display_text=secret)
    first=watch.share(row['id'],row['lease'],'identity_echo',metadata(activity,now),IMAGE,'image/png')['moment']
    service.received('receipt','identity_echo',first['text_sha256'])
    assert identity_source in core.store.read('shared_moment','identity_echo')['source_ids']
    entered,release=Event(),Event();outputs=[];thread=None
    if pending:
        now[0]+=30;watch.heartbeat(row['id'],row['lease'])
        def draft(*_):entered.set();assert release.wait(5);return AssistantResponse(display_text=secret)
        core.draft=draft
        thread=Thread(target=lambda:outputs.append(watch.share(row['id'],row['lease'],'pending_identity',metadata(activity,now,1),IMAGE,'image/png')))
        thread.start();assert entered.wait(5)
    before=watch.inspect(row['id'])
    core.personal_control('forget_identity','forget',{'target_id':identity_source})
    release.set()
    if thread:thread.join(5);assert not thread.is_alive() and outputs[0]['moment']['status']=='cancelled'
    with core.store.transaction(core.token) as db:
        # Inspect actual persisted payloads, not merely status/rendered views.
        assert not any(secret in body for (body,) in db.execute("SELECT body FROM records WHERE kind IN ('shared_moment','channel_review')"))
    assert core.store.read('turn','unrelated_turn')==unrelated
    after=watch.inspect(row['id'])
    assert after['status']=='active' and after['expires_at']==before['expires_at']
    assert after['consumed_moments']==before['consumed_moments'] and activity.status()['state']['enabled']
    now[0]=max(now[0]+30,after['next_sample_at']);watch.heartbeat(row['id'],row['lease'])
    contexts=[]
    core.draft=lambda text,context:contexts.append(json.loads(context)) or AssistantResponse(display_text='Only current canonical identity.')
    next_result=watch.share(row['id'],row['lease'],'after_forget',metadata(activity,now,2),IMAGE,'image/png')['moment']
    assert next_result['status']=='generated' and secret not in json.dumps(contexts)
    assert contexts[0]['channel_event']['prior_shared_moments']==[]


@pytest.mark.parametrize('binding',['missing','mismatch','redacted'])
def test_post_clear_erases_legacy_or_invalid_review_text_without_erasing_independent_image(moments,binding):
    from gamma.coordination.shared_moments import redact_removed_reviews
    core,activity,service,now,_=moments
    watch,row=enable(moments)
    result=watch.share(row['id'],row['lease'],'legacy',metadata(activity,now),IMAGE,'image/png')['moment']
    service.received('received','legacy',result['text_sha256'])
    original=core.store.read('shared_moment','legacy')
    with core.store.transaction(core.token) as db:
        if binding=='missing':
            old=core.store.get(db,'shared_moment','legacy');old['review_id']=None;core.store.put(db,'shared_moment','legacy',old)
        else:
            review=core.store.get(db,'channel_review',original['review_id'])
            if binding=='redacted':review.update(status='redacted',response=None)
            else:review['response']['display_text']='Unrelated checked text.'
            core.store.put(db,'channel_review',review['id'],review)
        assert redact_removed_reviews(core.store,db,now[0])==1
    cleaned=core.store.read('shared_moment','legacy')
    assert cleaned['result']['text']=='' and cleaned['text_receipt'] is None and cleaned['text_received_count']==1
    assert cleaned['capture']==original['capture'] and cleaned['summary']==original['summary']
    assert cleaned['fingerprint']==original['fingerprint']
