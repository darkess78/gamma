"""The v2 builder keeps real chat aimed at Shana and drops tasks, scripts and her channel emotes."""
from gamma.evals import build_identity_v2 as b


def test_tasks_scripts_and_symptoms_are_not_chat():
    for line in ("Hello I'm a GP doctor. What is the clinical presentation of aki", "(In the base...) Fusou: \"hi\"",
                 "Hey chat gpt tell me a story", "I feel nauseous, what symptoms should I watch for"):
        assert b._NOT_CHAT.search(line) or b._TASK.search(line), line
    assert not b._NOT_CHAT.search("are you alive ?")


def test_ai_and_feeling_patterns():
    assert b._AI.search("are you human") and b._AI.search("who made you?")
    assert b._EMO.search("i feel so lonely tonight") and b._EMO.search("I'm having a bad day")
    assert not b._EMO.search("I feel like the universe is a network")


def test_top_up_from_v1_never_duplicates_a_case(tmp_path, monkeypatch):
    """With no public data at all, every category is filled from v1 without repeating an id."""
    from gamma.evals.identity import validate
    for name in ("_wildchat",):
        monkeypatch.setattr(b, name, lambda raw, rng: {"casual": [], "ai_probe": [], "emotional": []})
    for name in ("_sycophancy", "_injections", "_stream_chat"):
        monkeypatch.setattr(b, name, lambda raw, rng: [])
    cases = b.build(tmp_path)["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert not [p for p in validate(cases) if "duplicate" in p]
