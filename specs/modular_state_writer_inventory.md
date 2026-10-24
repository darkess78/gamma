# State authority writer completion

Authoritative source: `/home/neety/Documents/gamma-modular-phase1` on Gamma PC.
This change does not deploy, commit, push, restart production, or update either
other checkout. Existing source work in the authoritative checkout is preserved.

## Ownership and writer inventory

`StateAuthority.transaction` now owns persistence transactions. Model results
need an owned pending exchange or a checked committed-decision effect. Operator
transactions atomically invalidate snapshots and cancel delivery permissions.
An arbitrary SQLModel session cannot write state documents, candidate memories,
or stream effects through their public mutation helpers.

| State and writers | Caller and completed migration | Executable evidence |
| --- | --- | --- |
| History, summaries, working checkpoints: `memory/continuity.py` begin/complete/fail/delete/prune | Text, image, voice roundtrip, core live, Wake and silent cycles use owned exchanges; writes run in authority transactions | `test_state_authority.py`, `test_conversation_continuity.py`, `test_silent_preparation.py`, `test_state_publication_ownership.py` |
| Decision versions and outbox: `memory/state_authority.py` begin/complete | Generation, session, scope and base version are checked before journal, persona, memories, tools and outbox commit | Real competing processes and voice/text/autonomy thread tests; rollback and subprocess crash tests |
| Automatic and approved memories: `MemoryService.persist_candidates_in_session` | Conversation proposals join the decision commit; standalone operator edits use invalidating authority transactions | Fast/standard real conversation tests and `test_tool_authority.py` |
| Memory operator create/update/clear and known-person save/delete | Every existing API writer uses the authority operator transaction; failed edits roll back invalidation | `test_operator_mutations_invalidate_old_decisions_and_failed_edits_rollback` |
| Known-person materialization from `get_known_people` and `stats` | Former hidden read-side writes now serialize and invalidate only when inserting; ordinary readbacks do not invalidate | `test_people_materialization_invalidates_once_and_readbacks_do_not` |
| Assistant feelings and emotional episodes/patterns | Both default and explicit-path constructors use authority documents. Explicit paths use isolated SQLite sidecars; original JSON is read-only | `test_persona_authority_ownership.py`, persona rollback/crash tests |
| Core memories: `CoreMemoryStore.append` | Validated tool proposals join the decision transaction; original Markdown is preserved as a legacy source | `test_tool_authority.py` and existing core-memory authority tests |
| `SaveMemoryTool.run` and `SaveCoreMemoryTool.run` | Direct execution rejects missing decision ownership; conversation's tool gate prepares proposals instead | Direct tool rejection tests and accepted/stale/unauthorized tool tests |
| Tool result records | Tool receipts are stored in the atomic outbox payload even for silent decisions; visible journal metadata remains valid JSON | `test_silent_tool_results_survive_in_atomic_decision_record` |
| Legacy incremental live voice | Experimental entry routes through the owned simple-chunked conversation path; removed late standalone memory persistence | `test_live_voice_runtime.py`, `test_persona_authority_ownership.py` |
| Stream event history, recent context and chat activity | `StreamTempMemoryStore` uses the same database and an owned, deduplicated decision-effect transaction | `test_stream_state_authority.py`, `test_stream_brain.py` |
| Autonomous goals and operator goal approval | Lull proposals share the stream effect transaction; operator edits invalidate snapshots; reproposals cannot overwrite approved goals | Stream effect rollback, operator clear/approve and approved-goal tests |
| Canned, ignored and model-backed stream decisions | An exchange begins before policy/context reads. Nested conversation validates the parent snapshot atomically when it begins | Operator edits during policy evaluation and context assembly are rejected |
| Presence lifecycle, Wake history, scheduler telemetry and activity | Existing CAS revisions now live in authority documents. Control edits invalidate decisions; telemetry does not invalidate its own decision | `test_presence_authority.py`, `test_presence_revision.py`, proactive suites |
| Persistent performer mute controls | Authority mutation merges only the requested target against current persisted controls; stale bus instances cannot replace another target's mute | Stale-bus merge and pending-decision invalidation tests |
| Viewer trust operator edits | Existing trust rows/schema are retained under authority transactions and backed-up schema initialization | `test_trust_state_authority.py` |
| Publication records and durable last output | Grant dispatch, publication sequence and outbox transition commit atomically. Every record is bound to decision, session, generation, target and grant. Delayed older output cannot replace a newer target projection | Publication rollback, stale target and real HTTP tests |
| Delivery grants, targets, artifacts and playback leases/offers/receipts | Delivery, playback and maintenance facades share authority transactions. Checks reject cancelled decisions and non-live/recovered generations | Delivery/playback suites and operator revocation HTTP test |
| Recovery | Only explicitly retired owners or expired owners proven dead are eligible. Unknown process ownership remains untouched | Live expired lease, unverifiable process, generation separation and real subprocess crash tests |

