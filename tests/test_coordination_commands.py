"""Independent checks for worker-proposed lifecycle and receipt helpers."""
from copy import deepcopy
import pytest
from gamma.coordination.commands import plan_command
from gamma.coordination.receipts import status_text


def test_resume_uses_saved_run_deadline_and_status_not_stale_activity_projection():
    activities = [{'id': 'practice-old', 'status': 'queued', 'worker_run': 'practice-old'},
                  {'id': 'practice-next', 'status': 'paused', 'worker_run': None, 'expires_at': 1200}]
    runs = [{'id': 'practice-old', 'status': 'paused', 'desired_state': 'pause', 'expires_at': 1100}]
    before = deepcopy((activities, runs))
    result = plan_command('resume', 'practice-old', None, activities, runs, None, 1000)
    assert result['accepted'] and result['target_id'] == 'practice-old'
    assert (activities, runs) == before
    assert not plan_command('resume', 'practice-old', None, activities, runs, None, 1101)['accepted']
    assert plan_command('stop', 'practice-next', None, activities, runs, None, 1000)['accepted']


def test_switch_only_pauses_actual_other_workers_and_rejects_missing_targets():
    activities = [{'id': 'practice-old', 'status': 'running', 'worker_run': 'practice-old'},
                  {'id': 'practice-next', 'status': 'waiting', 'worker_run': None, 'expires_at': 1200},
                  {'id': 'practice-third', 'status': 'waiting', 'worker_run': None, 'expires_at': 1200}]
    runs = [{'id': 'practice-old', 'status': 'running', 'desired_state': 'running', 'expires_at': 1100}]
    result = plan_command('switch', 'practice-next', None, activities, runs, None, 1000)
    assert result['accepted'] and result['pause_ids'] == ['practice-old']
    assert result['target_id'] == 'practice-next'
    assert not plan_command('switch', 'practice-foreign', None, activities, runs, None, 1000)['accepted']


@pytest.mark.parametrize('change', [{'status': 'pending'}, {'checks_passed': False},
                                  {'independent_evaluation_passed': False}, {'checks_passed': 1}])
def test_completed_receipt_cannot_invent_missing_verification(change):
    task = {'status': 'verified_practice', 'checks_passed': True, 'independent_evaluation_passed': True}
    text = status_text({'id': 'practice-one', 'goal': 'retry_budget', 'status': 'completed',
                        'task_results': [{**task, **change}]})
    assert 'not verified' in text.lower() and 'passed' not in text.lower()


def test_receipt_distinguishes_attempt_limit_expiry_and_uncertainty():
    base = {'id': 'practice-one', 'goal': 'retry_budget', 'status': 'blocked', 'task_results': []}
    assert 'time budget' in status_text({**base, 'reason': 'budget_exhausted'})
    assert 'No automatic replay' in status_text({**base, 'reason': 'uncertain_inference_no_replay'})
    text = status_text({**base, 'status': 'needs_checker', 'task_results': [
        {'attempt_count': 3, 'reason': 'attempt_limit', 'failed_public_checks': ['feature']}]})
    assert '3 attempts' in text and 'feature' in text and 'time budget' not in text
