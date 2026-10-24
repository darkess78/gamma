import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from gamma.dashboard import main as dashboard


class CodingDashboardTests(unittest.TestCase):
    def test_page_has_practice_controls_and_post_uses_bounded_defaults(self):
        service = Mock()
        service.start_coding_practice.return_value = {'ok':True}
        with patch('gamma.dashboard.main.is_authenticated', return_value=True), patch('gamma.dashboard.main.get_dashboard_service', return_value=service):
            client = TestClient(dashboard.app)
            response = client.get('/dashboard/improvement')
            self.assertEqual(response.status_code, 200)
            self.assertIn('codingPracticeForm', response.text)
            response = client.post('/api/coding/practice', json={'model':'qwen3.8:27b'})
            self.assertEqual(response.status_code, 200)
            service.start_coding_practice.assert_called_once_with(model='qwen3.8:27b', maximum_attempts=5, budget_seconds=300)
            self.assertEqual(client.post('/api/coding/practice', json={'model':'x','maximum_attempts':100}).status_code, 422)

    def test_unauthenticated_request_does_not_launch_worker(self):
        service = Mock()
        with patch('gamma.dashboard.main.is_authenticated', return_value=False), patch('gamma.dashboard.main.get_dashboard_service', return_value=service):
            response = TestClient(dashboard.app).post('/api/coding/practice', json={'model':'x'})
            self.assertIn(response.status_code, (401,403))
            service.start_coding_practice.assert_not_called()

    def test_owner_can_edit_lessons_and_configure_idle_practice(self):
        service=Mock()
        service.edit_coding_lesson.return_value={'active':False}
        service.set_coding_idle_policy.return_value={'enabled':True}
        with patch('gamma.dashboard.main.is_authenticated',return_value=True),patch('gamma.dashboard.main.get_dashboard_service',return_value=service):
            client=TestClient(dashboard.app)
            self.assertEqual(client.patch('/api/coding/lessons/lesson-feature_verification',json={'text':'Verify the feature.','active':False}).status_code,200)
            self.assertEqual(client.put('/api/coding/idle-policy',json={'enabled':True,'model':'qwen3.8:27b'}).status_code,200)
            service.set_coding_idle_policy.assert_called_once_with({'enabled':True,'model':'qwen3.8:27b','maximum_attempts':5,'budget_seconds':300,'interval_seconds':3600,'maximum_runs_per_day':2})

    def test_lesson_and_policy_mutations_require_authentication(self):
        service=Mock()
        with patch('gamma.dashboard.main.is_authenticated',return_value=False),patch('gamma.dashboard.main.get_dashboard_service',return_value=service):
            client=TestClient(dashboard.app)
            self.assertIn(client.patch('/api/coding/lessons/lesson-x',json={'text':'hello','active':False}).status_code,(401,403))
            self.assertIn(client.put('/api/coding/idle-policy',json={'enabled':True,'model':'x'}).status_code,(401,403))
            service.edit_coding_lesson.assert_not_called()
            service.set_coding_idle_policy.assert_not_called()


if __name__ == '__main__': unittest.main()