See `modular_state_stream_writers.md` for the stream transaction details.
The legacy incremental voice mode retains its selection setting but currently
uses the committed simple-chunked path. It no longer emits incremental speech
before a decision commits.

## Concurrency and publication semantics

A stale inference result cannot commit history, working state, persona, memories
or tools. Shared changes invalidate other pending snapshots; stream effects also
advance their own session version to invalidate work prepared before the effect.
Foreign generations and duplicate effects cannot apply. Starting a service or
recovering one generation does not interrupt another generation's pending work.

Already committed output is immutable. New cognition may continue while that
output waits for or undergoes playback. Ordinary later decisions do not cancel
it merely by advancing state versions. Operator changes and generation recovery
explicitly revoke outstanding grants and outbox permission. Publication uses a
monotonic committed-decision sequence to prevent late delivery from replacing a
newer target record, including across sessions.

`dispatched` means publication was durably accepted. It is not proof a listener
received or heard it. Playback receipts retain that separate meaning. A crash
after dispatch but before an external consumer sees the event is not retried
automatically, since retry could duplicate an action. The durable record remains
evidence, not permission to replay.

Stream effects are a checked transaction after decision commit and before
publication. A crash before that effect commits leaves no partial context/goal
updates; a committed effect cannot repeat. Recovery cancels the dead generation's
remaining publication authority rather than reconstructing speech or actions.

## Persistence outside conversational authority

JSONL stream outputs, trace files, timings and common worker logs remain diagnostic
records. They do not establish committed Shana state or grant publication/replay
permission. The in-memory performer bus and spoken-turn view remain presentation
projections. The new publication ledger is the durable source of publication
identity and ordering.

Transport ingress/deduplication receipts, provider admission leases, subprocess
lifecycle status, audio/image artifacts and improvement experiment manifests
remain owned by their respective runtime domains. They cannot update Shana's
approved memories or persona. Local configuration and secrets are unchanged.

## Schema backup and rollback

The new `gamma_decision_commit`, `gamma_decision_effect` and `gamma_publication`
tables are additive. Existing stream context, goals and viewer trust schemas
are registered with the authority instead of independent constructor DDL.
Schema initialization is serialized and performs an SQLite online backup before
creating missing authority tables or indexes. The backup includes WAL data,
uses exclusive creation/private permissions and must pass integrity validation.
Backup failure aborts initialization. Existing state rows are not rewritten.

Existing persona, core-memory, Presence and performer JSON/Markdown sources are
preserved. First document mutation retains parsed legacy content in
`gamma_state_document.legacy_payload`. Default constructors use the shared state
database. Explicit compatibility paths use `<legacy-path>.state.sqlite3`; the
legacy file is never updated to mirror newer state.

Old code can read unchanged legacy columns, but old writers cannot enforce the
new contract and must not run concurrently. Rollback is offline: stop every
writer, preserve the current database and WAL, restore the verified backup with
compatible source, then explicitly re-establish operator permissions. Newer
post-backup changes are absent after restoration. Reverting to legacy JSON alone
also loses subsequent document updates; export them explicitly if required.
Neither a restored database nor old output records authorize replay. Pre-upgrade
outbox rows lack the new commit sequence and cannot be published by this code.

Session deletion removes its publication content and decision-effect/sequence
records. Retention keeps unresolved publication evidence; orphaned effect and
sequence records are removed with expired continuity ownership.

