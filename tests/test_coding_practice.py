from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gamma.coding.practice import PracticeCheck, PracticeTask, run_practice, record_run, capability_summary


def task(identifier="message", depends_on=()):
    return PracticeTask(
        id=identifier, skill="behavior_verification", instructions="Implement answer returning 42.",
        files={"answer.py": "def answer(): return 0\n", "regression.py": "# protected\n", "feature.py": "# protected\n"},
        editable=("answer.py",),
        checks=(PracticeCheck("regression", "regression", "regression"), PracticeCheck("feature", "feature", "feature")),
        depends_on=depends_on,
    )


def checks(workspace, spec):
    passed = "return 42" in (workspace / "answer.py").read_text()
    return {"name": spec.name, "kind": spec.kind, "exit_code": 0 if passed else 1, "passed": passed,
            "output_tail": "ok" if passed else "AssertionError", "duration_ms": 1.0}


class CodingPracticeTests(unittest.TestCase):
    def test_public_success_failing_independent_check_is_not_collected_or_retried(self):
        import hashlib, json
        calls, samples = [], []
        def propose(*args):
            calls.append(args)
            return {'answer.py':'def answer(): return 42\n'}
        def evaluate(item, files):
            return {'passed':False,'status':'failed','source_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest(), 'secret':'WITHHELD'}
        result=run_practice([task(),task('dependent',('message',))],propose=propose,check=checks,evaluate=evaluate,collect=lambda *args:samples.append(args))
        self.assertEqual(len(calls),1)
        self.assertEqual(samples,[])
        self.assertEqual(result['tasks'][0]['reason'],'independent_evaluation_failed')
        self.assertEqual(result['tasks'][1]['status'],'blocked')
        self.assertNotIn('WITHHELD',str(result))

    def test_independent_success_collects_only_checked_sources_and_mismatch_blocks(self):
        import hashlib, json
        samples=[]
        def evaluate(item,files):
            return {'passed':True,'status':'passed','source_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
        result=run_practice([task()],propose=lambda *_:{'answer.py':'def answer(): return 42\n'},check=checks,evaluate=evaluate,collect=lambda *args:samples.append(args))
        self.assertTrue(result['passed'])
        self.assertEqual(len(samples),1)
        result=run_practice([task()],propose=lambda *_:{'answer.py':'def answer(): return 42\n'},check=checks,evaluate=lambda *_:{'passed':True,'status':'passed','source_sha256':'0'*64})
        self.assertFalse(result['passed'])

    def test_honest_failure_and_checker_request_after_five_attempts(self):
        calls = []
        def propose(item, files, feedback):
            calls.append(feedback)
            return {"answer.py": "def answer(): return 7\n"}
        result = run_practice([task()], propose=propose, check=checks)
        self.assertEqual(len(calls), 5)
        self.assertFalse(result["passed"])
        self.assertEqual(result["tasks"][0]["status"], "needs_checker")
        self.assertEqual(result["tasks"][0]["attempt_count"], 5)
        self.assertIn("AssertionError", str(calls[-1]))

    def test_verified_result_contains_both_kinds_and_source_binding(self):
        result = run_practice([task()], propose=lambda *_: {"answer.py": "def answer(): return 42\n"}, check=checks)
        self.assertTrue(result["passed"])
        item = result["tasks"][0]
        self.assertEqual(item["status"], "verified_practice")
        self.assertEqual({c["kind"] for c in item["checks"]}, {"regression", "feature"})
        self.assertEqual(len(item["source_sha256"]), 64)
        self.assertFalse(result["production_verified"])

    def test_model_cannot_modify_its_tests(self):
        result = run_practice([task()], propose=lambda *_: {"answer.py": "def answer(): return 42\n", "feature.py": "pass\n"}, check=checks, maximum_attempts=1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["tasks"][0]["reason"], "proposal_scope_violation")

    def test_dependent_task_waits_but_independent_task_continues(self):
        seen = []
        def propose(item, *_):
            seen.append(item.id)
            return {"answer.py": "def answer(): return 42\n" if item.id == "independent" else "def answer(): return 7\n"}
        result = run_practice([task("dependent", ("message",)), task(), task("independent")], propose=propose, check=checks, maximum_attempts=1)
        states = {item["id"]: item["status"] for item in result["tasks"]}
        self.assertEqual(states["dependent"], "blocked")
        self.assertEqual(states["independent"], "verified_practice")
        self.assertNotIn("dependent", seen)

    def test_empty_or_one_sided_checks_rejected(self):
        for checks_value in [(), (PracticeCheck("regression", "regression", "regression"),)]:
            with self.assertRaises(ValueError):
                PracticeTask(id="unsafe", skill="verification", instructions="x", files={"a.py": "pass"}, editable=("a.py",), checks=checks_value)

    def test_traversal_and_unknown_dependencies_rejected(self):
        with self.assertRaises(ValueError):
            PracticeTask(id="unsafe", skill="verification", instructions="x", files={"../a.py": "pass"}, editable=("../a.py",), checks=task().checks)
        with self.assertRaises(ValueError):
            run_practice([task(depends_on=("missing",))], propose=lambda *_: {}, check=checks)
        with self.assertRaises(ValueError):
            run_practice([task("a", ("b",)), task("b", ("a",))], propose=lambda *_: {}, check=checks)

    def test_forged_pass_and_missing_result_are_not_evidence(self):
        def forged(workspace, spec):
            return {"name": spec.name, "kind": spec.kind, "exit_code": 1, "passed": True, "output_tail": "", "duration_ms": 0}
        result = run_practice([task()], propose=lambda *_: {"answer.py": "def answer(): return 42\n"}, check=forged, maximum_attempts=1)
        self.assertFalse(result["passed"])

    def test_records_do_not_claim_general_coding_competence(self):
        result = run_practice([task()], propose=lambda *_: {"answer.py": "def answer(): return 42\n"}, check=checks)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = record_run(root, result)
            self.assertTrue(path.is_file())
            summary = capability_summary(root)
            self.assertEqual(summary["run_count"], 1)
            self.assertFalse(summary["production_verified"])
            self.assertIn("behavior_verification", str(summary["skills"]))
            self.assertNotIn("def answer", str(summary))

    def test_verification_infrastructure_failure_does_not_blame_the_model(self):
        calls=[]
        def unavailable(workspace,spec):
            return {'name':spec.name,'kind':spec.kind,'exit_code':1,'passed':False,
                    'infrastructure_error':True,'output_tail':'bwrap unavailable'}
        result=run_practice([task()],propose=lambda *_: calls.append(True),check=unavailable)
        self.assertEqual(result['tasks'][0]['status'],'blocked')
        self.assertEqual(result['tasks'][0]['reason'],'verification_unavailable')
        self.assertEqual(calls,[])

    def test_missing_feature_check_cannot_be_hidden_by_a_regression_pass(self):
        def partial(workspace,spec):
            return {'name':spec.name,'kind':spec.kind,'exit_code':0,'passed':spec.kind=='regression','output_tail':''}
        result=run_practice([task()],propose=lambda *_:{'answer.py':'def answer(): return 42\n'},check=partial,maximum_attempts=1)
        self.assertFalse(result['passed'])

    def test_uncertain_inference_is_not_replayed(self):
        from gamma.coding.learner import PracticeInferenceUnavailable
        calls=[]
        def unavailable(*_):
            calls.append(True)
            raise PracticeInferenceUnavailable('timeout')
        result=run_practice([task()],propose=unavailable,check=checks)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['tasks'][0]['status'],'blocked')


if __name__ == "__main__":
    unittest.main()
