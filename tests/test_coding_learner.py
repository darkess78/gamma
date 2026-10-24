import unittest
from unittest.mock import patch

from gamma.coding.learner import LocalPracticeLearner


class CodingLearnerTests(unittest.TestCase):
    def test_hosted_destinations_and_credentials_rejected(self):
        for endpoint in ['https://127.0.0.1:11434', 'http://8.8.8.8:11434', 'http://user:pass@localhost:11434', 'http://localhost:11434/v1', 'http://example.com', 'http://0.0.0.0']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LocalPracticeLearner(endpoint=endpoint, model='local-model')

    def test_busy_endpoint_defers_without_generation(self):
        learner = LocalPracticeLearner(endpoint='http://127.0.0.1:11434', model='local-model')
        with patch.object(learner, '_json', return_value={'models':[{'name':'conversation'}]}) as call:
            with self.assertRaises(RuntimeError): learner.require_idle()
            call.assert_called_once_with('/api/ps')

    def test_truncated_output_rejected(self):
        learner = LocalPracticeLearner(endpoint='http://127.0.0.1:11434', model='local-model')
        from gamma.coding.curriculum import discord_foundations
        with patch.object(learner, 'require_idle'), patch.object(learner, '_json', return_value={'done':True,'done_reason':'length','message':{'content':'{}'}}):
            with self.assertRaises(ValueError): learner(discord_foundations()[0], {'solution.py':'pass'}, [])


if __name__ == '__main__': unittest.main()
