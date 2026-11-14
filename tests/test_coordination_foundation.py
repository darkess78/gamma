"""Connected foundation regressions, using the actual owner and durable store."""
import json
import pytest

from test_coordination_core import make_core
from gamma.schemas.response import AssistantResponse, BoundedStateUpdates
from gamma.coordination.store import private_path
from gamma.coding.sandbox import run_practice_check
from gamma.coding.evaluation import evaluate_candidate


def test_private_storage_rejects_parent_traversal_before_touching_state(tmp_path):
    with pytest.raises(ValueError, match='parent traversal'):
        private_path(tmp_path / 'inside' / '..' / 'outside')
    assert not (tmp_path / 'inside').exists()


@pytest.mark.parametrize('action', ['practice', 'request_practice'])
def test_focused_owner_can_choose_permitted_quiet_work(tmp_path, action):
    now = [1000.0]
    def choose(text, context):
        return AssistantResponse(delivery_mode='silent', response_action='stay_silent',
            state_updates=BoundedStateUpdates(activity_action=action, practice_exercise='retry_budget'))
    core = make_core(tmp_path, clock=lambda: now[0], draft=choose)
    try:
        core.control('focus', 'focus', focus='focused')
        core.control('scope', 'permit', exercise_ids=['retry_budget'], budget_seconds=300)
        now[0] += 31
        core.review()
        status = core.status()
        assert len(status['activities']) == 1
        assert status['activities'][0]['origin'] == 'authorized_quiet_time'
        assert status['focus'] == 'focused'
    finally:
        core.close()


def test_scheduled_review_receives_latest_private_session(tmp_path):
    now = [1000.0]
    seen = []
    def observe(text, context):
        seen.append(json.loads(context))
        return AssistantResponse(display_text='Understood.', delivery_mode='text_only',
            state_updates=BoundedStateUpdates(activity_action='wait'))
    core = make_core(tmp_path, clock=lambda: now[0], draft=observe)
    try:
        core.input('Our current goal is the lighthouse exercise.', 'monitor-local-stream', 'current')
        core.control('scope', 'permit', exercise_ids=['retry_budget'], budget_seconds=300)
        now[0] += 31
        core.review()
        assert len(seen) == 2
        assert any('lighthouse' in t['user'] for t in seen[-1]['recent_exchanges'])
    finally:
        core.close()


def test_wait_reconsideration_cannot_outlast_permission(tmp_path):
    now = [1000.0]
    core = make_core(tmp_path, clock=lambda: now[0], draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent',
        state_updates=BoundedStateUpdates(activity_action='wait', reconsider_after_seconds=3600)))
    try:
        core.control('scope', 'permit', exercise_ids=['retry_budget'], budget_seconds=90)
        now[0] += 31
        core.review()
        brain = core.store.read('brain', 'state')
        assert now[0] < brain['reconsider_at'] <= brain['permit']['expires_at']
    finally:
        core.close()


def test_conversation_keeps_request_then_switches_and_resumes_saved_work(tmp_path):
    target = [None]
    def decisions(text, _context):
        actions = {'do this next': ('request_practice', None), 'switch now': ('switch', target[0]),
                   'pause that': ('pause', target[0]), 'resume original': ('resume', original)}
        action, run_id = actions[text]
        return AssistantResponse(delivery_mode='silent', response_action='stay_silent',
            state_updates=BoundedStateUpdates(activity_action=action, practice_exercise='message_chunks', activity_run_id=run_id))
    core = make_core(tmp_path, draft=decisions)
    try:
        core.control('scope', 'permit', exercise_ids=['message_chunks'], maximum_runs=1)
        original = core.control('original', 'practice', exercise_id='retry_budget')['activity']['id']
        requested = core.input('do this next', 'actual-session', 'request')['result']['assistant_response']
        waiting = [a for a in core.status()['activities'] if a['worker_run'] is None]
        assert len(waiting) == 1 and waiting[0]['origin'] == 'owner_request'
        assert requested['reason_code'] == 'activity_waiting'
        target[0] = waiting[0]['id']
        core.input('switch now', 'actual-session', 'switch')
        assert core.store.read('run', original)['desired_state'] == 'pause'
        core.runner.run(original)
        assert core.store.read('run', original)['status'] == 'paused'
        core.input('pause that', 'actual-session', 'pause-new')
        assert core.store.read('activity', target[0])['status'] == 'paused'
        core.input('resume original', 'actual-session', 'resume-old')
        assert core.store.read('run', original)['desired_state'] == 'running'
        assert core.store.read('brain', 'state')['permit']['remaining_runs'] == 0
    finally:
        core.close()


def test_natural_status_can_target_one_run_and_does_not_pool_old_failure(tmp_path):
    chosen = [None]
    core = make_core(tmp_path, draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent',
        state_updates=BoundedStateUpdates(activity_action='report_status', activity_run_id=chosen[0])))
    try:
        old = core.control('older', 'practice', exercise_id='message_chunks')['activity']['id']
        core.control('stop-older', 'stop', run_id=old)
        core.runner.run(old)
        current = core.control('newer', 'practice', exercise_id='retry_budget')['activity']['id']
        core.runner.run(current)
        chosen[0] = current
        reply = core.input('How did the one you just finished turn out?', 'monitor-local-stream', 'ask')['result']['assistant_response']
        assert reply['reason_code'] == 'practice_status'
        assert current in reply['display_text'] and old not in reply['display_text']
        assert 'passed' in reply['display_text'] and 'stopped' not in reply['display_text']
    finally:
        core.close()


