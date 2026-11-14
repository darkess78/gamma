# Isolated Shana coordination core

Status: Implemented first private slice for review. Not activated in live Shana.
Date: 2026-10-23.
Direction and authority: [implementation handoff](shana_coordination_core_handoff.md).

## What this slice does

One coordinator owns private Monitor text, bounded working notes and mood,
attention, awake/asleep state, activity decisions, and acceptance of offline
practice evidence. Ordinary text can continue while a practice worker runs.
Private text never requests TTS or publishes to the performer bus.

Practice status is a bounded capability. `practice status`, `practice progress`
and `practice result` render canonical evidence without inference. The model can
select `state_updates.activity_action=report_status` for an ordinary-language
progress question; the coordinator then renders the same checked receipt. This
keeps attempt exhaustion distinct from an expired time budget.

The capability is the fictional offline curriculum, with an explicit exercise
selection and its prerequisite closure. It reuses `PracticeTask`, protected
regression/feature checks, the Linux sandbox and completion receipts, withheld
generated-case evaluation, and bounded local practice inference. It cannot call
the repository executor, apply source, restart services, or train weights.

The conversation adapter reuses `ConversationService` in its side-effect-free
evaluation mode, including identity/recipient privacy, structured parsing,
leakage guards, delivery resolution and safety filtering. Its memory, persona
and journal services resolve inside the isolated checkout. It does not copy or
open production personal state. Tools, automatic memory extraction and legacy
state writes are disabled on this draft path.
The isolated local adapter uses structured JSON, disables thinking, caps context
at 8192 tokens and output at 1024 tokens, and rebuilds context through the existing
prompt budget manager. Ordinary adapter defaults remain unchanged.

The coordinator stores its accepted private exchange, compact working updates
and output in one SQLite transaction. It preserves exact private scope,
request IDs and input ordering. Failed, superseded and interrupted exchanges
do not become accepted history. This is the coordination slice's journal,
not a migration or replacement of the existing MemoryService/ContinuityService
records. Long-term personal-memory writes and live journal migration remain
outside this isolated milestone. Existing journal repairs remain intact.

## Explicit isolated hosts

`gamma.main` does not import or activate the new owner. Separate factories are:

- `gamma.coordination.app:create_app`, Shana-owned private API.
- `gamma.coordination.dashboard:create_app`, authenticated private Monitor.

The private Monitor host reuses the existing dashboard authentication, page,
and HTTP client. Its own typed proxies expose only authentication, static assets,
Monitor input and activity routes. Service, repository-coding and voice routes
were absent in the first slice. The isolated P5 extension adds only typed private
interaction routes described in `shana_private_interaction.md`. Ordinary private
Monitor text still requests no speech. Legacy performer connections and status polling do not start in
this host. The ordinary dashboard retains its existing stream/rehearsal input
default. Only the isolated host renders the private-text option and activities.

Private input requires both API authentication and explicit `owner_private`
audience selection. Dashboard login alone does not confirm a private physical
audience. Public output and speech fields supplied to the dashboard cannot
redirect private text. Request IDs survive an uncertain browser retry.
The private host rejects legacy input modes, public output targets and speech
requests before any remote call. It requires `GAMMA_COORDINATION_API_ORIGIN`
to select a separate literal loopback HTTP origin, excludes production ports,
and verifies that the authenticated backend identifies its isolated scope.

Factories fail closed without explicit opt-in, authentication and portable
isolated source. The Shana factory also requires local/mock inference, a
SQLite database under `data/coordination/`, the checkout's current working
directory, and no machine
local config files. Nothing installs a unit or changes existing ports.
`GAMMA_COORDINATION_STATE_DIR` optionally selects a fresh run directory inside
the checkout's `data/coordination/`. Storage paths reject parent traversal and
symlinks. This process-only setting does not edit machine-local configuration.

## HTTP contracts

The existing `/api/monitor/input` envelope remains `ok`, `event`, `result`, with
`result.assistant_response` and `result.decision`. For `input_mode=private_text`,
the dashboard proxies to `/v1/coordination/input` with `text`, `session_id`,
`request_id` and `audience=owner_private`. The response also includes its owner
generation. It communicates through `display_text`; `speech_text` is empty.

`GET /api/coordination/status?audience=owner_private` proxies to the corresponding
Shana `/v1/coordination/status`. It returns awake/focus/mood, compact notes,
activities and newly reconciled evidence. It never returns learner source or
withheld cases.

`POST /api/coordination/control` proxies to `/v1/coordination/control`. It accepts
an explicit private audience, stable request ID, and one action:

| Action | Bounded behavior |
| --- | --- |
| `practice` | Select a known exercise, 1-5 attempts per exercise, 30-1800 seconds. Return a durable run ID promptly. |
| `permit` | Authorize named exercises for at most two quiet-time starts and three discretionary reviews before expiry. |
| `focus` | Select focused or relaxed attention. Conversation does not suspend an existing worker. |
| `sleep` / `wake` | Suspend new Shana decisions, then reconcile actual worker outcomes on waking. Messages and worker events do not wake her. |
| `pause` / `resume` / `stop` | Control one authorized run at durable boundaries. Resume preserves attempts and expiry. |

