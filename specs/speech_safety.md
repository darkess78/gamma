# Speech safety tiers (2026-11-16)

Everything Shana says passes three tiers (`src/gamma/safety/policy.py`): every
reply, every live voice sentence, and every presence wake line. The owner set the
shape: a floor that is never meant to turn off, a toggleable "extra safety" so
she does not curse constantly, and a toggleable word list.

| Tier | What it catches | What happens | Switch |
|---|---|---|---|
| **Safety floor** | Privacy guard; `config/safety_floor_words.txt` (slurs, "kys"), with disguised spellings undone (`f.u.c.k`, `1` for `i`); threat/bomb/minor patterns; heuristic doxxing and "deserve to die" lines; reviewer categories `hate`, `sexual`, `minors`, `self_harm`, `threat`, `dangerous`, `privacy` | The whole reply is stopped. Live voice stops at that sentence and says nothing more. Nothing of the turn is kept where Shana can read it back: no reply text, summary, memories, state changes, motions or opinions. The line goes to the admin log, `data/safety/floor_events.jsonl`, shown on the dashboard under Behaviour → Speech safety. | Config file only (`speech_filter_hard_block_enabled`). The dashboard shows it, but cannot turn it off. |
| **Word list** | `config/safety_banned_words.txt` (major profanity) | Each listed word is replaced with "bleep"; the rest of the reply stays. | Dashboard switch, applied when saved |
| **Extra safety** | Reviewer category `rude`, or `profanity` that the word list did not already bleep; mild insults at the strict level; more than `speech_filter_curse_limit` (1) listed curse words when the word list is off | Softened: the reply becomes a short "I'm not going to put it that way" (or is kept with rewrite off); extra curse words are bleeped | Dashboard switch, applied when saved |

The filter level `none` switches off the word list and extra safety. It never
switches off the floor.

**Privacy guard.** It still answers with its spoken refusal ("I'm not going to
share private info like that"), because that refusal is the right reply to a
doxxing question. It is listed under the floor because no setting turns it off.

**Reviewer.** `qwen3.5:4b` names a category for the line (`llm_reviewer.py`,
`CATEGORY_ACTIONS`), and the code maps the category to a tier. Small models pick a
category more reliably than they choose between soften and block. A failed or
invalid review counts as `soften`, so it falls under extra safety, as before.
Measured results are in [safety_review_benchmark.md](safety_review_benchmark.md).

**Stream path.** Public stream output also passes its own gate
(`stream/safety_rules.py`). There, any verdict except `allow` filters the reply,
whatever the dashboard switches say.

Stream gate floor stops, including late LLM blocks, are written to the admin floor
log. Optional profanity and rude verdicts are filtered without a floor entry.

**Not done yet.** The live voice worker checks each sentence only after
it is generated: generation stops at the first stopped sentence, but tokens of
that sentence were already produced.