def test_status_turn_retains_context_updates_and_operator_start_selects_new_run(tmp_path):
    core = make_core(tmp_path, draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent', state_updates=BoundedStateUpdates(
            activity_action='report_status', attention_focus='focused', emotion='teasing')))
    try:
        first = core.control('one', 'practice', exercise_id='retry_budget')['activity']['id']
        core.runner.run(first)
        second = core.control('two', 'practice', exercise_id='message_chunks')['activity']['id']
        reply = core.input('I am focusing now; how is practice going?', 'private', 'status')['result']['assistant_response']
        assert second in reply['display_text'] and first not in reply['display_text']
        assert core.status()['focus'] == 'focused' and core.status()['mood'] == 'teasing'
    finally:
        core.close()


def test_expired_paused_work_preserves_evidence_and_does_not_block_fresh_scope(tmp_path):
    now = [1000.0]
    core = make_core(tmp_path, clock=lambda: now[0], draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent', state_updates=BoundedStateUpdates(
            activity_action='practice', practice_exercise='retry_budget')))
    try:
        old = core.control('old', 'practice', exercise_id='retry_budget', budget_seconds=30)['activity']['id']
        core.control('pause-old', 'pause', run_id=old)
        core.runner.run(old)
        before = core.store.read('run', old)
        now[0] = 1031
        core.control('fresh', 'permit', exercise_ids=['retry_budget'], budget_seconds=300)
        now[0] += 31
        core.review()
        after = core.store.read('run', old)
        assert after['status'] == 'blocked' and after['reason'] == 'budget_exhausted'
        assert after['checkpoint_ref'] == before['checkpoint_ref'] and after['expires_at'] == before['expires_at']
        assert len(core.status()['activities']) == 2
    finally:
        core.close()


def test_deferred_user_request_dispatches_with_reserved_scope_and_real_checks(tmp_path):
    core = make_core(tmp_path, check=run_practice_check,
        evaluate=lambda task, files: evaluate_candidate(task, files, check=run_practice_check),
        draft=lambda *_: AssistantResponse(delivery_mode='silent', response_action='stay_silent',
            state_updates=BoundedStateUpdates(activity_action='request_practice', practice_exercise='retry_budget')))
    try:
        core.control('scope', 'permit', exercise_ids=['retry_budget'], maximum_attempts=2)
        old = core.control('old', 'practice', exercise_id='retry_budget')['activity']['id']
        core.input('Please do another exercise after that one.', 'private', 'later')
        intention = next(a for a in core.status()['activities'] if a['worker_run'] is None)
        core.control('stop-old', 'stop', run_id=old)
        core.runner.run(old)
        core.tick()
        core.future.result(timeout=15)
        run = core.store.read('run', intention['id'])
        assert run['status'] == 'completed' and run['maximum_attempts'] == 2
        assert run['expires_at'] == intention['expires_at']
        assert core.store.read('activity', intention['id'])['source_request'] == 'later'
        assert core.store.read('brain', 'state')['permit']['remaining_runs'] == 0
    finally:
        core.close()


def test_old_paused_work_survives_more_than_recent_history_on_reopen(tmp_path):
    from copy import deepcopy
    core = make_core(tmp_path)
    original = core.control('old', 'practice', exercise_id='retry_budget')['activity']['id']
    core.control('hold', 'pause', run_id=original)
    core.runner.run(original)
    before = core.store.read('run', original)
    with core.store.transaction(core.token) as db:
        old_activity = core.store.get(db, 'activity', original)
        for number in range(105):
            identifier = f'practice-newer-{number}'
            run = deepcopy(before)
            run.update(id=identifier, status='stopped', desired_state='stop', created_at=before['created_at'] + number + 1)
            activity = deepcopy(old_activity)
            activity.update(id=identifier, worker_run=identifier, status='stopped', created_at=run['created_at'])
            core.store.put(db, 'run', identifier, run)
            core.store.put(db, 'activity', identifier, activity)
    core.close()
    core = make_core(tmp_path)
    try:
        status = core.status()
        assert any(a['id'] == original and a['status'] == 'paused' for a in status['activities'])
        with core.store.transaction() as db:
            assert any(a['id'] == original for a in json.loads(core._context(db, 'private'))['activities'])
        core.control('resume-old', 'resume', run_id=original)
        after = core.store.read('run', original)
        assert after['desired_state'] == 'running'
        assert all(after[k] == before[k] for k in ('checkpoint_ref', 'initial_checkpoint_ref', 'expires_at', 'maximum_attempts'))
    finally:
        core.close()


def test_direct_practice_keeps_user_provenance_instead_of_claiming_self_start(tmp_path):
    core = make_core(tmp_path, draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent', state_updates=BoundedStateUpdates(
            activity_action='practice', practice_exercise='retry_budget')))
    try:
        core.control('scope', 'permit', exercise_ids=['retry_budget'])
        core.input('Please start this exercise now.', 'private', 'user-start')
        activity = core.status()['activities'][0]
        assert activity['origin'] == 'owner_request' and activity['source_request'] == 'user-start'
        assert activity['requested_goal'] == 'Please start this exercise now.'
    finally:
        core.close()


def test_dispatch_overhead_cannot_extend_the_finite_permission(tmp_path):
    now = [1000.0]
    def clock():
        now[0] += .01
        return now[0]
    core = make_core(tmp_path, clock=clock, draft=lambda *_: AssistantResponse(
        delivery_mode='silent', response_action='stay_silent', state_updates=BoundedStateUpdates(
            activity_action='practice', practice_exercise='retry_budget')))
    try:
        core.control('scope', 'permit', exercise_ids=['retry_budget'])
        deadline = core.store.read('brain', 'state')['permit']['expires_at']
        core.input('Please start.', 'private', 'start')
        activity = core.status()['activities'][0]
        assert core.store.read('run', activity['id'])['expires_at'] == deadline
    finally:
        core.close()
