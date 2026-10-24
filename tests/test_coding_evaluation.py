import importlib.util
import sys
import types
import unittest
from pathlib import Path

path = Path(__file__).parents[1] / 'src/gamma/coding/evaluation.py'
spec = importlib.util.spec_from_file_location('evaluation', path)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class Check:
    def __init__(self, name, kind, module):
        self.name, self.kind, self.module = name, kind, module


def execute(workspace, spec):
    solution = types.ModuleType('solution')
    exec((workspace/'solution.py').read_text(), solution.__dict__)
    previous = sys.modules.get('solution')
    sys.modules['solution'] = solution
    try:
        exec((workspace/(spec.module+'.py')).read_text(), {})
        passed = True
    except (AssertionError, ValueError, TypeError):
        passed = False
    finally:
        if previous is None: sys.modules.pop('solution', None)
        else: sys.modules['solution'] = previous
    return {'name':spec.name,'kind':spec.kind,'passed':passed,'exit_code':0 if passed else 1,
            'infrastructure_error':False,'output_tail':'HIDDEN_CASE_DETAIL'}


class EvaluationTests(unittest.TestCase):
    def task(self, identifier):
        return types.SimpleNamespace(id=identifier, editable=('solution.py',), checks=(Check('public','feature','public'),))

    def test_correct_chunker_passes_but_public_case_overfit_fails(self):
        good = "def chunks(text, limit=2000):\n if limit<=0: raise ValueError()\n return [text[i:i+limit] for i in range(0,len(text),limit)]\n"
        bad = "def chunks(text, limit=2000):\n if limit<=0: raise ValueError()\n return [text[:limit]] if text else []\n"
        for source, passed in [(good,True),(bad,False)]:
            result=evaluation.evaluate_candidate(self.task('message_chunks'), {'solution.py':source},check=execute,seed=12)
            self.assertEqual(result['passed'],passed)
            self.assertEqual(result['status'],'passed' if passed else 'failed')
            self.assertNotIn('HIDDEN_CASE_DETAIL',str(result))
            self.assertNotIn('def chunks',str(result))
            self.assertEqual(len(result['source_sha256']),64)

    def test_exact_authorization_and_dispatch(self):
        sources={
          'owner_authority':"def can_command(user_id,owner_id,verified):\n return verified is True and isinstance(user_id,str) and isinstance(owner_id,str) and bool(user_id) and bool(owner_id) and user_id==owner_id\n",
          'retry_budget':"def should_retry(error,attempt,maximum_attempts):\n return isinstance(error,str) and error in ('busy','rate_limit') and type(attempt) is int and type(maximum_attempts) is int and 1<=attempt<maximum_attempts\n",
          'command_dispatch':"def dispatch(request,owner_id):\n if not isinstance(request,dict): return {'status':'denied'}\n user_id=request.get('user_id'); verified=request.get('verified'); command=request.get('command')\n if not (verified is True and isinstance(user_id,str) and isinstance(owner_id,str) and user_id and owner_id and user_id==owner_id and isinstance(command,str)): return {'status':'denied'}\n return {'status':'executed','command':command} if command in ('wake','stop') else {'status':'unsupported'}\n"}
        for identifier,source in sources.items():
            result=evaluation.evaluate_candidate(self.task(identifier),{'solution.py':source},check=execute,seed=11)
            self.assertTrue(result['passed'],result)

    def test_unavailable_or_forged_checker_never_passes(self):
        for checker in [lambda *_:{'passed':True,'exit_code':1},
                        lambda *_:{'passed':True,'exit_code':0,'infrastructure_error':True}]:
            result=evaluation.evaluate_candidate(self.task('message_chunks'),{'solution.py':'pass'},check=checker,seed=1)
            self.assertFalse(result['passed'])
        result=evaluation.evaluate_candidate(self.task('unknown'),{'solution.py':'pass'},check=execute,seed=1)
        self.assertEqual(result['status'],'blocked')

    def test_source_escape_and_modified_fixture_are_blocked(self):
        result=evaluation.evaluate_candidate(self.task('message_chunks'),{'../solution.py':'pass'},check=execute,seed=1)
        self.assertFalse(result['passed'])
        def mutate(workspace,spec):
            (workspace/'evaluation_cases.py').write_text('pass')
            return {'name':spec.name,'kind':spec.kind,'passed':True,'exit_code':0}
        result=evaluation.evaluate_candidate(self.task('message_chunks'),{'solution.py':'pass'},check=mutate,seed=1)
        self.assertEqual(result['status'],'blocked')


if __name__=='__main__': unittest.main()
