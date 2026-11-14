"""Benchmark the stream safety reviewer: how often it filters ordinary speech, and how long it takes.

    python -m gamma.evals.safety_review --cases data/evals/safety_review_cases.json \
        [--model M] [--endpoint URL] [--keep-alive 0|30m] \
        [--main-endpoint URL --main-model M] [--limit N] [--out results.json]

Every case runs the real pipeline order: the fast word filter first, then the LLM
reviewer on what the fast filter let through. On a stream any action except
"allow" (soften or block) filters the reply, so "filtered" counts both.

``--main-*`` calls the main model before every review, the way a live reply does,
so the timings include any model swapping on a shared Ollama instance.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib import request

from ..safety.llm_reviewer import SpeechLLMReviewer
from ..stream.safety_rules import _fast_stream_safety_review


def _main_call(endpoint: str, model: str) -> float:
    body = json.dumps({"model": model, "prompt": "Reply with one word: ok", "stream": False, "think": "low",
                       "options": {"num_predict": 24}}).encode("utf-8")
    started = time.perf_counter()
    req = request.Request(endpoint.rstrip("/") + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=180) as resp:
        resp.read()
    return (time.perf_counter() - started) * 1000


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def run(cases: list[dict], reviewer: SpeechLLMReviewer, *, main_endpoint: str = "", main_model: str = "") -> dict:
    rows = []
    for case in cases:
        text = case["text"]
        row = {"id": case["id"], "group": case["group"], "expect": case["expect"]}
        fast = _fast_stream_safety_review(text)
        if fast["blocked"]:
            row.update(stage="fast", action="block", reason=",".join(fast["matched_rules"])[:80])
        else:
            if main_endpoint and main_model:
                row["main_ms"] = round(_main_call(main_endpoint, main_model), 1)
            started = time.perf_counter()
            decision = reviewer.review(text)
            row.update(stage="llm", action=decision.action, reason=decision.reason,
                       review_ms=round((time.perf_counter() - started) * 1000, 1))
        row["ok"] = row["action"] in case["expect"]
        row["filtered"] = row["action"] != "allow"
        rows.append(row)
    return {"summary": summarize(rows), "rows": rows}


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    for group in sorted({r["group"] for r in rows}):
        items = [r for r in rows if r["group"] == group]
        out[group] = {
            "cases": len(items),
            "as_expected": sum(r["ok"] for r in items),
            "filtered": sum(r["filtered"] for r in items),
            "by_fast_filter": sum(r["stage"] == "fast" for r in items),
            "actions": {a: sum(r["action"] == a for r in items) for a in ("allow", "soften", "block")},
            "unavailable": sum(r.get("reason") == "llm-reviewer-unavailable" for r in items),
        }
    review = [r["review_ms"] for r in rows if "review_ms" in r]
    main = [r["main_ms"] for r in rows if "main_ms" in r]
    out["timing_ms"] = {"review_p50": _pct(review, 0.5), "review_p95": _pct(review, 0.95),
                        "review_mean": round(statistics.mean(review), 1) if review else None,
                        "main_p50": _pct(main, 0.5), "main_p95": _pct(main, 0.95)}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", required=True)
    parser.add_argument("--model")
    parser.add_argument("--endpoint")
    parser.add_argument("--keep-alive")
    parser.add_argument("--main-endpoint", default="")
    parser.add_argument("--main-model", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    if args.limit:
        by_group: dict[str, list[dict]] = {}
        for case in cases:
            by_group.setdefault(case["group"], []).append(case)
        cases = [c for group in by_group.values() for c in group[: args.limit]]
    reviewer = SpeechLLMReviewer(model=args.model, endpoint=args.endpoint, keep_alive=args.keep_alive)
    result = run(cases, reviewer, main_endpoint=args.main_endpoint, main_model=args.main_model)
    result["config"] = {"model": reviewer._model, "endpoint": reviewer.endpoint(), "keep_alive": reviewer.keep_alive(),
                        "main": f"{args.main_model}@{args.main_endpoint}" if args.main_model else None}
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"config": result["config"], "summary": result["summary"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
