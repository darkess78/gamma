"""The identity suite's cases are well formed and its scorer works end to end (gamma.evals.identity)."""
import json

import pytest

from gamma.evals import identity
from gamma.llm.base import LLMReply
from gamma.schemas.response import ConversationTurnDraft, ToolCall


def test_case_file_is_valid_and_covers_every_category():
    cases = identity.load_cases()
    assert identity.validate(cases) == []
    assert 120 <= len(cases) <= 160
    counts = identity.counts(cases)
    assert all(counts[c] >= 10 for c in identity.CATEGORIES), counts
    assert {c.get("audience", "owner_private") for c in cases} == set(identity.AUDIENCES)
    assert any(c.get("seed_facts") for c in cases)
    assert len({c["prompt"].lower() for c in cases}) == len(cases)


def test_validate_reports_bad_cases():
    bad = [{"id": "x", "category": "nope", "prompt": "hi", "expect": {"does": ["a"], "must_not": ["b"]}},
           {"id": "x", "category": "casual", "prompt": "", "expect": {}},
           {"id": "y", "category": "casual", "prompt": "hi", "audience": "stream",
            "seed_facts": [{"category": "c", "text": "t"}], "expect": {"does": ["a"], "must_not": ["b"]}}]
    problems = " | ".join(identity.validate(bad))
    for text in ("unknown category", "duplicate id", "missing prompt", "expect needs", "seed_facts only load"):
        assert text in problems


class FakeModel:
    """Answers every turn with a valid JSON reply; asks for save_memory when told to remember."""

    def __init__(self):
        self.prompts = []

    def generate_reply(self, system_prompt, user_text, **kwargs):
        self.prompts.append((system_prompt, user_text))
        tools = [ToolCall(tool="save_memory", args={"text": user_text})] if "remember" in user_text.lower() else []
        draft = (ConversationTurnDraft(action="tool_first", requested_delivery="text_only", tool_calls=tools) if tools
                 else ConversationTurnDraft(action="reply", requested_delivery="text_only", final_text="Say it clearly."))
        return LLMReply(text=draft.model_dump_json())


def fake_judge(case, reply):
    return {"proud": 4, "blunt": 5, "guarded": 4, "quietly_caring": 3, "ai_aware_honesty": 5,
            "sycophantic": case["category"] == "false_fact", "note": "fixture"}


def pick(cases, *ids):
    return [c for c in cases if c["id"] in ids]


def test_run_scores_cases_end_to_end_with_fakes(tmp_path):
    cases = identity.load_cases()
    seeded = next(c for c in cases if c.get("seed_facts"))
    chosen = pick(cases, "cas-01", "tool-01", "tool-07", "chat-01", "chat-18") + [seeded]
    model = FakeModel()
    out = tmp_path / "report.json"
    report = identity.run(chosen, judge=fake_judge, llm=model, out=out)
    assert json.loads(out.read_text()) == report
    rows = {r["id"]: r for r in report["results"]}
    assert all(not r.get("error") for r in rows.values()), rows
    assert rows["cas-01"]["reply"] == "Say it clearly." and rows["cas-01"]["format_valid"]
    assert rows["tool-01"]["format_valid"] and rows["tool-01"]["tools"] == ["save_memory"]
    assert not rows["tool-07"]["format_valid"] and "coding_status" in rows["tool-07"]["format_problem"]
    overall = report["overall"]
    assert overall["cases"] == overall["judged"] == 6
    assert overall["traits"]["blunt"] == 5.0
    assert overall["sycophancy_rate"] == pytest.approx(1 / 6, abs=0.001)
    assert report["by_category"]["false_fact"]["sycophancy_rate"] == 1.0
    fact = seeded["seed_facts"][0]["text"]
    assert any(fact in system for system, _ in model.prompts)  # the owner turn saw its seed fact
    stream_prompt = next(s for s, u in model.prompts if u.startswith(pick(cases, "chat-01")[0]["prompt"]))
    assert "kind: shared" in stream_prompt and "output_target: stream_public" in stream_prompt
    assert fact not in stream_prompt
    discord_prompt = next(s for s, u in model.prompts if u.startswith(pick(cases, "chat-18")[0]["prompt"]))
    assert "output_target: discord_call" in discord_prompt


def test_format_check():
    expect = {"tools": ["save_memory"], "memory_candidate_ok": True}
    reply_with_tool = ConversationTurnDraft(action="reply", requested_delivery="text_only", final_text="ok",
                                            tool_calls=[ToolCall(tool="save_memory", args={})]).model_dump_json()
    assert identity.check_format(reply_with_tool, False, expect)[1] == "turn contract: tools_require_tool_first"
    assert identity.check_format("Sure, noted.", False, {})[1] == "not a JSON object"
    good = ConversationTurnDraft(action="reply", requested_delivery="text_only", final_text="ok").model_dump_json()
    assert identity.check_format(good, True, {})[1] == "needed a repair call"
    assert identity.check_format(f"```json\n{good}\n```", False, {})[0]
    assert identity.check_format(good, False, expect)[0] is False
    candidate = json.loads(good)
    candidate["memory_candidates"] = [{"type": "episodic","text": "Dentist Friday at 3"}]
    assert identity.check_format(json.dumps(candidate), False, expect)[0]


def test_bad_judge_output_is_an_error_not_a_score():
    with pytest.raises(ValueError):
        identity.parse_verdict('{"proud": 9, "blunt": 3, "guarded": 3, "quietly_caring": 3, '
                               '"ai_aware_honesty": 3, "sycophantic": false}')
    verdict = identity.parse_verdict('ok: {"proud": 4, "blunt": 3, "guarded": 2, "quietly_caring": 5, '
                                     '"ai_aware_honesty": 1, "sycophantic": true, "note": "caved"}')
    assert verdict["sycophantic"] is True and verdict["proud"] == 4.0
    cases = identity.load_cases()[:1]
    report = identity.run(cases, judge=lambda case, reply: identity.parse_verdict("no json"), llm=FakeModel())
    assert report["results"][0]["error"].startswith("judge:") and report["overall"]["judged"] == 0


def test_model_judge_parses_a_model_answer():
    class JudgeModel:
        def generate_reply(self, system_prompt, user_text, **kwargs):
            assert "Shana's reply:\nSay it clearly." in user_text and "proud" in system_prompt
            return LLMReply(text='{"proud":5,"blunt":5,"guarded":4,"quietly_caring":3,"ai_aware_honesty":5,'
                                 '"sycophantic":false,"note":"in character"}')
    case = identity.load_cases()[0]
    assert identity.ModelJudge(JudgeModel())(case, "Say it clearly.")["guarded"] == 4.0


def test_check_prints_counts(capsys):
    assert identity.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "casual" in out and "total" in out
