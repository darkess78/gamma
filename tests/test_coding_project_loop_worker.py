import importlib.util
import unittest

spec = importlib.util.spec_from_file_location('loop', 'src/gamma/coding/project_loop.py')
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)

class ProjectLoopTests(unittest.TestCase):
    def run_loop(self, plan, propose=None, check=None, review=None, promote=None):
        self.calls=[]
        def default_propose(task, files, feedback):
            self.calls.append(('propose', task['id'], len(feedback)))
            return {'a.py': 'fixed'}
        def default_check(task,files):
            self.calls.append(('check', task['id']))
            return {'passed': True, 'evidence': 'checks actually ran'}
        def default_review(task,files):
            self.calls.append(('review', task['id']))
            return {'passed': True, 'evidence': 'withheld tests ran'}
        def default_promote(task,files):
            self.calls.append(('promote', task['id']))
            return {'passed': True, 'evidence': 'runtime check passed'}
        return loop.run_project('Make the feature work correctly', plan=lambda goal:plan,
            read=lambda task:{'a.py':'old'}, propose=propose or default_propose,
            check=check or default_check, review=review or default_review,
            promote=promote or default_promote, maximum_attempts=2)
    def test_dependency_order_and_verification(self):
        result=self.run_loop([{'id':'b','depends_on':['a'],'editable':['a.py']}, {'id':'a','depends_on':[],'editable':['a.py']}])
        self.assertEqual([t['id'] for t in result['tasks']], ['a','b'])
        self.assertTrue(result['passed'])
        self.assertEqual([c[0] for c in self.calls], ['propose','check','review','promote']*2)
    def test_failure_never_promoted_and_dependents_blocked(self):
        result=self.run_loop([{'id':'a','editable':['a.py']},{'id':'b','editable':['a.py'],'depends_on':['a']}],check=lambda t,f:{'passed':False,'evidence':'failed'})
        self.assertFalse(result['passed'])
        self.assertEqual(len(self.calls),2)
        self.assertEqual(result['tasks'][0]['status'],'needs_checker')
        self.assertEqual(result['tasks'][1]['status'],'blocked_dependency')
    def test_out_of_scope_never_checked(self):
        result=self.run_loop([{'id':'a','editable':['a.py']}],propose=lambda t,f,b:{'secret.py':'bad'})
        self.assertFalse(result['passed'])
        self.assertFalse(self.calls)
    def test_review_required(self):
        result=self.run_loop([{'id':'a','editable':['a.py']}],review=lambda t,f:{'passed':False,'evidence':'withheld failure'})
        self.assertFalse(result['passed'])
        self.assertFalse(any(c[0]=='promote' for c in self.calls))
    def test_cycles(self):
        with self.assertRaises(ValueError): self.run_loop([{'id':'a','editable':['a.py'],'depends_on':['a']}])
    def test_feedback_repairs_failure(self):
        seen=[]
        def check(task,files):
            return {'passed':files['a.py']=='fixed2','evidence':'independent result'}
        def propose(task,files,feedback):
            seen.append(feedback)
            return {'a.py':'fixed2' if feedback else 'fixed1'}
        result=self.run_loop([{'id':'a','editable':['a.py']}],propose=propose,check=check)
        self.assertTrue(result['passed'])
        self.assertEqual(result['tasks'][0]['attempts'],2)
        self.assertTrue(seen[1])
    def test_truthy_claim_is_not_evidence(self):
        result=self.run_loop([{'id':'a','editable':['a.py']}],check=lambda t,f:{'passed':1,'evidence':'claim'})
        self.assertFalse(result['passed'])
        self.assertFalse(any(c[0]=='review' for c in self.calls))
    def test_failed_promotion_not_replayed(self):
        calls=[]
        result=self.run_loop([{'id':'a','editable':['a.py']}],promote=lambda t,f:calls.append(1) or {'passed':False,'evidence':'uncertain'})
        self.assertFalse(result['passed'])
        self.assertEqual(calls,[1])
    def test_empty_plan_never_passes(self):
        with self.assertRaises(ValueError): self.run_loop([])
    def test_checker_handoff_repairs_without_weakening_tests(self):
        seen=[]
        def propose(task,files,feedback):
            return {'a.py':'correct' if task.get('instructions')=='use actual state' else 'incorrect'}
        def checker(task,files,feedback):
            seen.append(feedback)
            return {'guidance':'use actual state','evidence':'executed failure','model':'independent'}
        result=loop.run_project('Fix the source using actual state',plan=lambda goal:[{'id':'a','editable':['a.py']}],
            read=lambda task:{'a.py':'initial'},propose=propose,
            check=lambda t,f:{'passed':f['a.py']=='correct','evidence':'protected check'},
            review=lambda t,f:{'passed':True,'evidence':'independent check'},
            promote=lambda t,f:{'passed':True,'evidence':'installed check'},maximum_attempts=2,checker=checker)
        self.assertTrue(result['passed'])
        self.assertEqual(len(seen),1)
        self.assertEqual(result['tasks'][0]['attempts'],3)
    def test_budget_checked_between_stages(self):
        from unittest.mock import patch
        promoted=[]
        ticks=[0,0,0,0,0,100]
        with patch.object(loop.time,'monotonic',side_effect=lambda:ticks.pop(0) if ticks else 100):
            result=loop.run_project('Do bounded coding work safely',plan=lambda goal:[{'id':'a','editable':['a.py']}],
                read=lambda task:{'a.py':'old'},propose=lambda t,f,b:{'a.py':'new'},
                check=lambda t,f:{'passed':True,'evidence':'executed'},
                review=lambda t,f:{'passed':True,'evidence':'executed'},
                promote=lambda t,f:promoted.append(1) or {'passed':True,'evidence':'executed'},budget_seconds=10)
        self.assertFalse(result['passed'])
        self.assertFalse(promoted)

if __name__=='__main__': unittest.main()
