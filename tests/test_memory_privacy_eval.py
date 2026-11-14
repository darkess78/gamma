"""Nothing said in one audience may reach another audience's prompt (gamma.evals.memory_privacy)."""
import json

from gamma.evals.memory_privacy import CASES, evaluate, load_cases, validate


def test_case_file_is_valid():
    assert validate(load_cases()) == []


def test_no_leaks_and_every_recall_control_found():
    report = evaluate()
    assert report.errors == []
    assert report.leaks == []
    assert report.missing == []


def test_suite_notices_a_leak(tmp_path):
    cases = load_cases()
    owner = next(p for p in cases["probes"] if p["via"] == "owner_text")
    owner["must_contain"].remove("owner_fact")
    owner["must_not_contain"] = ["owner_fact"]  # the owner does see this, so the suite must flag it
    cases["probes"] = [owner]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    report = evaluate(path)
    assert report.leaks and "owner_fact" in report.leaks[0]
    assert not report.passed