A requested pause is distinct from a worker-acknowledged pause. An unknown
exercise, conflicting request ID, second active run, unsupported audience or
expired scope cannot silently expand authority.

## Ownership and recovery

The host holds a nonblocking OS process lease. Startup claims an owner and
generation with compare-and-swap. SQLite `BEGIN IMMEDIATE` checks that owner
inside each effect transaction, including accepted text, bounded state,
activity dispatch and result reconciliation. A late old-generation draft
cannot commit. Shutdown invalidates outstanding decision threads before
releasing the lease. A crashed handover rolls back atomically.

Workers retain evidence for already-authorized runs across owner changes. A
separate per-run OS lease prevents duplicate execution and reconnects a surviving
worker. Run/checkpoint references and observed revisions make dispatch and
catch-up idempotent. A new generation revalidates result artifacts and contract
versions without duplicating previously accepted outcomes.

Each complete immutable checkpoint includes exercise identity and contract
version, original inputs and original source reference, candidate source
reference/hash, stage, public check receipts and their source binding, bounded
feedback, consumed attempts and the next operation. The run record retains its
original checkpoint, budget/expiry and authorized scope. Artifact content is
saved and fsynced before the checkpoint reference commits. Content-addressed
artifacts reject corruption, symlinks and oversized data.

A saved candidate resumes with its checks, without regenerating that edit.
Interrupted disposable checks/evaluation may repeat. An interrupted or timed-out
model request remains blocked as uncertain, with its attempt charged; it is not
automatically replayed. Changed exercise/check implementations or missing
artifacts require review. No recovery step replenishes budgets or treats
infrastructure failure as model success or a training requirement.

Completion requires protected public checks plus a source-bound withheld
evaluation. Withheld cases, seeds and detailed failures never enter learner
feedback, checkpoints or training examples. All results retain
`production_verified=false`; collecting practice evidence does not update
personality, long-term memory or model weights.

## Foundation continuation, October 23

The user authorized finishing P1 with workers and verification of the full
connected flow, while retaining isolation until separately approved activation.
The implementation now adds durable user-requested exercise intentions and
conversational `request_practice`, `pause`, `resume`, `stop`, and `switch`
decisions. New work still requires an explicit finite exercise permit. A queued
intention reserves one allowed start; execution retains its original absolute
deadline and attempts. Switching requests a safe pause of other active work and
preserves its candidate. It does not claim an immediate worker acknowledgment.

Progress receipts can select an exact activity ID, include that ID, and require
verified per-task evidence before claiming success. They preserve otherwise
valid focus, mood and compact-note updates from the same interaction. Expired
paused work and intentions retain their evidence and become blocked; they do
not permanently suppress fresh authorized work.

Scheduled review follows the most recent accepted private session instead of a
fixed session name. Focused availability permits nonverbal background work;
sleep, foreground activity, authorization, deadlines and worker capacity still
gate decisions. Context explains available exercises and their purposes. Waiting
remains a valid model choice, with a next review bounded by current permission.

Worker proposals were independently inspected and integrated with additional
corrections. A worker pass did not certify the integration: review caught missing
target identifiers, stale projected status, and expiry handling that the worker
contract did not cover. New integration and independent helper tests cover those
cases. Unsuccessful worker outputs and real-model attempts are retained.

`scripts/verify_coordination_foundation.py` drives the actual private Monitor in
an installed headless browser, real local inference, and protected practice
checks. Its two explicit timing fixtures hold a saved candidate and a check
boundary. They do not choose model decisions, replace proposals or fake checks.
It crashes the isolated host and starts a different process before resuming the
same candidate, then tests sleep completion and wake catch-up. This new proof is
separate from the historical rejected legacy Monitor/performer consumer probe.
The connected real-model result remains pending until its receipt is reviewed.

The continuation also keeps essential private coordination policy, permission
and brief current activity state in a required prompt layer. Recent conversation
history remains compactable. Model-facing activity summaries omit source/check
payloads, cap individual strings, and retain at most six recent activities;
canonical receipts and durable checkpoints still read the full stored evidence.
The structured repair uses the same prompt-budget builder with space reserved
for its repair instruction. Private diagnostics retain layer names, compaction
actions and validation field/error codes, never rejected model payloads or raw
reasoning. A quiet `request_practice` decision is accepted as the same permitted
practice capability; it grants no quiet-time lifecycle authority.

## Scheduling and resources

The coordinator batches worker observations without model calls. A discretionary
review requires awake, idle foreground state and an unexpired explicit
permit. The model can choose practice or wait and request its next reconsideration
in 30-3600 seconds, capped by a still-active permit deadline. A permit has at most three reviews. Sleep and
unchanged not-yet-due conditions do not trigger repeated inference.

