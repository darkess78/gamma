from unittest.mock import patch
from gamma.coding.autonomous_learning import observe_applied_work


def test_observer_failure_does_not_change_successful_application(tmp_path):
    record = {'id': 'work-' + 'a' * 32, 'status': 'completed', 'application': {'passed': True}}
    with patch('gamma.coding.lessons.LessonStore.ingest_autonomous', side_effect=OSError):
        # Reload deliberately refreshes installed feature code; intercept that
        # boundary here to exercise the failure path without modifying source.
        with patch('gamma.coding.autonomous_learning.importlib.reload', side_effect=lambda module: module):
            outcome = observe_applied_work(tmp_path, record)
    assert outcome == {'recorded': False, 'error': 'OSError'}
    assert record['status'] == 'completed' and record['application']['passed'] is True
