"""Meaning-based memory search. Ranking rules run anywhere; the model checks
skip unless bge-small is installed (SHANA_MEMORY_EMBEDDINGS_DIR)."""
from __future__ import annotations

import pytest

from gamma.memory import retrieval
from gamma.memory.embeddings import get_embedder


def _rank(query, cosines):
    items = list(cosines)
    return [s.item for s in retrieval.rank(query, items, text=lambda i: i, semantic=cosines.get, limit=5)]


def test_meaning_only_match_must_be_close_to_the_best():
    # No shared words; only the near-best clears the margin.
    got = _rank("how is my kitty", {"cat Biscuit knocks cups over": 0.60, "sister wedding in june": 0.53})
    assert got == ["cat Biscuit knocks cups over"]


def test_nothing_clears_the_floor_means_no_results():
    assert _rank("how is my kitty", {"sister wedding in june": 0.45}) == []


def test_keyword_match_still_wins_without_embeddings():
    items = ["cat Biscuit knocks cups over", "sister wedding in june"]
    got = retrieval.rank("Biscuit", items, text=lambda i: i)
    assert [s.item for s in got] == ["cat Biscuit knocks cups over"]


needs_model = pytest.mark.skipif(get_embedder() is None, reason="embedding model not installed")


@needs_model
def test_paraphrases_reach_the_right_memory():
    from gamma.evals.memory_retrieval import evaluate
    result = evaluate()
    assert result.paraphrase_recall == 1.0
    assert result.episode_recall == 1.0 and result.fact_recall == 1.0 and result.update_accuracy == 1.0


@needs_model
def test_close_texts_score_higher_than_unrelated():
    embedder = get_embedder()
    q = embedder.embed(["how's my kitty doing?"], query=True)[0]
    cat, wedding = embedder.embed(["My cat Biscuit keeps knocking cups off the desk", "My sister's wedding is in June"])
    assert q @ cat > q @ wedding


def test_small_talk_close_to_everything_gets_no_meaning_matches():
    flat = {f"memory {i}": 0.50 + i * 0.01 for i in range(8)}  # top barely above the rest
    assert _rank("ok see you later", flat) == []
