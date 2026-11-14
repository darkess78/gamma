# Shana improvement plan

Status: Proposed, 2026-11-14. Nothing here is approved for implementation until
the owner picks it. Builds on the [foundation direction](shana_foundation_direction.md)
(D1–D11) and replaces the Codex-era roadmap as the working plan.

## How to read this

The owner's goals are split into workstreams. Each has a verdict:

- **Do** — good idea, start soon.
- **Do, changed** — the goal is right but the approach needs to change.
- **Later** — good idea that depends on something else or on the GPU.
- **Not now** — costs more than it returns at this stage.

Each verdict weighs value (how much it moves Shana toward the vision),
feasibility (this hardware, today's models), dependencies, risk, and the
smallest first step that proves it before more is built.

## Constraints

- **Hardware:** Gamma PC, RTX 3090 (24 GB) + RTX 3060 Ti (8 GB), Linux only.
  The GPUs are lent to the manga localizer for now, so GPU work is sequenced
  after it or uses a hosted model.
- **Owner approval:** nothing changes running Shana without it (D5).
- **Privacy:** familiarity never authorizes revealing private material publicly (D7).
- **Budget:** hosted API spend is the owner's call (see decisions).

## The first finding: Shana never reads her memory

Every real turn — dashboard text, live voice, Discord, Twitch — runs as a
shared or unknown recipient. Only `run_llm_test.py:22` ever builds an
owner-private recipient, so `persona/loader.py:100` never loads facts,
episodes, core memories or relationship state, and conversation history is
dropped. Memory is written but never used. This was a deliberate fail-closed
privacy choice by Codex with no matching way to open it, and it is the main
reason Shana feels like she doesn't know anyone. Fixing it is step one; most
other memory and people work is invisible until it is done.

## Workstreams

### W0. Measurement — **Do, first**

Without numbers, "better memory" or "more like Shana" can't be judged, and a
fine-tune can't be compared to a prompt.

- **Identity suite:** 100–200 fixed prompts (casual talk, "you're just ChatGPT"
  probes, users asserting false facts, emotional bids, tool/JSON turns), scored by
  an LLM judge against a trait rubric (proud, blunt, guarded, quietly caring,
  AI-aware) plus a sycophancy rate and format-validity rate.
- **Memory suite:** 50–100 questions over seeded histories — single facts,
  changed preferences, "when did…", cross-platform people, and must-not-leak
  cases (a leak is a hard fail). Retrieval recall@k needs no model.
- Smallest step: the memory suite's retrieval half, which runs without a GPU.

### W1. Memory reaches the prompt — **Do, first**

- Authenticated owner channels (dashboard text, local live voice from the owner)
  become owner-private, so memory, history and core memories load.
- Shared channels (Discord, Twitch) get scoped history and the stream's recent
  context, which is built today but stripped.
- Live voice currently passes no speaker at all, and a missing speaker is
  treated as the owner by the identity resolver (`identity/resolver.py:47`)
  while the prompt treats it as unknown. Both need an explicit speaker.
- Smallest step: dashboard text as owner-private, with a privacy test that
  Discord/Twitch turns still can't see it.

**Step 1 done (2026-11-14):** the dashboard proves it is the owner with a
secret header (`X-Gamma-Owner-Channel`, key in `data/runtime/owner-channel.key`,
created on first use, never sent to a browser). With it, Monitor owner text
(`mic_transcript`/`owner_command` with private output), dashboard live voice,
image replies and `/v1/conversation/respond` run owner-private with the owner as
speaker, so memory and history load. Stream-public output, rehearsed chat and
Twitch/Discord sources stay shared even with the header. Remaining: shared
channels' scoped history and stream context, and explicit speakers elsewhere.

### W2. Better memory selection — **Do**

Current retrieval is OR-substring matching on any 3+ letter word, filtered to
the current session, with no recency (`memory/service.py:365-400`).

1. Hybrid search in SQLite: FTS5 plus sqlite-vec, fused with reciprocal rank
   fusion. Embeddings from a small local model (EmbeddingGemma-300M or
   Qwen3-Embedding-0.6B) on CPU or the 3060 Ti. Cross-session.
2. Score = relevance + recency decay + importance (rated when written), then a
   token budget.
3. Facts that change are superseded, not overwritten (`valid_from`,
   `invalid_at`, `superseded_by`), so she can say "you used to…".
4. Nightly reflection during sleep (D10) writes summary insights.

Smallest step: (1) alongside the old search, measured with W0. No GPU needed.

### W3. Knowing people — **Do, changed**

The goal is right; the priority is different from "richer profiles".

- **Visibility first:** every memory gets a scope when written (stream-public,
  Discord server, DM, owner-only). Retrieval filters by audience before
  ranking. This is what lets Shana remember people *safely* across Twitch and
  Discord.
- **One trust system:** identity roles (`config/users.toml`) and Twitch's
  separate `ViewerTrustStore` don't talk to each other. Merge them into the
  person record.
- **Linking accounts:** by stable platform IDs only, confirmed by the owner or
  a code posted from both accounts. Never by matching names.
- **Relationship state per person** (familiarity, last seen, notes) updated
  after sessions, drifting back toward neutral. Today it is one static file.
- **Her opinions as memories** with the episodes behind them, superseded when
  they change (D1, D7).
- Shared prompts currently hide the speaker's name entirely ("Guest speaker");
  she should at least know who she's talking to.

Smallest step: visibility column + filter, speaker display name in shared prompts.

### W4. Twitch and other chats — **Do**

Today: one blocking HTTP call per chat message; replies only when someone says
"shana" or a keyword scores high; deferred events are never played; filters
catch normal chat ("act as", any "ban "); Shana never posts in chat; EventSub
can stall its own connection and has no duplicate check.

1. Fix the filters and check the speech budget *before* the LLM call. (S)
2. Workers enqueue and return; one loop picks the best message from a recent
   window, drops stale ones and drains the deferred queue after speech. (M)
3. A small model (on the 3060 Ti) summarizes busy chat for her, with a check
   that anything she quotes was really said (Riko's known failure). (M)
4. EventSub: async posting, dedupe on message ID, more subscriptions. (S)
5. Other sites (YouTube chat, Kick) through the same event shape later.

Whether Shana should type in Twitch chat as well as speak is an owner decision.

### W5. Discord — **Do**

Today: one guild, one channel; @mentions not detected; messages during a reply
are dropped; Discord text leaks into the Twitch stream memory; voice replies to
anyone who speaks, can't be interrupted, and runs the full slow pipeline.

1. Text: @mention and reply detection, queue instead of drop, typing indicator,
   multiple channels, separate from stream memory. (S–M)
2. Voice: decide who she's answering (name, owner, direct question), pacing,
   and interruption. Depends on W6 for speed. (M)

### W6. Voice latency — **Later (needs GPU)**

Each live turn starts a fresh Python process that reloads Whisper, the
conversation service and the stream brain, then does full STT, a full LLM
reply, then TTS. Every reference project streams LLM tokens into
sentence-level TTS.

- One long-lived voice process with models loaded; stream LLM tokens; speak
  each sentence as it completes; transcribe while the person is still talking.
- This is the biggest win for "feels live", and it needs the GPU to test.

### W7. Games — **Do, changed**

The goal is right; the approach should be one protocol, not per-game code.
Neuro-sama's game SDK is MIT-licensed and documented: games connect over a
WebSocket and register actions with JSON schemas; the AI is the server and
sends back actions. Community mods already exist for Balatro, Inscryption,
Buckshot Roulette, Slay the Spire 2, Among Us and more, and other people
already run their own servers for it (Gary, Jippity).

- Implement the server side of that protocol in Gamma. Existing mods then work
  with Shana by pointing their URL at Gamma; new games get mods written against
  a known API.
- Test with the SDK's own tools (Randy for random play, Tony for manual) —
  conformance needs no LLM.
- Then one turn-based game end to end, with schema-constrained output.
- Caveats: some mods say "Neuro" in their text; one forced action at a time;
  results expire after about 20 seconds.
- "Learning to play" (D8) is separate from having access: memory of what
  happened in past runs comes from W2.

Smallest step: protocol server + Randy, no GPU.

### W8. Shana coding her own improvements — **Do, changed**

The goal is reachable; the current approach can't get there. The existing
executor asks a local model for one-shot JSON patches with an 8k context and
no way to read files, run tests or see errors between edits, and most of the
code worth improving is on its protected list. Independent tests put 24 GB
local models well below their published scores on real repository tasks.

- Keep: the bubblewrap sandbox, baseline-must-pass, test-count-can't-drop, the
  credential scan, and stopping at owner review.
- Replace the planner/patcher with an existing coding agent run headless in a
  git worktree (Claude Code / Agent SDK, or mini-swe-agent for local models),
  producing a branch, its diff including tests, and the test results for review.
- Use a hosted model for real tasks (estimated $1–2 per small task, so about
  $10–20 a week for ten); local models only for trivial ones, and only when the
  GPU is free.
- Guardrails: protected paths checked on the diff (coding/, safety/, auth,
  deploy/, config/), no secrets mounted, network limited to the model API, a
  budget and time cap per task, Gamma owns git, no auto-deploy.
- Drop the lessons/curriculum/practice-learning code from this path.

Smallest step: about 200 lines — worktree, headless agent under bwrap,
existing checks, diff + results as `awaiting_owner_review` on the Coding page.

### W9. Shana's identity in the model — **Later, changed**

"Knows she's Shana without a character prompt every time" can't fully happen:
current facts, memories, the people she's talking to, privacy rules and output
format must stay in the prompt, and prompt rules are the safety boundary. A
fine-tune *can* shrink the character section to a few lines and make her
voice steadier.

- Method with the best evidence: a short first-person "constitution", DPO
  (teacher answering in character vs base model), then SFT on self-reflection,
  from a few thousand synthetic dialogues built from the persona plus a few
  hundred curated real turns. QLoRA fits on the 3090 for 7–14B comfortably and
  ~27B tightly; serve as an Ollama adapter.
- Known risks: more agreeable/sycophantic, weaker character in tool/JSON turns,
  hidden capability loss, catchphrase overuse, retraining on every base upgrade.
- Needs W0 first, and the GPU.

Smallest step: QLoRA on a 7–14B model, compared against the prompted 27B on W0.

### W10. The coordination core — **Do (spine)**

The core on this branch is the one place that decides what Shana does next.
Monitor, then dashboard text, then Discord and Twitch move onto it over time,
replacing the four separate decision paths. `_respond`, the 510-line reply
function, gets rebuilt here rather than patched.

Smallest step: connect Monitor's private text to the core (needs a model to
judge quality, so real-model testing waits for the GPU).

## Order

**Phase 1 — no GPU needed**
1. W1 memory reaches the prompt (dashboard text first)
2. W0 retrieval half of the memory suite
3. W4 steps 1 and 4 (filters, budget check, EventSub fixes)
4. W5 text fixes
5. W7 protocol server tested with Randy
6. W8 harness v1 with a hosted model (if the budget is approved)
7. W2 hybrid search, measured
8. W3 visibility scopes and one trust system

**Phase 2 — when the GPU is free**
- W6 streaming voice, W10 real-model wiring, W4 steps 2–3, W5 voice,
  W7 first real game, W0 identity suite against the real model.

**Phase 3**
- W9 fine-tune experiment, W2 reflection, independent streams (D8).

## Decisions for the owner

1. Hosted model budget for self-coding (and possibly judging evals): yes/no,
   and a monthly cap.
2. Should Shana type in Twitch chat, or only speak?
3. Which game first? (A turn-based one with an existing mod is easiest.)
4. When to deploy `rebuild/shana-foundation` to Gamma PC (it is not live yet).
5. Which Discord servers/channels she should be in.
6. An API caller that names no speaker is still treated as the owner (owner
   tools included). Production requires the API token, so only Gamma's own
   processes can do this, but every one of them could. Tightening it means
   updating the local command-line tools and about 25 tests that rely on it.

## Sources

Research done 2026-11-14 (web, cited in the session): Neuro SDK
(github.com/VedalAI/neuro-sdk, MIT; Gary, Randy, Tony), J.A.I.son
(github.com/limitcantcode/jaison-core), Project Riko
(github.com/rayenfeng/riko_project), Open Character Training (arXiv 2511.01689),
Generative Agents (2304.03442), MemGPT (2310.08560), Graphiti (2501.13956),
LongMemEval (2410.10813), SWE-bench reproduction (arXiv 2604.00362).
