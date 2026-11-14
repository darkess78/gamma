"""Acceptance scenarios for the isolated owner, durable practice and HTTP ingress."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from gamma.coordination.app import build_app
from gamma.coordination.core import Core
from gamma.coordination.store import OwnershipLost, Store, process_lock
from gamma.coding.durable_practice import PracticeRunner, PracticeYield
from gamma.coding.practice import _digest
from gamma.coding.sandbox import run_practice_check
from gamma.schemas.response import AssistantResponse, BoundedStateUpdates


SOLUTION = '''def should_retry(error, attempt, maximum_attempts):
    return isinstance(error, str) and error in ('busy','rate_limit') and type(attempt) is int and type(maximum_attempts) is int and 1 <= attempt < maximum_attempts
'''


def propose(*_args):
    return {'solution.py': SOLUTION}


def check(workspace, spec):
    passed = (workspace / 'solution.py').read_text() == SOLUTION
    return {'name': spec.name, 'kind': spec.kind, 'passed': passed, 'exit_code': 0 if passed else 1,
            'output_tail': 'public check failed' if not passed else 'check_completed'}


def evaluate(task, files):
    return {'source_sha256': _digest(files), 'passed': True, 'status': 'passed', 'withheld_secret': 'DO_NOT_RETAIN'}


def draft(text, context):
    return AssistantResponse(display_text='Still here.', delivery_mode='text_only',
                             state_updates=BoundedStateUpdates(emotion='teasing', active_topic='ongoing work'))


def make_core(root, **kwargs):
    store = Store(root)
    runner = PracticeRunner(store, propose=kwargs.get('propose', propose), check=kwargs.get('check', check),
                            evaluate=kwargs.get('evaluate', evaluate), clock=kwargs.get('clock', time.time))
    core = Core(store, runner=runner, draft=kwargs.get('draft', draft), clock=kwargs.get('clock', time.time))
    return core


def start(core, identifier='one', **kwargs):
    return core.control(identifier, 'practice', exercise_id='retry_budget', **kwargs)['activity']['id']


def test_real_http_ingress_real_offline_checks_sleep_and_duplicate_catch_up(tmp_path):
    # The real sandbox and withheld generated-case evaluator are used here.
    app = build_app(tmp_path, bearer_token='test-owner', draft_factory=lambda: draft,
                    runner_factory=lambda store: PracticeRunner(store, propose=propose, check=run_practice_check),
                    run_background=False)
    with TestClient(app) as client:
        assert client.get('/v1/coordination/status?audience=owner_private').status_code == 401
        client.headers['Authorization'] = 'Bearer test-owner'
        assert client.post('/v1/coordination/input', json={'request_id': 'bad', 'text': 'private', 'audience': 'shared'}).status_code == 422
        data = {'request_id': 'start', 'audience': 'owner_private', 'action': 'practice', 'exercise_id': 'retry_budget'}
        accepted = client.post('/v1/coordination/control', json=data)
        assert accepted.status_code == 200, accepted.text
        run_id = accepted.json()['activity']['worker_run']
        assert client.post('/v1/coordination/control', json=data).json()['activity']['worker_run'] == run_id
        client.post('/v1/coordination/control', json={'request_id': 'sleep', 'audience': 'owner_private', 'action': 'sleep'})
        app.state.core.runner.run(run_id)
        run = app.state.core.store.read('run', run_id)
        assert run['status'] == 'completed', run
        sleeping = client.get('/v1/coordination/status?audience=owner_private').json()
        assert sleeping['activities'][0]['status'] == 'queued'
        assert sleeping['catch_up'] == []
        deferred = client.post('/v1/coordination/input', json={'request_id': 'asleep-input', 'audience': 'owner_private', 'text': 'hello'}).json()
        assert deferred['result']['assistant_response']['reason_code'] == 'asleep'
        wake = client.post('/v1/coordination/control', json={'request_id': 'wake', 'audience': 'owner_private', 'action': 'wake'}).json()
        assert wake['catch_up'][0]['status'] == 'completed'
        assert wake['catch_up'][0]['result_ref'] == run['checkpoint_ref']
        assert wake['catch_up'][0]['observed_after_sleep']
        assert client.get('/v1/coordination/status?audience=owner_private').json()['catch_up'] == []
        result = client.post('/v1/coordination/input', json={'request_id': 'chat', 'audience': 'owner_private', 'text': 'still there?'}).json()
        assert result['result']['assistant_response']['display_text'] == 'Still here.'
        assert result['result']['assistant_response']['speech_text'] == ''
        assert not list(tmp_path.rglob('*.wav'))


def test_candidate_saved_before_pause_resumes_checks_without_model_replay(tmp_path):
    calls = []
    core = None
    def pausing_proposal(*args):
        calls.append('model')
        core.control('pause', 'pause', run_id=run_id)
        return propose(*args)
    core = make_core(tmp_path, propose=pausing_proposal)
    run_id = start(core)
    before = core.store.read('run', run_id)
    core.runner.run(run_id)
    paused = core.store.read('run', run_id)
    checkpoint = core.runner.artifacts.get(paused['checkpoint_ref'])
    assert paused['status'] == 'paused'
    assert checkpoint['tasks'][0]['stage'] == 'candidate_saved'
    assert checkpoint['tasks'][0]['attempt_count'] == 1
    core.close()
    resumed = make_core(tmp_path, propose=lambda *_: pytest.fail('saved edit regenerated'))
    try:
        resumed.control('resume', 'resume', run_id=run_id)
        resumed.runner.run(run_id)
        final = resumed.store.read('run', run_id)
        assert final['status'] == 'completed'
        assert final['expires_at'] == before['expires_at']
        assert calls == ['model']
        assert 'DO_NOT_RETAIN' not in ''.join(p.read_text() for p in (tmp_path / 'artifacts').glob('*.json'))
    finally:
        resumed.close()


def test_crash_during_inference_is_uncertain_consumes_attempt_and_never_replays(tmp_path):
    def crash(*_args):
        raise KeyboardInterrupt('simulated process death')
    core = make_core(tmp_path, propose=crash)
    run_id = start(core)
    with pytest.raises(KeyboardInterrupt):
        core.runner.run(run_id)
    core.close()
    recovered = make_core(tmp_path, propose=lambda *_: pytest.fail('uncertain inference replayed'))
    try:
        recovered.runner.run(run_id)
        run = recovered.store.read('run', run_id)
        assert run['status'] == 'blocked'
        assert run['reason'] == 'uncertain_inference_no_replay'
        assert recovered.runner.artifacts.get(run['checkpoint_ref'])['tasks'][0]['attempt_count'] == 1
    finally:
        recovered.close()


def test_crash_after_saved_candidate_recovery_tests_that_edit(tmp_path):
    core = make_core(tmp_path)
    run_id = start(core)
    save = core.runner._save
    def crash_after_saved(identifier, checkpoint, **kwargs):
        result = save(identifier, checkpoint, **kwargs)
        if checkpoint['tasks'][0]['stage'] == 'candidate_saved':
            raise KeyboardInterrupt('crash after durable artifact publication')
        return result
    core.runner._save = crash_after_saved
    with pytest.raises(KeyboardInterrupt):
        core.runner.run(run_id)
    core.close()
    recovered = make_core(tmp_path, propose=lambda *_: pytest.fail('durable edit regenerated'))
    try:
        recovered.runner.run(run_id)
        assert recovered.store.read('run', run_id)['status'] == 'completed'
    finally:
        recovered.close()


def test_foreground_chat_during_worker_and_stale_generation_at_commit(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def blocked_draft(text, context):
        entered.set()
        assert release.wait(5)
        return draft(text, context)
    core = make_core(tmp_path, draft=blocked_draft)
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(core.input, 'first', 'private', 'first')
            assert entered.wait(5)
            core.store.claim(core.token[0])
            release.set()
            with pytest.raises(OwnershipLost):
                future.result()
        assert core.store.read('turn', 'first')['status'] == 'recoverable'
        with pytest.raises(OwnershipLost):
            start(core)
        with pytest.raises(OwnershipLost):
            core.tick()
    finally:
        release.set()
        core.close()


def test_background_worker_does_not_block_conversation_or_finish_claim(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def blocked_proposal(*args):
        entered.set()
        assert release.wait(5)
        return propose(*args)
    core = make_core(tmp_path, propose=blocked_proposal)
    try:
        run_id = start(core)
        core.tick()
        assert entered.wait(5)
        assert core.input('hello', 'private', 'chat')['result']['assistant_response']['display_text'] == 'Still here.'
        assert core.store.read('run', run_id)['status'] == 'running'
        release.set()
        core.future.result(timeout=10)
        assert core.status()['activities'][0]['status'] == 'completed'
    finally:
        release.set()
        core.close()


def test_budget_and_attempts_cannot_be_replenished_by_resume(tmp_path):
    now = [1000.0]
    core = make_core(tmp_path, clock=lambda: now[0])
    try:
        run_id = start(core, budget_seconds=30)
        core.control('pause', 'pause', run_id=run_id)
        core.runner.run(run_id)
        now[0] += 31
        with pytest.raises(ValueError, match='expired'):
            core.control('resume', 'resume', run_id=run_id)
        assert core.store.read('run', run_id)['expires_at'] == 1030
        assert core.store.read('run', run_id)['status'] == 'paused'
    finally:
        core.close()
    limited = make_core(tmp_path / 'attempts', propose=lambda *_: {'solution.py': 'def should_retry(*args): return False\n'})
    try:
        run_id = start(limited, maximum_attempts=2)
        limited.runner.run(run_id)
        cp = limited.runner.artifacts.get(limited.store.read('run', run_id)['checkpoint_ref'])
        assert cp['tasks'][0]['attempt_count'] == 2
        assert cp['tasks'][0]['status'] == 'needs_checker'
    finally:
        limited.close()


def test_quiet_review_bounded_nonverbal_focused_work_allowed_sleep_waits(tmp_path):
    now, calls = [1000.0], []
    def quiet(text, context):
        calls.append(text)
        return AssistantResponse(delivery_mode='silent', response_action='stay_silent',
                                 state_updates=BoundedStateUpdates(activity_action='practice', practice_exercise='retry_budget'))
    core = make_core(tmp_path, clock=lambda: now[0], draft=quiet)
    try:
        core.control('permit', 'permit', exercise_ids=['retry_budget'])
        core.control('focus', 'focus', focus='focused')
        now[0] += 31
        for _ in range(10):
            core.review()
        assert len(calls) == 1
        core.control('sleep', 'sleep')
        core.control('relax', 'focus', focus='relaxed')
        core.review()
        assert len(calls) == 1
        core.control('wake', 'wake')
        core.review()
        assert len(calls) == 1
        activities = core.status()['activities']
        assert len(activities) == 1 and activities[0]['origin'] == 'authorized_quiet_time'
        for _ in range(10):
            core.review()
        assert len(calls) == 1
    finally:
        core.close()


def test_tampered_artifact_and_changed_checks_never_become_completion(tmp_path):
    core = make_core(tmp_path)
    try:
        run_id = start(core)
        core.runner.run(run_id)
        run = core.store.read('run', run_id)
        cp = core.runner.artifacts.get(run['checkpoint_ref'])
        source_path = core.runner.artifacts.root / (cp['tasks'][0]['source_ref'] + '.json')
        source_path.write_text('{}')
        assert core.status()['activities'][0]['status'] == 'blocked'
    finally:
        core.close()
    changed = make_core(tmp_path / 'changed')
    try:
        run_id = start(changed)
        with patch('gamma.coding.durable_practice.task_contract', return_value='different'):
            changed.runner.run(run_id)
        assert changed.store.read('run', run_id)['status'] == 'blocked'
    finally:
        changed.close()


def test_process_lease_and_failed_handover_preserve_evidence(tmp_path):
    with process_lock(tmp_path / 'owner.lock'):
        with pytest.raises((BlockingIOError, OSError)):
            with process_lock(tmp_path / 'owner.lock'):
                pytest.fail('second owner acquired process lease')
    store = Store(tmp_path)
    token = store.claim(0)
    with pytest.raises(OwnershipLost):
        store.claim(0)
    assert store.read('brain', 'state')['generation'] == token[0]
    with pytest.raises(RuntimeError):
        with store.transaction(token) as db:
            brain = store.get(db, 'brain', 'state')
            brain.update(generation=99, owner='incomplete')
            store.put(db, 'brain', 'state', brain)
            raise RuntimeError('crash before atomic handover commit')
    assert store.read('brain', 'state')['generation'] == token[0]


def test_repeated_foreground_ticks_do_not_restart_waiting_worker(tmp_path):
    core = make_core(tmp_path)
    try:
        run_id = start(core)
        with core.store.transaction(core.token) as db:
            core.store.put(db, 'turn', 'pending', {'id': 'pending', 'status': 'pending', 'generation': core.token[0]})
        before = core.store.read('run', run_id)['revision']
        with patch.object(core.pool, 'submit') as submit:
            for _ in range(20):
                core.tick()
            submit.assert_not_called()
        assert core.store.read('run', run_id)['revision'] == before
    finally:
        core.close()


def test_status_capability_renders_evidence_instead_of_model_blocker_claim(tmp_path):
    def wrong_summary(*_args):
        return AssistantResponse(display_text='Budget exhausted.', delivery_mode='text_only',
            state_updates=BoundedStateUpdates(activity_action='report_status'))
    core = make_core(tmp_path, draft=wrong_summary, propose=lambda *_: {'solution.py': 'def should_retry(*args): return False\n'})
    try:
        run_id = start(core, maximum_attempts=2)
        core.runner.run(run_id)
        text = core.input('How did practice go?', 'private', 'report')['result']['assistant_response']['display_text']
        assert '2 attempts' in text and 'feature' in text
        assert 'Budget exhausted' not in text
        assert core.store.read('run', run_id)['status'] == 'needs_checker'
        # An explicit command reaches the same bounded capability without inference.
        with patch.object(core, 'draft', side_effect=AssertionError('status used model')):
            assert core.input('practice status', 'private', 'canonical')['result']['assistant_response']['display_text'] == text
    finally:
        core.close()


def test_focus_and_mood_carry_between_chat_and_activity_changes(tmp_path):
    replies = iter([
        AssistantResponse(display_text='I will keep quiet.', delivery_mode='text_only',
            state_updates=BoundedStateUpdates(attention_focus='focused', emotion='teasing', current_objective='homework')),
        AssistantResponse(display_text='Still here.', delivery_mode='text_only'),
    ])
    core = make_core(tmp_path, draft=lambda *_: next(replies))
    try:
        core.input('I need to focus.', 'private', 'focus')
        core.input('hello', 'private', 'hello')
        status = core.status()
        assert status['focus'] == 'focused' and status['mood'] == 'teasing'
        assert status['working_notes']['current_objective'] == 'homework'
    finally:
        core.close()


def test_recovery_revalidates_without_duplicating_accepted_result(tmp_path):
    core = make_core(tmp_path)
    run_id = start(core)
    core.runner.run(run_id)
    assert len(core.status()['catch_up']) == 1
    core.close()
    recovered = make_core(tmp_path)
    try:
        assert recovered.status()['catch_up'] == []
        assert recovered.status()['activities'][0]['status'] == 'completed'
    finally:
        recovered.close()


def test_model_cannot_claim_dispatch_without_current_permit(tmp_path):
    core = make_core(tmp_path, draft=lambda *_: AssistantResponse(
        display_text='I started practice.', delivery_mode='text_only',
        state_updates=BoundedStateUpdates(activity_action='practice', practice_exercise='retry_budget')))
    try:
        result = core.input('choose some work', 'private', 'unpermitted')['result']['assistant_response']
        assert result['reason_code'] == 'practice_not_started'
        assert core.status()['activities'] == []
        core.control('permit', 'permit', exercise_ids=['retry_budget'])
        result = core.input('choose authorized work', 'private', 'permitted')['result']['assistant_response']
        assert result['reason_code'] == 'practice_queued'
        assert core.status()['last_decision']['accepted_activity'] == 'practice'
    finally:
        core.close()


def test_private_turn_contract_advertises_activity_fields_and_valid_silent_shape():
    from gamma.conversation.turn_contract import StructuredTurnParser, structured_turn_instruction
    legacy = structured_turn_instruction(default_delivery='text_only')
    private = structured_turn_instruction(default_delivery='text_only', coordination=True)
    assert 'activity_action' not in legacy
    assert 'only emotion, active_topic' in legacy
    assert 'only emotion, active_topic' not in private
    assert '"activity_action":"wait"' in private
    assert 'practice_exercise' in private and 'reconsider_after_seconds' in private
    payload = {'action': 'stay_silent', 'requested_delivery': 'silent', 'final_text': '',
               'state_updates': {'activity_action': 'practice', 'practice_exercise': 'message_chunks',
                                 'attention_focus': 'relaxed', 'reconsider_after_seconds': 300}}
    parsed = StructuredTurnParser().parse(json.dumps(payload), legacy_delivery='text_only')
    assert parsed.draft.state_updates.activity_action == 'practice'


def test_coordination_contract_opt_in_requires_effect_free_private_recipient():
    from unittest.mock import Mock
    from gamma.conversation.service import ConversationService
    from gamma.errors import ConversationError
    from gamma.identity.profile import SpeakerProfile
    from gamma.schemas.conversation import RecipientContext
    conversation = ConversationService.__new__(ConversationService)
    private = RecipientContext('owner_private', 'dashboard_monitor', True)
    with pytest.raises(ConversationError):
        conversation.respond('hello', coordination_mode=True, recipient=private)
    with pytest.raises(ConversationError):
        conversation.respond('hello', coordination_mode=True, evaluation_mode=True)
    conversation._identity = Mock()
    conversation._identity.resolve.return_value = SpeakerProfile(name='Owner', trust='owner', is_owner=True)
    conversation._respond = Mock(return_value=AssistantResponse(display_text='safe', delivery_mode='text_only'))
    conversation.respond('hello', coordination_mode=True, evaluation_mode=True, recipient=private)
    assert conversation._respond.call_args.kwargs['coordination_mode'] is True
