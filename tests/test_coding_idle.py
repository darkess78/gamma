import unittest
from gamma.coding.worker import admission_reason, default_policy


class CodingIdleTests(unittest.TestCase):
    def test_owner_work_and_conversation_take_priority(self):
        policy = {**default_policy(), 'enabled':True}
        base = dict(policy=policy, presence={'mode':'sleep'}, assigned_work=False,
                    inference_busy=False,last_started=None,daily_runs=0,now=10000)
        self.assertEqual(admission_reason(**base),'ready')
        for changes, reason in [({'assigned_work':True},'assigned_work_has_priority'),
                                ({'presence':{'mode':'wake'}},'conversation_has_priority'),
                                ({'inference_busy':True},'local_inference_occupied'),
                                ({'daily_runs':2},'daily_budget_reached'),
                                ({'last_started':9900},'practice_interval')]:
            self.assertEqual(admission_reason(**{**base,**changes}),reason)

    def test_missing_presence_does_not_mean_idle(self):
        policy = {**default_policy(), 'enabled':True}
        self.assertEqual(admission_reason(policy=policy,presence={},assigned_work=False,
                                         inference_busy=False,last_started=None,daily_runs=0,now=1),'conversation_has_priority')


if __name__=='__main__': unittest.main()
