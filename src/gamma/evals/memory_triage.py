"""How often the memory triage gate agrees with the owner's labelled dataset.

    python -m gamma.evals.memory_triage

Reads research/cs4700_memory_triage/data/memory_triage_primary_dataset.csv
(400 keep/skip labels) and reports agreement, and precision/recall for "keep".
Also scores keep-everything, which is what Gamma did before the gate existed.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..memory.triage import triage

DATASET = Path(__file__).resolve().parents[3] / "research/cs4700_memory_triage/data/memory_triage_primary_dataset.csv"


@dataclass
class Score:
    agreement: float
    keep_precision: float
    keep_recall: float
    disagreements: Counter


def score(predict, rows) -> Score:
    tp = fp = fn = agree = 0
    misses: Counter = Counter()
    for row in rows:
        gold = row["label"] == "keep"
        guess = predict(row["candidate_text"])
        agree += gold == guess
        tp += gold and guess
        fp += guess and not gold
        fn += gold and not guess
        if gold != guess:
            misses[row["rationale_code"]] += 1
    return Score(agree / len(rows), tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0, misses)


def load(path: Path = DATASET) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [r for r in csv.DictReader(handle) if r.get("primary_eligible", "true") == "true"]


def main() -> int:
    rows = load()
    for label, predict in (("keep everything", lambda _t: True), ("triage gate", lambda t: triage(t).keep)):
        s = score(predict, rows)
        print(f"{label:16} agreement {s.agreement:.0%}  keep precision {s.keep_precision:.0%}  keep recall {s.keep_recall:.0%}")
        if s.disagreements:
            print("    disagreements by label reason:", dict(s.disagreements.most_common(6)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
