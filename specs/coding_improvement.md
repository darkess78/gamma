# Coding capability and model improvement

Status: Initial coding-practice and assessment foundation, October 4, 2026.

## Intended outcome

Gamma should turn broad owner goals into task lists, handle necessary subtasks,
continue independent work around blockers, and deliver runnable changes with
reviewable evidence. Reliable coding and accurate completion claims come first.
Discord text, voice, and authorized commands are the first proposed real project.

The longer-term learning process observes verified work, records editable
lessons, practices weaknesses while idle, and evaluates separate model candidates.
Candidate model activation initially requires owner approval and rollback.

## Implemented foundation

`src/gamma/coding/` provides fixed, fictional offline Discord-related exercises:
lossless message chunking, exact verified-owner authorization, command dispatch,
and bounded retry decisions. They never connect to Discord or edit a live project. Dependencies are
ordered; failed prerequisites block dependent tasks while unrelated tasks continue.

Local Ollama inference receives only editable exercise source, its goal, active
skill-specific lessons, and bounded check feedback. Protected regression and
feature fixtures cannot be changed by a proposal. Malformed, unchanged,
oversized, out-of-scope, and invalid Python replacements consume attempts.
Transport failures or occupied inference endpoints stop inference without replay.
Occupied endpoints produce blocked run records, so a deferred invocation remains
visible rather than disappearing after a process error.

Each check executes in a read-only Bubblewrap namespace with no host home,
network, configuration, credentials, or GPU devices. A systemd user scope limits
memory, process count, and CPU. The trusted external virtual environment supplies
Python. Completion requires successful regression and feature checks plus a
fresh completion receipt from the trusted check bootstrap. A bare zero exit code
does not establish completion. Infrastructure failures are blocked work, not
evidence that the coding model needs training.

Results use `verified_practice`, `needs_checker`, or `blocked`. All records retain
`production_verified: false`. Source hashes bind checks to the exercised code.
New runs also require a separate generated-case evaluation after public checks
pass. Varied Unicode, boundary, authorization, and malformed-input cases are
withheld from the learner. Failed evaluation stops that task without exposing
cases to a repair prompt. This is independent executable verification, not a
stronger model's semantic review. Historical public-only runs remain identifiable
through separate independently-evaluated counts.

Passing solutions and their public fixtures are saved as
`candidate-solutions.json` beside each run. These contain only the fictional
exercise source, instructions, model identity, source hash, and evaluation
summary. They remain `training_approved: false`; no training consumes them.
Generated evaluation cases and seeds are excluded from both solutions and
learner feedback. Repeated generated cases measure these task families only;
they are not a separate unfamiliar-project benchmark.

Five attempts per task and a five-minute budget are defaults. The time budget is
checked between stages; an in-flight inference may take up to 90 seconds and
a sandbox check up to 30 seconds. This is not a strict aggregate deadline.

Records live under ignored `data/coding/practice`. Their bounded capability
summary reports successes, failures, repairs, and blocked work. It never infers
general coding competence from repeated familiar exercises.

Observed practice lessons are persisted separately under `data/coding/lessons`.
Owner text and active-state edits override future automatic updates. Disabled
lessons remain disabled. These records are practice guidance, not a replacement
for Gamma's personal memory and not modifications to learned weights.

The Improvement dashboard shows practice results and editable lessons, starts a
bounded practice process, and configures idle practice through authenticated
dashboard-owned `/api/coding/*` routes. Inference runs outside request handlers.

Idle practice defaults to disabled. An enabled worker polls every 30 seconds,
requires explicit sleeping Presence, gives queued/running/paused improvement
requests priority, defers on unknown or occupied inference, and rechecks
foreground activity between proposals. Defaults allow two starts per UTC day,
at least one hour apart. Pausing prevents later starts; an in-flight bounded
operation completes at its next checkpoint. The worker is supervisor-managed
through its module process, but is not yet installed as a reboot-persistent service.
Admission is advisory: this does not reserve Ollama against a foreground request
arriving after the idle check.

## Commands

Run these on the Gamma PC with the repository virtual environment:

```bash
.venv/bin/python -m gamma.coding.cli practice --model qwen3.8:27b
.venv/bin/python -m gamma.coding.cli assess
.venv/bin/python -m gamma.coding.worker --once
```

The named model is a practice baseline, not a selected fine-tuning target.

## Outstanding implementation

### Repository coding and Discord dashboard controls

