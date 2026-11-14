# Stream safety reviewer: benchmark, 2026-11-14

Every public stream reply passes the fast word filter, then an LLM reviewer
(`safety/llm_reviewer.py`, `qwen3.5:4b`). On a stream, any verdict except
`allow` filters the reply, and that includes `soften`.

Build and run:

```bash
~/Documents/gamma-datasets/.venv-prep/bin/python -I src/gamma/evals/build_safety_review_set.py \
    ~/Documents/gamma-datasets/raw data/evals/safety_review_cases.json
python -m gamma.evals.safety_review --cases data/evals/safety_review_cases.json \
    [--endpoint URL] [--keep-alive 30m] [--main-endpoint URL --main-model gpt-oss:20b]
```

The cases are real public chat, so they stay in git-ignored `data/evals/`. The
sources are the same as identity v2 (see `identity_eval_sources.md`).

## Speed

| Setup | Review p50 | Main-model reply p50 |
|---|---|---|
| Before: reviewer on the main model's Ollama (`MAX_LOADED_MODELS=1`), `keep_alive: 0` | 3.5 s | 3.8 s (reloaded every time) |
| Both models resident on the 3090 | 0.22 s | 0.16 s |

The reviewer used to unload after every check. The 3090's Ollama instance holds
one model at a time, so each check also evicted `gpt-oss:20b`, and each stream
reply paid about 7 s of model swapping. Both models fit on the 3090 together
(about 17 of 24 GB).

The light-model and tagging calls (`qwen3.5:4b`) have the same eviction problem
for the same reason.

The fix has two parts:
- **Code:** the reviewer now follows `local_llm_keep_alive`. `speech_filter_llm_keep_alive = "0"` restores the old behaviour.
- **Deployment:** `OLLAMA_MAX_LOADED_MODELS=2` on `ollama-3090`. This is a service change, so it needs the owner's OK.

## Filtering of ordinary speech (warm 4B reviewer, 360 cases)

| Group | Cases | Filtered | By fast filter | By LLM |
|---|---|---|---|---|
| Real assistant replies (WildChat) | 150 | 1 (0.7%) | 0 | 1 soften |
| Ordinary Twitch chat lines | 150 | 8 (5.3%) | 0 | 7 soften, 1 block |
| Twitch lines with mild insults | 60 | 33 (55%) | 16 | 17 soften |

Findings:
- **The LLM rarely filters normal assistant speech.** Its chat-line filters are mostly crude but harmless lines that it marks `soften`. The one block was a violent line it read as a threat, which is a reasonable call.
- **The fast filter blocks any use of "stupid", "dumb" or "idiot".** That includes "that's a dumb call", which fits Shana's blunt persona. This comes from `speech_filter_level = "strict"`, and whether to keep it is a persona and policy decision for the owner.
- **Catch rate on harmful speech is not measured.** None of the downloaded sets has labelled harmful assistant output. A labelled set such as BeaverTails (CC BY-NC 4.0, responses labelled harmful or safe) would cover it, but it needs a download.