The coordinated practice path does not use the legacy sleep-only idle scheduler.
Already-authorized work can continue awake or asleep. Both conversation and
practice use the existing process-safe `backend_slot`; foreground waiters have
priority over later background admission. Practice yields before HTTP when
foreground/control conditions change, without charging an unsent model request.
The coordinated learner accepts its own resident model while holding admission;
the legacy learner retains its conservative occupied-endpoint behavior.

Admission is cooperative and nonpreemptive. A request already running can delay
a later conversation by its bounded operation. External clients that bypass
admission are not reserved or preempted. This implementation does not establish
a conversational latency guarantee or universal GPU arbitration.

## Verification and remaining limits

The [P1 acceptance record](shana_foundation_acceptance.md) records the subsequent
connected foundation work and its final verification. The observations below
describe the earlier isolated slice and remain historical evidence.

`tests/test_coordination_core.py` exercises the actual private HTTP ingress,
including a complete practice run through the real Linux sandbox and withheld
evaluator. Other scenarios cover saved-edit recovery, uncertain inference,
pause/resume budget preservation, concurrent conversation, sleep catch-up,
duplicate effects, stale generations, contract changes and handover failure.
`tests/test_coordination_dashboard.py` crosses real dashboard middleware,
service/client and Shana HTTP handlers, including rejected anonymous access.

`scripts/verify_coordination_slice.py --model <local-model> --endpoint <origin>`
is an explicit Gamma-PC smoke using temporary localhost listeners and the real
factories, credentials generated only in memory, private Monitor proxies, local
inference, practice and result reporting. It refuses a resident endpoint at
preflight and writes its fictional-interaction receipt under ignored
`data/coordination/`. It does not start or restart production services.

Observed verification on October 23:

- Current focused suites passed 177 tests and 75 subtests on Gamma PC. Both
  changed browser scripts passed Node syntax checks. The actual Monitor-script
  contract also verified deferred-input retention, stable uncertain-request
  identity, private text rendering and absence of legacy connection startup.
- A final broader run, with real-model smoke stopped, passed 2128 tests and 90 subtests, with three skips and two
  failures. The spec-reference and public filtered-audio expectation failures
  also reproduced from untouched checkpoint source. An existing Discord reviewer
  test cannot collect because its synthetic package lacks backend admission;
  this reproduced on checkpoint source and that file was excluded from the
  broader run. Setting a private `TMPDIR` resolved an unrelated teardown fixture's
  missing-environment error. The full suite is not green.
  A concurrent run also hit timing-sensitive publication-test failures; the
  final run without concurrent inference passed those cases. No safety gate or
  public publication implementation was changed to obtain that result.
- Preservation checks matched 340 source/test files in the original Windows
  tree, all 700 production checkpoint files and all 763 older-candidate files.
  The four production service PIDs remained unchanged.
- The real 4B smoke verified prompt run handoff, foreground chat, sleep catch-up,
  deduplication and canonical status rendering. Its exercise failed after three
  attempts and its conversational behavior included a guarded fallback. It did
  not reliably select the status capability from ordinary language. That model
  run is not accepted as proof of satisfactory conversation or coding quality.
- The default Qwen3.8 27B model, on the same isolated path with the explicit 8K
  context cap, passed the selected retry exercise on its first attempt, including
  protected public checks and withheld evaluation. Dispatch took 54.1 ms; chat
  submitted while work was running took 14898.2 ms. Wake reconciled completion
  once and the explicit status capability rendered its verified evidence. Its
  ordinary-language status reply summarized both current success and retained
  older failures, and did not select the canonical status capability. This is
  evidence for the narrow path, not a latency, conversational-quality or broad
  autonomous-activity acceptance. Real discretionary selection remains unproven.

The scheduled quiet-time check exposed a conflict in the reused prompt: its
allowed state fields excluded the new coordination decisions. The internal
`coordination_mode` now advertises the bounded extension only for side-effect-free
owner-private drafts. After that repair, receipt
`data/coordination/smoke-4724b1462d9f461a846accacdd009357.json` recorded a valid
nonverbal `quiet_time_review`: Shana selected wait and a 300-second reconsideration
after the actual scheduler due time. No dialogue or new worker was emitted.
This verifies a real bounded wait choice, not autonomous project initiation.
Unpermitted model dispatch claims are replaced with an honest capability receipt.

The isolated receipts are `data/coordination/smoke-4f3ffba5761c41a797d9bef84b89b4a5.json`
and `data/coordination/smoke-3eff6db193ad445d83336fc5494e7a9c.json` on Gamma PC.
They retain the unsuccessful 4B attempt and the successful 27B attempt separately.
Run results remain private ignored artifacts. The code and documentation are
uncommitted on `refactor/shana-coordination-core`; nothing was pushed or activated.

Persistence and scenario tests cannot establish that the experience feels like
one continuing Shana. Familiar interaction, voice, other channels, personal-state
migration, long-term character/skill learning, live handover and rollback still
need their own review. The current live owner remains unchanged. Activation
requires the user's separate approval and a controlled single-owner cutover.
