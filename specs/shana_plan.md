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

### W0. Measurement — **Do, first** (memory retrieval set built 2026-11-14)

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
Twitch/Discord sources stay shared even with the header.

**Step 2 done (2026-11-14):** shared audiences keep their own history. Stream
turns are journaled as `public_stream` and Discord voice/text as
`public_discord`; each reads back only its own recent turns, labelled with
who said them. Old `public` rows (possibly mislabeled) are never read. Recent
stream chat reaches stream turns through its own `stream_context` (private
background notes still never reach shared turns), and Discord no longer
writes into stream chat memory. Shana is told who is speaking in shared chat.

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

**First pass done (2026-11-14):** `memory/retrieval.py` ranks by BM25
relevance (stopwords, light stemming) + recency (30-day half-life) +
importance, across sessions with a small same-session boost. Facts are now
chosen by relevance with the two most important pinned. Measured with
`python -m gamma.evals.memory_retrieval` on a seeded 20-episode/10-fact history:

| | episode recall@5 | fact recall@5 | changed facts | paraphrases |
|---|---|---|---|---|
| previous search | 0% | 57% | 0% | 25% |
| current search | 100% | 100% | 100% | 25% |

**Choosing what becomes a memory (2026-11-14):** automatic candidates now go
through `memory/triage.py` before storage. A cheap filter drops obvious
non-memories (reactions, single words, Shana's own reply text, one-turn
instructions); a pluggable judge decides the rest (`memory_triage_judge`:
`keep` today, `model` once the GPU is free, or the CS 4700 classifier later).
Explicit "remember this" saves skip triage. Scored with
`python -m gamma.evals.memory_triage` against the 400-row labelled dataset in
`research/cs4700_memory_triage`: the filter drops none of the 200 labelled
keeps; the dataset's skip examples are subtle (facts scoped to one release or
one attempt) and need the model judge or a trained classifier, so agreement is
50% until then.

Paraphrases ("kitty" for cat, "for a living" for job) still miss: that needs
embeddings, which need a model download (an owner decision). Supersession,
reflection and the FTS5 index for large histories remain.

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

**Foundation done (2026-11-14):** every new fact and episode records
`visibility` (`owner_only`, `stream`, `discord`, `unknown`) from where it was
said; older rows count as owner-only. Searches take a `visibility` filter;
owner-private turns are unchanged. Shared turns are told who is speaking (W1).
**Viewer memory and one trust system done (2026-11-14):**
`memory/viewer_memory.py` keeps only what a viewer says about themselves
(favourites, pets, what they're learning, pronouns, job), refuses instructions
and claims about others, keys notes by platform id, limits them to the
audience they were said in, caps 3/day and 30/viewer, and skips suspicious or
blocked viewers. Notes come back only when the same viewer speaks in the same
audience. `GET/DELETE /v1/memory/viewers/{key}` shows or forgets them.
`identity/standing.py` combines owner ids, linked person records and the Twitch
trust store; linked people's trust now applies on Twitch, and unlinked viewers
cap at guest.

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

**Steps 1 and 4 done (2026-11-14):** instruction-override detection matches
real overrides instead of any "act as"; trusted/regular viewers can share
links; rude words match whole words; moderation escalation needs an actual
request ("ban @x", "timeout that guy"); a spent speech budget defers before
the model call; EventSub drops repeated message ids and posts off the event
loop. More EventSub subscriptions need Twitch scopes on the owner's token.

Whether Shana should type in Twitch chat as well as speak is an owner decision.

### W5. Discord — **Do**

Today: one guild, one channel; @mentions not detected; messages during a reply
are dropped; Discord text leaks into the Twitch stream memory; voice replies to
anyone who speaks, can't be interrupted, and runs the full slow pipeline.

1. Text: @mention and reply detection, queue instead of drop, typing indicator,
   multiple channels, separate from stream memory. (S–M)
2. Voice: decide who she's answering (name, owner, direct question), pacing,
   and interruption. Depends on W6 for speed. (M)

**Step 1 done (2026-11-14):** @mentions of the bot and replies to her messages
count as addressing her (other mentions become display names); messages during
a reply queue (up to 3) instead of being dropped; "Shana is typing..." shows
while she thinks; `discord_text_channel_ids` adds more channels; Discord no
longer leaks into stream memory (done with W1).

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
Games connect over a WebSocket, declare actions with JSON schemas, and ask
Shana to decide; Gamma is the server and sends back actions. The design is
inspired by the Neuro SDK, but Gamma speaks its own protocol rather than
reusing Vedal's (owner's call, 2026-11-14), so Neuro-sama mods need a small
port to connect.

- Gamma's own versioned protocol, with params validated before sending so a
  bad model answer is retried instead of reaching the game.
- Test with a random chooser and an example game — no LLM needed.
- Then one turn-based game end to end with the real model.
- "Learning to play" (D8) is separate from having access: memory of what
  happened in past runs comes from W2.

**Version 1 done (2026-11-14):** see [games.md](games.md). Protocol at
`ws://<host>:8000/v1/games/play?key=…`, off by default, with a reference
Python client, an example tic-tac-toe game, a random chooser and a model
chooser. Speaking her move lines, comment-on-events, and a real-game trial
remain.

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
5. W7 protocol server tested with a random chooser
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
3. Which game first? (A turn-based one is easiest; it needs a small client, see games.md.)
4. When to deploy `rebuild/shana-foundation` to Gamma PC (it is not live yet).
5. Which Discord servers/channels she should be in.
7. Meaning-based memory search (catches "kitty" for cat) needs a small
   embedding model download (~300–600 MB, runs on CPU or the 3060 Ti).
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
