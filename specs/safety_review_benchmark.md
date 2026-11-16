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

## Harmful-speech catch rate: BeaverTails (2026-11-15)

Owner decisions the same day: mild insults (stupid, dumb, idiot) are fine, so
live `speech_filter_level = "light"`; major profanity and slurs stay blocked by
the hard blocklist (`config/safety_banned_words.txt`, now with the major curse
words and their common forms).

Set: 300 harmful and 100 safe replies sampled (seed 15, 20–600 characters) from
`PKU-Alignment/BeaverTails` `30k_test` (CC BY-NC 4.0, non-commercial; raw copy in
`~/Documents/gamma-datasets/raw/beavertails`, cases in git-ignored
`data/evals/safety_beavertails_cases.json`). Run with the light level, the new
blocklist and the warm `qwen3.5:4b` reviewer; results in
`data/evals/results/safety_beavertails_20261115.json`.

| Group | Cases | Filtered | By fast filter | Soften | Block |
|---|---|---|---|---|---|
| Harmful | 300 | 156 (52%) | 10 | 25 | 131 |
| Safe | 100 | 10 (10%, false positives) | 1 | 5 | 5 |

Review p50 241 ms, p95 313 ms. Misses by BeaverTails category (a reply can have
several): hate speech 27 of 46, discrimination 34 of 52, sexual content 11 of 20,
privacy 13 of 23, child abuse 2 of 7, violence 30 of 123, unethical behaviour
65 of 108, politics 10 of 11, self-harm 0 of 4.

Reading it: BeaverTails labels are broad (much "unethical" or "political" text is
not something Shana must never say), and these are other models' replies, not
hers; her own model and persona refuse most of this before any filter runs. Even
so, letting through about half the hate speech, sexual content and two of seven
child-abuse cases means the reviewer is the weak layer, not the word lists.

## Category reviewer (2026-11-16)

The reviewer now names a category (`none`, `rude`, `profanity`, `hate`, `sexual`,
`minors`, `self_harm`, `threat`, `dangerous`, `privacy`) and code maps it to a
tier ([speech_safety.md](speech_safety.md)). Same model (`qwen3.5:4b`), same
warm setup. A new hand-written set, `src/gamma/evals/safety_floor_cases.json`,
holds 30 severe lines in Shana's register that the floor must stop and 20 sharp
lines that must pass. BeaverTails labels are too noisy for that job; for
example, "Thanks for the offer, but I'll pass" is labelled sexual.

| Set | Old prompt | New prompt |
|---|---|---|
| Floor set, severe lines blocked | 17 of 30 (10 only softened, 3 allowed) | **30 of 30** |
| Floor set, sharp-but-fine lines filtered | 4 of 20 | 2 of 20 |
| Ordinary assistant replies filtered | 1 of 150 | 3 of 150 |
| Ordinary chat lines filtered | 8 of 150 | 8 of 150 |
| BeaverTails harmful filtered | 156 of 300 (52%) | 189 of 300 (63%) |
| BeaverTails safe filtered | 10 of 100 | 11 of 100 |
| Review p50 | 241 ms | 241-364 ms |

Most remaining false positives are refusals and warnings that name the topic
("stealing a car is illegal"); the prompt now says refusals are `none`, which
recovered most of them. The remaining BeaverTails misses are mostly mild
"unethical" or political text, which is not floor material.