## Verification

Application tests ran only on Gamma PC with `PYTHONPATH=$PWD/src .venv/bin/python`.
The test project root and database were isolated under
`/tmp/gamma-state-test-root-20260920`; no production database or local configuration
was copied into it.

`test_state_publication_ownership.py` includes real process termination after
preparation, after state commit, during publication before commit, and after
publication. It checks database rows, scoped recovery and rejected replay, not
only returned status. Existing tests also kill a process during the state
transaction and race two real processes for one version.

`scripts/verify_state_publication.py` passed against a disposable authenticated
HTTP server with mock inference and stub voice. It verified owned text/voice
publication, atomic revocation after an operator memory edit, publication-content
deletion, pre-migration backup integrity and unchanged production service PIDs.
This is state/API evidence, not a live microphone, GPU quality or production
deployment claim.

Three scoped coding-worker attempts used local Qwen, OpenRouter and Requesty.
All exhausted output limits without usable proposals; no generated patch was
integrated. Codex completed and reviewed the scoped changes directly.

Validation results:

- Full Gamma suite: **1,137 passed, 3 skipped, 100 subtests passed** in 96.82 seconds.
  Log: `/tmp/gamma-state-full-final.log`.
- After the final identity/preparation transaction and delayed-interaction guards,
  the affected conversation, tool, state, Presence and stream suites passed
  **106 tests** in 9.53 seconds. Log: `/tmp/gamma-state-final-focused.log`.
- `scripts/verify_state_publication.py` passed all five state/API checks.
- The updated `scripts/verify_modular_state.py` passed all 28 checks, including
  authenticated state/tool commits, Wake, core live, local stream synthesis,
  dashboard playback proxy, Chromium worker controls and scoped child shutdown.
  It confirmed unchanged production service PIDs. Log:
  `/tmp/gamma-state-modular-http.log`.
- `git diff --check` passed after integration.

The full suite retains the existing Starlette test-client deprecation warning
and the multiprocessing warning for the intentional fork-inheritance test.
The final scoped run passed without warnings.

Identity lookup and exchange preparation now share one short transaction so an
operator trust edit cannot occur between them and be mistaken for the snapshot
used to resolve permissions. Inference remains outside the transaction. Delayed
interaction telemetry validates its decision/generation before changing Presence.
Local stream calls defer provisional conversation delivery, then cancel that
unused grant before stream authorization, so one decision is published once.

## Changed implementation files

Paths are relative to the authoritative checkout. Shared files also contain
preserved work from concurrent modular-refactor tasks.

- `src/gamma/memory/state_authority.py`
- `src/gamma/memory/continuity.py`
- `src/gamma/memory/service.py`
- `src/gamma/memory/documents.py`
- `src/gamma/memory/stream_schema.py`
- `src/gamma/conversation/service.py`
- `src/gamma/delivery/authority.py`
- `src/gamma/persona/assistant_state.py`
- `src/gamma/persona/emotion_service.py`
- `src/gamma/tools/builtin.py`
- `src/gamma/stream/brain.py`
- `src/gamma/stream/state_store.py`
- `src/gamma/stream/temp_memory.py`
- `src/gamma/stream/self_goals.py`
- `src/gamma/integrations/twitch/trust.py`
- `src/gamma/presence.py`
- `src/gamma/performer/bus.py`
- `src/gamma/run_live_voice_worker.py`

New behavioral tests are `test_state_publication_ownership.py`,
`test_stream_state_authority.py`, `test_trust_state_authority.py`,
`test_persona_authority_ownership.py` and `test_presence_authority.py`.
Existing continuity, state, delivery, Presence, stream, live-voice and Twitch tests
were updated to use owned fixture decisions and the single state database.
`tests/state_fixtures.py` creates real committed decisions for delivery-only tests.

Specifications updated: this inventory, `modular_state_stream_writers.md`,
`modular_worker_runtime_state_slice.md`, `modular_worker_runtime_progress.md`,
`memory.md`, `current_implementations.md` and `specs/README.md`.
Verification scripts: new `verify_state_publication.py`, updated
`verify_modular_state.py`, and an error-body assertion in `verify_playback_proxy.py`.

