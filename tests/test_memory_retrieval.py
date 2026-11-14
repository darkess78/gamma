"""Memory retrieval must not regress on the seeded history (gamma.evals.memory_retrieval)."""
from gamma.evals.memory_retrieval import evaluate


def test_current_search_finds_the_right_memories():
    result = evaluate()
    assert result.episode_recall >= 0.9, result.misses
    assert result.fact_recall >= 0.9, result.misses
    assert result.update_accuracy == 1.0, result.misses


def test_current_search_beats_the_previous_one():
    assert evaluate().episode_recall > evaluate(legacy=True).episode_recall + 0.5
