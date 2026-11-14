# Coding capability and model improvement

Status: Initial coding-practice and assessment foundation, October 4, 2026.
Idle practice, its dashboard controls and the LoRA candidate pilot were removed 2026-11-13.

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

Practice now runs only under the [coordination core](shana_coordination_core.md).
The dashboard practice controls, `gamma.coding.cli`, the idle practice worker and
its `gamma-coding-idle.service` unit were removed on 2026-11-13.

## Outstanding implementation

The older registered-scope repository coding path (`gamma.coding.repository`,
its Discord-status scope and dashboard panel) was removed on 2026-11-14.

## Owner coding goals (local models)

The owner wants self-coding to stay on local models, so Shana works the way a
small model can succeed: in small, checked steps. This is the only path for
repository coding; the older executor (whole-file proposals, a separate
checker model, automatic application with rollback) was removed on 2026-11-14.

**Queue.** The Coding page (`GET/POST /api/coding/work`, `POST
/api/coding/work/{id}/control`, `GET /api/coding/work/{id}/proposal`) and
Shana's owner-only `coding_goal` tool queue a goal with a model and a time
budget (1 minute to 8 hours, counted from when it was queued). One goal is
active at a time. Jobs live in ignored `data/coding/autonomous/work-*`
(`state.json`, `control.json`, `proposal.diff`, `report.json`).
`python -m gamma.coding.workflow --goal "..."` queues one from a shell.

**Worker.** `coding/autonomous.py` runs queued jobs one by one (started on
demand, or as `deploy/gamma-coding-autonomous.service`). Pause, resume and
stop take effect at the next step. A job that was running when the worker died
is marked interrupted and never replayed.

**Model calls.** `coding/local_model.py` (`BackgroundModel`) waits until no
model is loaded in Ollama, takes the shared backend slot as a background
caller so a waiting conversation turn always goes first, and unloads the model
after each call. Owner controls are honoured while it waits. A sent request is
never retried.

**Workflow** (`coding/workflow.py`):

1. A throwaway git worktree of the repository at HEAD.
2. `coding/split.py` splits the goal into at most 6 tasks, each changing at
   most 3 files and naming the pytest check that proves it. Code enforces the
   rules and asks the model once more with the problems, or returns a question
   (status `needs_owner`).
3. `coding/task_loop.py` works each task with tools (read, search, replace,
   write, test, done, give_up), one per step, seeing each result. Edits are
   limited to the task's files and "done" is refused until the check passes.
4. Changed source is checked for tricks that fake a pass, then the full suite
   runs. Checks run in the bubblewrap sandbox (no network, read-only).
5. `proposal.diff` and `report.json` are written. The job ends
   `awaiting_owner_review`, `needs_owner`, `blocked` or `failed`. Nothing is
   applied to running Shana (D5); the owner reviews and applies the diff.

**What it may change** (`coding/project_policy.py`): Python modules under
`src/gamma` outside the protected areas (coding, safety, persona, tools, API,
identity, supervisor, improvement, resources, agent, config and the main entry
points), and only *new* `tests/test_*.py` files. Existing tests prove nothing
broke, so a task can't edit them. Changed source may not exit the process, use
exec/eval, patch builtins or import test machinery.

**Status for Shana.** `coding_status` gives only status, stage, task counts and
whether a proposal exists; never the goal, file names or code.

Not done yet: a real run on Gamma PC (needs the GPU), and an "apply proposal"
button (today the owner applies the diff by hand).
