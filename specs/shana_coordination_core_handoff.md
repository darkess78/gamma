# Shana coordination core: implementation handoff

Status: First isolated refactor authorized on 2026-10-23, after GitHub checkpoints.
Live activation remains subject to the user's review and approval.

## Read first

- [Shana foundation direction](shana_foundation_direction.md): desired experiences.
- [Source comparison](shana_foundation_review.md): production versus candidate
  evidence, reuse recommendations, and limits of the audit.
- `AGENTS.md`, `README.md`, `specs/README.md`, and the relevant domain specs.

The user chose a new implementation chat. This document carries the decisions
forward; chat memory is supplementary. Keep user-facing responses concise.

## Checkpoints and starting point

The authorized direction is a partial rebuild: introduce a small coordination
core and selectively reuse existing capabilities and safeguards. A wholesale
rewrite or activation of the older modular candidate is not selected.

GitHub repository: `darkess78/gamma`.

| Snapshot | Branch | Commit |
| --- | --- | --- |
| Production source | `checkpoint/production-before-brain-2026-10-23` | `67f5ed9c156d0d8f8540ee4a5a9de51ee511ef42` |
| Windows source and discussion | `checkpoint/windows-before-brain-2026-10-23` | `4c9341eed6cb12cc92c3f433ba825e44a98e713d` |
| Older modular candidate | `checkpoint/modular-candidate-before-brain-2026-10-23` | `38b266edf29efcb26a30c0a6b92de7013d33ca9d` |

The implementation branch `refactor/shana-coordination-core` starts from the
production snapshot with the discussion documents and this handoff added.
Check its current remote revision before starting; it may advance afterward.
The other snapshots preserve distinct work, not interchangeable runtime trees.

Snapshots capture portable source, tests, assets, and documentation, including
uncommitted work. They exclude secrets, machine-local configuration, runtime
databases, models, logs, caches, and private research/coursework. They are not
backups of Shana's personal database or deployment configuration. Their parent
is the previously published feature commit, avoiding publication of local
pre-publication history. Per-file manifests are under `specs/checkpoints/`.
Windows line endings are Git-normalized; its startup-script case alias is
handled in the snapshot only. The original checkout remains unchanged.

Production authority is `/home/neety/Documents/gamma-main` on Gamma PC. Its
pre-checkpoint HEAD was `38e3fbff4796e0c8ed56a2fcda9836d7a8e2273a`, with
substantial dirty work. The Windows checkout is not a complete runtime baseline.
The separate `/home/neety/Documents/gamma-modular-phase1` tree is reference code.

Checkpoint creation did not test conversational quality or execute practice,
training, inference, deployment, or service restarts. Recheck operational facts
before acting. Earlier accepted repairs went live on October 19; blanket older
claims that all foundation work is unactivated are stale.

## First useful experience

Use private text in Monitor and one bounded offline coding-practice capability.
Shana can choose authorized practice during quiet time, preserve her place when
asked to switch, and remain conversational while a worker runs. Completion and
blockers return to her activity record. During sleep, workers may finish existing
instructions, while new decisions wait. On waking, she catches up on actual
outcomes. No event-driven waking or live-source mutation is in this first slice.

This demonstrates coordination and follow-through. It does not establish
long-term interest development, general coding competence, game learning,
natural voice quality, or behavioral continuity across arbitrary model swaps.

## Core ownership

One continuing Shana owns the current situation, attention, intentions,
commitments, accepted personal-state changes, and decisions about what happens
next. Specialist components execute work and return evidence.

Use one repeated process: incorporate observations/results, choose the next
action or wait condition, execute through a bounded capability, and reconcile
the observed outcome. Speaking is one possible action. Internal decisions need
not produce dialogue. Retain compact working notes, not raw chain-of-thought.

Mood, relationships, and interests contribute context to the same decisions.
They are not independent controllers. Mood can carry between activities and
settle, but should not intentionally reduce reliability. Baseline personality
and seeded interests are sufficient for the initial milestone. Do not introduce
separate interest, boredom, self-evaluation, or training systems upfront.

A lightweight scheduler watches input, worker outcomes, due conditions, and
bounded discretionary reviews. The model can specify its next reconsideration
condition. Batch ordinary events, avoid repetitive calls while nothing changes,
and preserve foreground resource priority.

## Reuse and concrete gaps

Preserve identity/privacy boundaries, scoped memory and journal repairs,
provider adapters, structured reply/silence contracts, Monitor output rendering,
and offline exercise/check/evaluation machinery.

- Monitor currently sends typed input to `/api/monitor/input` through the stream
  decision path with speech requested. Make private text explicit through the
  core, preserving authentication, useful guards, and browser contracts.
- Practice is independently launched from Dashboard into `gamma.coding.cli`.
  Its CLI starts the fixed curriculum, not a selected arbitrary project. Add
  bounded exercise selection and a capability handoff that promptly returns a
  run identifier; never run the long task inside the conversation request.
- Practice keeps intermediate files and attempt state in memory and records
  results after the run. Add progress and durable resumption boundaries.
- Reuse backend admission, but reconcile sleep-only idle eligibility and
  resident-model rejection with awake background work and foreground priority.
  Cooperative admission does not prove conversational latency or preemption.
