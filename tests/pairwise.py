"""All-pairs test matrices.

Some tests vary several independent settings (speaker x recipient x path x
mode). Running every combination multiplies fast; almost every real bug needs
only one setting, or a particular pair, to show up. ``pairwise`` picks a small
set of combinations in which every pair of values from any two settings still
appears at least once.

    @pytest.mark.parametrize(*pairwise(("target", TARGETS), ("fast_mode", [True, False]),
                                       ("verdict", VERDICTS)))

A setting may name several arguments ("field,value") with tuple values, as in
``pytest.mark.parametrize``. ``include`` lists combinations that must run
(as dicts of setting name to value), for three-way cases that matter on their
own. The choice is deterministic, so test ids are stable.
"""
from __future__ import annotations

from itertools import combinations, product
from typing import Any, Sequence


def pairwise(*dims: tuple[str, Sequence[Any]], include: Sequence[dict[str, Any]] = ()) -> tuple[str, list[tuple]]:
    names = [name for name, _ in dims]
    values = [list(vals) for _, vals in dims]
    if len(dims) < 3:
        rows = list(product(*values))
    else:
        uncovered = {(i, a, j, b) for i, j in combinations(range(len(dims)), 2)
                     for a in range(len(values[i])) for b in range(len(values[j]))}
        candidates = list(product(*(range(len(v)) for v in values)))
        chosen: list[tuple[int, ...]] = []
        for required in include:
            row = tuple(next(k for k, v in enumerate(values[d]) if v is required[name] or v == required[name])
                        for d, name in enumerate(names))
            chosen.append(row)
            uncovered -= {(i, row[i], j, row[j]) for i, j in combinations(range(len(row)), 2)}
        while uncovered:
            best = max(candidates, key=lambda c: sum((i, c[i], j, c[j]) in uncovered
                                                     for i, j in combinations(range(len(c)), 2)))
            chosen.append(best)
            uncovered -= {(i, best[i], j, best[j]) for i, j in combinations(range(len(best)), 2)}
        rows = [tuple(values[d][k] for d, k in enumerate(c)) for c in chosen]
    flat_rows = []
    for row in rows:
        flat: list[Any] = []
        for name, value in zip(names, row):
            if "," in name:
                flat.extend(value)
            else:
                flat.append(value)
        flat_rows.append(tuple(flat))
    return ",".join(names), flat_rows