The Improvement page now starts `gamma.coding.repository` jobs under the existing
dashboard access policy.
The initial registered scope is the pure Discord dashboard status helper at
`src/gamma/integrations/discord/bot_status.py`. Gamma's local model produces its
own task plan and source replacements. The runner executes protected regression
and feature checks offline, then separate withheld malformed-input checks. Failed
checks feed subsequent attempts, up to five within a ten-minute budget.
After five unsuccessful attempts, an independently selected local checker
diagnoses the executed failures and replaces the failed implementation approach.
Two further repair attempts may use that guidance under the same wall-clock
budget. The protected tests and scope cannot be changed by either model.
Completion requires applying the candidate to the registered source and repeating
checks on the installed artifact. A baseline hash prevents overwriting intervening
edits; each successful application retains the original source for rollback.
Interrupted or failed promotion is never replayed automatically.

`GET/POST /api/coding/projects` lists/starts this scope. The stop endpoint requests
termination at the next stage boundary. Results retain plans, failures, hashes,
attempt sources and verification evidence under ignored `data/coding/projects`.
Successful runs add an editable `observed_repository` lesson. Candidate examples
are marked `training_approved: false`; no model weights are trained or activated.
Idle practice yields to queued or running repository jobs.

This registered scope can apply verified changes under the owner's current
authorization. The existing broader improvement experiments keep their separate
review-only promotion policy. They are not a general-purpose coding executor.
The autonomous repository executor below expands source discovery and task
planning beyond this registered helper. Runtime adapters remain necessary for
unfamiliar integrations and external actions.

The Presence page provides Shana bot start/stop and voice join/leave/pause/resume.
Commands inherit dashboard access controls and use a private serialized mailbox, owner identity from
local configuration, and receipts from the live bot. Queued commands expire;
running commands are never replayed after interruption. Status requires a live
process and a fresh finite heartbeat and executes the installed Gamma-authored
pure helper with restricted builtins. It exposes the helper hash for verification.

- Automatic browser/runtime acceptance for new, unfamiliar coding scopes.
- External checker integration beyond the local checker handoff.
- Evidence-driven curriculum expansion rather than repetition of the fixed set.
- Larger verified training datasets, incumbent-model comparison, production
  candidate serving, owner activation and model rollback.
- Personal preference/character learning and integration with the Memory dashboard.
- Global GPU reservation and foreground preemption during candidate training.

Passing this initial curriculum is a baseline measurement, not a model upgrade
and not proof that Gamma can complete the Discord project autonomously.

## Owner-directed autonomous repository coding

The authenticated Improvement dashboard owns `GET/POST /api/coding/work` and
`POST /api/coding/work/{id}/control`. The owner gives a result and rough notes;
Gamma inspects current source and plans up to four dependent tasks. The coding
model and a different local checker are explicitly selected. Budgets range
from one minute to eight hours. Pause, resume and stop act at safe boundaries;
in-flight model calls and sandbox checks have their own bounded timeouts.

Each job copies the current dirty checkout into a private snapshot. Local
credentials, model files and runtime databases are excluded. Protected baseline
tests must pass before inference. The checker writes acceptance tests before
seeing candidate edits. Invalid test programs receive bounded format feedback;
valid acceptance is frozen. Gamma writes exact source patches and uses executed
failures to repair them. Both requested behavior and protected regressions must
pass before a task is accepted. After five attempts, the checker diagnoses the
failures, followed by at most two repair attempts under the same time budget.

Candidate checks use the existing no-network, read-only sandbox and an executed
pytest receipt requiring actual test calls. The final independent review sees
the source diff and evidence. Application checks baseline hashes, stores original
bytes, repeats the full suite on the installed source, restarts affected managed
services and checks HTTP health. Failures roll back only unchanged candidate
bytes; concurrent owner edits are preserved. Interrupted inference or an
uncertain application is never replayed automatically.

The executor can edit ordinary Python modules and the lesson/curriculum learning
modules. Its own controller, tests, authentication, safety, identity, supervisor,
configuration and model weights remain protected. Integration worker activation
requires a feature-specific adapter; HTTP health alone does not establish voice
or external-service correctness. This is bounded repository autonomy, not a
guarantee that every unfamiliar project will succeed.

Only a trusted owner speaker may invoke `coding_goal`, `coding_status` or
`coding_control` in conversation. An explicit `coding goal: <result>` starts a
job without waiting for coding to finish in the conversation request. Caller
arguments cannot supply owner identity. Dashboard access retains the existing
authentication and proxy policy.