- Existing `coding_goal` targets the repository executor, whose checked path can
  apply source and restart services. It is not the offline-practice capability.
  Do not expose that authority through the first brain or activate it by accident.

Existing exercise lessons are skill-specific guidance. They must not become
automatic personal-memory, personality, or model-weight updates.

## Shared activities and worker checkpoints

An activity record identifies the goal, origin, lifecycle state, worker run,
checkpoint/result references, next step, and waiting condition. Awake/asleep and
current focus belong once in brain state. Ordinary chat can change attention
without suspending the worker. Worker execution details have one owner.

A worker checkpoint needs:

- Run/exercise identity and version, original inputs, current candidate files or
  durable artifact references, and hashes. Hashes alone cannot restore content.
- Current stage, completed check receipts bound to the candidate/check versions,
  bounded usable feedback, and the next unfinished operation.
- Attempts already consumed, remaining budget and any expiry, authorized scope,
  and uncertain in-flight operations. Resume does not replenish limits.

Persist complete checkpoints at meaningful boundaries. Preserve edit artifacts
before referencing them and publish a complete checkpoint atomically. Keep
withheld evaluation details out of learner feedback and training examples.

On recovery, reconnect to a surviving worker or restore its last complete
checkpoint. Validate inputs/artifacts/check versions before trusting successes.
Continue with the next unfinished step. If an edit was saved but tests were not
completed, test the edit instead of regenerating it. Changed checks may require
revalidation. An uncertain interrupted model request or disposable test may need
repeating; do not promise zero repeated computation or replay external actions.
Stable run/operation identifiers and processed-result tracking prevent duplicate
dispatch and duplicated effects. A requested pause differs from an acknowledged
pause. Never report interrupted or uncertain work as finished.

## Learning direction, without building a learning platform

An experience can be retained as memory, inform a tentative decision change, and
later become training evidence. A decision policy may itself be a trained model;
the distinction is which component changes. Memory alone does not update weights.

Keep observations/actions/results separate from interpretations. Outcomes and
ordinary explicit corrections provide feedback; silence or self-confidence does
not certify success. Self-review and another model can propose or question lessons,
but agreement is not independent ground truth. Ask occasional targeted questions
when needed and sample apparent successes to detect confident errors.

Keep early changes narrow and reversible. Evaluate generalization, contradictory
cases, and regressions on separate evidence. Retain failures. User praise is not
the sole objective; Shana should develop opinions and be able to disagree.
Version accepted changes, retain their evidence, and revise a wrong interpretation
without deleting the underlying experience. For trained replacements, keep a
previous model version. Subjective character/relationship changes may still need
human feedback; automatic detection cannot establish every mistake.

## Single-owner migration

Develop against isolated state, private outputs, and bounded offline tools.
Current Shana remains the sole live owner. New code cannot publish live speech,
write personal records, or launch production work during isolated validation.

Before a later approved cutover, establish one active owner and generation.
All paths that publish, commit personal changes, or dispatch work must honor it
at effect commit, not just at turn start. Stop or subordinate independent old
selectors. Late old-generation responses cannot commit or publish. Existing
authorized workers may retain evidence for the current owner to review.

Connect capabilities incrementally, but hand over live authority explicitly.
Unconnected channels cannot retain a competing controller during a limited live
trial. Defer cutover until necessary channels are ready, or agree the limited
trial scope. Rollback is also a controlled ownership handover with compatible
current state; do not restore an old database snapshot and lose experiences.

## Acceptance before expanding

Demonstrate through the real selected entry points, not only helper tests:

- Sensible speech/silence and activity choices across focused/relaxed situations;
  ordinary chat remains usable during work and waiting is resource-bounded.
- One run retains work through interruption, sleep, and restart; duplicate
  completion reports do not duplicate actions or memory effects.
- Completion claims match artifact/check evidence. Bounded failures yield useful
  blockers, and user-requested work is not silently abandoned.
- Delayed old responses, duplicate worker results, and a crash during handover
  cannot create competing decisions. Recovery and rollback preserve evidence.
- Owner/private scope remains enforced; no training, production edits, service
  restart, or activation occurs without the separately required authority.

The user also assesses whether the experience feels like one continuing Shana.
Do not infer that from persistence tests or aggregate task scores alone.

## Execution instructions for the new chat

Implementation is now authorized. Begin with the smallest isolated slice above;
do not reopen the entire brainstorming discussion or ask routine permission.
Create/reuse isolated checkouts starting at `refactor/shana-coordination-core`.
Keep the original dirty Windows, production, and modular-candidate trees intact.
Run Gamma tests, inference, GPU checks, and runtime validation on Gamma PC.

Follow the user's worker requirement: read
`C:/Users/darke/.codex/workers/README.md`, prefer scoped coding-workers jobs,
default to local Qwen, keep one active job per provider and at most five global
jobs, respect the verified context/budget limits, and review source hashes,
`proposal.diff`, `result.json`, and actual behavior before integration.

Run Windows CLI helpers and all nested children hidden, using `pythonw.exe` and
`CREATE_NO_WINDOW` with logs. A hidden Start-Process window alone is insufficient
for a console executable. Include this in worker prompts and verify actual paths.

Preserve route contracts and machine-local configuration. Use purpose-based
branches without a `codex/` prefix. Activation of running Shana remains a later
user decision after concrete implementation and verification evidence are ready.
