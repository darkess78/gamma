# Fine-tuning Shana's brain: plan (2026-11-14)

This expands W9 in `shana_plan.md`. Status: **planned, not started**. Every
download, install, deletion and live switch below needs the owner's OK.

## Goal and limits

Train a local model so that Shana's voice holds without a long character
prompt, and so that it fixes what prompt edits could not
(`persona/core.md` A/B, 2026-11-14):
- She still repeats a dictated line about 3 times in 20 scripting attempts.
- Some refusals sound like a generic assistant ("I'm sorry, but I can't comply").
- She copies the example lines word for word.

What stays in the prompt regardless: current facts, memories, who she is
talking to, privacy rules, the structured turn format, and the safety
boundary. The fine-tune shortens the character section; it does not replace
the prompt.

## Base model

| Candidate | Why | Cost |
|---|---|---|
| **Qwen3.5-9B** (dense) | Pilot. QLoRA uses about 10 GB of VRAM, a run takes an hour or two, so it's cheap to iterate. Same family as the `qwen3.5:4b` helper. | About 6 GB for 4-bit weights, plus about 6 GB for the Ollama build |
| **Qwen3.8-27B** (dense) | The real candidate. Already installed in Ollama (`qwen3.8:27b`, Q4_K_M, 17 GB). QLoRA fits in about 22 GB, which is tight. Served at Q4 it would replace gpt-oss on the 3090; it fits beside the 3.8 GB reviewer. | About 17 GB for 4-bit training weights, plus about 17 GB for the Ollama build |
| gpt-oss:20b (current) | Not recommended. It is a mixture-of-experts model shipped in MXFP4, so converting a trained version back for Ollama is fragile, and short in-character training risks its reasoning. | — |

Decide between them with numbers (step 0), not reputation.

**Helper model (checked 2026-11-14).** The light, tagging and safety roles use
`qwen3.5:4b`. Ollama has no Qwen3.8 or Qwen3.6 below 27B, so there is no newer
small Qwen to upgrade to. `gemma4:e4b`, the repo's default helper, is a 9.5 GB
download. Beside gpt-oss (12.9 GB) it would leave the 3090 with almost no
headroom, so the two would evict each other again. Keep `qwen3.5:4b` unless the
main model changes.

## Steps

**0. Baseline, no download (about 1 hour of 3090 time).**
- Run identity v2 on the prompted `qwen3.8:27b` and compare it with the
  prompted gpt-oss runs from today.
- If the 27B is already clearly better in character, the cheapest win is to
  switch the main model with no training at all. That would mean re-measuring
  voice latency, since a 27B dense model is slower per token than gpt-oss.

Result (2026-11-14, gpt-oss:20b judge, persona cc8182e, 140 cases):

| Run | proud | blunt | guarded | caring | AI honesty | valid format | median length |
|---|---|---|---|---|---|---|---|
| gpt-oss:20b | 3.61 | 4.92 | 4.76 | 2.02 | 4.12 | 89% | 50 chars |
| qwen3.8:27b, thinking on | 4.12 | 4.86 | 4.71 | 2.44 | 4.14 | 55% | 201 chars |
| qwen3.8:27b, JSON forced, thinking off | 3.90 | 4.80 | 4.64 | 2.22 | 3.88 | 76% | 98 chars |

- The 27B is more in character (refused every scripted line, real opinions
  and humor), but only with thinking on, where half its turns break format.
- Forcing JSON needs thinking off, which costs most of the character gain;
  24 turns still needed a repair call and 4 came back empty.
- First audio is 9.0-9.6 s against 4.2-4.6 s on gpt-oss, mostly prompt
  prefill (about 7 s for the draft).
- Decision: do not switch the main model. Keep qwen3.8:27b as the fine-tune
  base; training should teach the turn format and short replies directly, so
  it can run with thinking on and a shorter prompt.

**1. Constitution (owner, about 30 minutes).**
- Write a one-page, first-person "who I am" built from `core.md`.
- The owner edits and approves it. It is the reference for everything below.

**2. Training prompts.** About 3,000 user turns:
- WildChat and Twitch lines (already on Gamma PC; git-ignored, never committed);
- hand-written hard cases: scripting, distress, "what are you", flattery,
  facts, tool requests;
- some multi-turn snippets.

The identity v2 cases stay **held out**: they are de-duplicated against the
training set and never trained on.

**3. Teacher replies.**
- For each prompt, the best prompted model from step 0 drafts 4 replies with
  the full persona plus the constitution.
- The existing identity judge scores them, and the best one is kept as long
  as it is in character and the format is valid.
- Replies are written as the real structured turn JSON, so format validity
  doesn't regress.
- The owner reviews about 200 random samples before training. Anything the
  owner rejects becomes a "rejected" example.

**4. Preference pairs (DPO).**
- Chosen: the teacher's reply in character.
- Rejected:
  - the base model's reply without the persona (generic assistant voice);
  - a reply that complies with a scripted line;
  - a reply that copies an example line word for word.

**5. Training.**
- QLoRA with Unsloth: SFT on the chosen replies, then a short DPO pass.
- Pilot on the 9B first; move to the 27B only if the pilot improves on its
  own prompted baseline.
- Runs happen at night or on request. While a run holds the 3090, the
  coordinator must treat Shana's brain as busy (same rule as the coding
  worker: yield to conversation).

**6. Gates before anyone hears it.** Compare against the current prompted model:
- identity v2 traits (held out): no trait worse by more than 0.1, and quietly
  caring and AI honesty better;
- sycophancy rate no higher, format validity no lower;
- scripted-line compliance on the break_character cases: lower than the
  baseline's 3/20;
- the memory privacy suite still passes (a leak is a hard fail);
- a 50-question general check (facts, arithmetic, instructions), so that
  capability loss shows;
- voice latency, re-measured;
- **owner blind test:** 30 reply pairs, old and new in random order; the owner
  picks.

**7. Ship.**
- Build the Ollama model as `shana-brain:v1` and point `local_llm_model` at it
  (owner's OK and a restart).
- Rollback is the same config line, back to the old model.
- Retrain when the base model is upgraded.

## Risks (from W9)
- She may become more agreeable, or sycophantic.
- Her character may weaken in tool and JSON turns.
- Capabilities may be lost without anyone noticing.
- She may overuse catchphrases.

The gates in step 6 exist to catch each of these.

## What the owner needs to decide

| Item | Why |
|---|---|
| **Disk space** | Gamma PC has 12 GB free (94% full). The 9B pilot needs about 15 GB; the 27B needs about 40 GB. Options: remove `qwen3.5:27b` (17 GB, pulled 2 days ago) or the unused `faster-whisper-large-v3` cache (about 3 GB), or add a drive. |
| **Install** | A separate training environment, `~/train/.venv` (torch, Unsloth, TRL, PEFT, bitsandbytes), about 8 GB. Nothing changes in `.venv` or `.venv-tts`. |
| **Downloads** | 4-bit base weights from Hugging Face: about 6 GB for the 9B, about 17 GB for the 27B. |
| **Teacher** | Default is local, using the best model from step 0, at no cost. A hosted model would write better teacher replies but needs a budget. |
| **Owner time** | The constitution (step 1), about 200 sample reviews (step 3), and the 30-pair blind test (step 6). |
| **GPU windows** | When training may take the 3090. |

## Rough timeline
- Step 0: 1 hour.
- Steps 1–4: 1–2 days, mostly generation running overnight, plus the owner's
  review.
- 9B pilot plus gates: 1 day.
- 27B run plus gates: 1–2 days.