Jobs, plans, model responses, frozen tests, repairs and application receipts are
stored under ignored `data/coding/autonomous`. Verified examples explicitly say
`training_approved: false` and `model_weights_changed: false`. Collecting examples
and editable lessons does not change model weights. The separate isolated
candidate trial below performs actual weight updates.

`deploy/gamma-coding-autonomous.service` is a user systemd unit for the Gamma PC
checkout. It maintains one executor and survives user-session restarts when
lingering is enabled. `deploy/gamma-coding-idle.service` provides the corresponding
practice worker. Idle practice yields to queued, running, paused and deferred
autonomous work as well as foreground conversation.

### Frozen owner checks and durable learning

Trusted dashboard/API or host callers may supply up to three independently
written `test_goal_owner_*.py` programs and select `acceptance_mode=owner_contract`.
They are validated and hashed before candidate inference. This substitutes for
checker test design; final independent model review remains mandatory. The
conversation coding tool cannot select this mode. Successful frozen checks are
retained as uniquely named protected `tests/test_verified_*.py` files, without
overwriting owner tests, and participate in future repository snapshots.

After successful installed checks and activation, the host observer reloads the
installed lesson implementation and passes the actual completed work record.
Strict proof validation and work-ID deduplication precede learning. Tasks without
an explicit skill produce `lesson-repository_verification`; valid explicit skill
labels remain compatible. Owner text and disabled flags survive observations.
The dashboard exposes bounded revision history with automatic/owner provenance.
Observer failure is reported separately and cannot replay or undo applied work.

### Isolated coding LoRA pilot

`GET/POST /api/coding/candidates` and `POST /api/coding/candidates/{id}/stop`
expose a manual bounded trial from Improvement. Training uses the separate
`.venv-training` environment and pinned official `Qwen/Qwen3-4B-Instruct-2507`
revision `cdbee75f17c01a7cc42f958dc650907174af0554`. Base safetensor hashes are
verified before and after the trial. NF4 double quantization with BF16 compute,
rank-8 LoRA on attention projections, frozen base parameters, assistant-only
labels and a bounded AdamW schedule run on Gamma's 3090. The production models
and audio models remain separate.

Only re-executed, hash-matched fictional practice solutions enter the dataset;
the known flawed command-dispatch example is excluded. Eight separate unfamiliar
coding tasks never enter training. Each response must satisfy the JSON source
protocol and execute protected tests in the offline sandbox. Admission requires
free GPU headroom, no resident Ollama model and an exclusive candidate lock.
Coding/idle practice yields while the lock is held; candidate execution checks
for new owner work at stage/step boundaries. This is not global foreground
GPU preemption. Stop does not replay an interrupted trial.

The comparison requires matching checkpoint, revision, fixture hashes, unique
task IDs, valid execution receipts, strict improvement, no case regressions and
at least 80 percent candidate accuracy. Even a passing pilot does not activate a
production model: incumbent comparison and serving compatibility remain open.
The first actual 12-step trial changed 5,898,240 adapter parameters and scored
1/8 versus its base's 0/8. The candidate was rejected and remains isolated.
Protocol failures count as failures; these scores measure usable coding output,
not code quality independent of response format. Three small training examples
prove the pipeline, not a sufficient dataset for general coding competence.

### Comparative task-board benchmark, 2026-10-04

The bounded goal was a new pure `gamma.task_board.analyze_tasks` evaluator:
dependency readiness, waiting reasons, status separation, stable priorities,
strict input validation, no input mutation and iterative handling of 2,000 tasks.
It is a benchmark module and is not connected to the dashboard or work scheduler.
Codex wrote a reference and froze 75 independent checks before Gamma inference.
Neither the reference nor those checks entered the first Gamma job's prompt.

The reference passed 75/75. Gamma's first candidate passed 74/75, accepting an
explicit null dependency list instead of rejecting it. Its local checker created
contradictory assertions and rejected the valid reference on five of twenty
checks. Attempts to satisfy these assertions reduced candidate correctness;
the first autonomous job stopped at `needs_checker` without applying source.

A separately recorded assisted job used 35 validated contract checks, without
reference code. Gamma implemented and installed its own solution in two attempts.
Both the installed Gamma solution and the reference passed a 75-check transfer
suite containing 40 new generated boards withheld from that assisted job.
Contract and transfer tests are retained as protected regressions. This result
establishes one small pure-module success with human-supplied verification, not
general project autonomy or a controlled ranking of the underlying models.
Ignored artifacts under `data/coding/benchmarks/task-board-20261004` retain the
original goal, frozen hashes, all seven first-run candidates, executed receipts,
both comparison reports and installed-source application evidence.
