# Stream state writer migration

The authority owns stream temporary context and autonomous goal proposals in the
same SQLite database as conversation decisions. This source change was made in
the isolated Gamma worktree; it does not deploy or restart production workers.

| Persistent writer | Caller and ownership | Evidence |
| --- | --- | --- |
| `StreamTempMemoryStore.record_turn` | `StreamBrain` writes public event history and recent activity inside one `decision_transaction(..., effect="stream_state")` | Atomic rollback test and existing public-event/context tests |
| `StreamTempMemoryStore.add/upsert` | Internal autonomous callers must supply the validated authority session; standalone calls are operator transactions and invalidate pending snapshots | Raw-session and foreign-database rejection; operator invalidation test |
| `StreamTempMemoryStore.clear` | API operator clear uses an authority operator transaction | Parameterized control test rejects a pending decision after clearing |
| `StreamSelfGoalStore.propose` | Lull evaluation uses the same decision transaction as temporary context | Effects commit together; approved goals retain their description on late reproposal |
| `StreamSelfGoalStore.set_status/clear` | API approval, rejection and clearing use authority operator transactions | Parameterized approval/clear invalidation; approval/reproposal protection |
| Stream policy without a model response | `StreamBrain.handle_event` prepares an owned event before evaluating policy and completes that decision before persistent effects | Operator mutation during policy evaluation rejects the old result |
| Canned stream output | The pre-policy event decision becomes the response's `route_trace_id` before authorization | Existing canned-response and output tests run through actual state persistence |
| Conversation-backed stream output | The conversation validates its parent event snapshot when beginning, then its committed decision and generation own downstream effects; the unused outer event is cancelled | Operator change during background context assembly is rejected before inference; foreign-generation effect rejection |

The store adapter does not commit a caller's transaction. It verifies that the
session is authority-owned and bound to the same database, then executes the
existing stream SQL through that transaction's connection. Readback uses the
same connection so uncommitted results remain isolated. A failed second write
rolls back the first; duplicate effect identifiers cannot apply again. Exceptions
are propagated instead of silently losing stream persistence failures.
The parent-event snapshot check occurs in the transaction that begins the nested
conversation. An operator correction between stream policy/context evaluation
and conversation preparation therefore cannot be silently accepted as new input.

An effect commits after the conversation decision and before publication. A
crash before the effect commit leaves no partial goal/context mutations. A crash
after effect commit cannot repeat that effect. This does not replay an external
message or speech: delivery remains governed by its own durable authority
publication record and grant.

## Schema backup and rollback

`memory/stream_schema.py` registers the existing stream tables and indexes in the
authority metadata. Their names, columns, unique temporary-memory key and
existing rows are preserved. Store constructors no longer run independent
`CREATE TABLE` or index mutations. The authority's serialized schema initializer
takes and integrity-checks an SQLite online backup before adding any missing
table or index; backup failure aborts migration. Existing stream rows need no
data conversion.

Rollback requires stopping every writer to the affected database, retaining the
current database and WAL as evidence, and restoring the verified pre-migration
backup together with compatible source. Old stream writers cannot run alongside
the authority because they do not enforce decision versions or operator
invalidation. Restoring a database never authorizes publication replay.

## Validation

`tests/test_stream_state_authority.py` uses isolated real SQLite databases for
operator invalidation, atomic effect rollback and retry, duplicate rejection,
foreign generation rejection, wrong database and raw-session rejection, approved
goal preservation, two-record turn rollback, and an operator edit during actual
stream policy evaluation and nested-conversation context assembly.
`tests/test_stream_brain.py` now isolates its database
per test and gives injected stores the same database as the authority.
The focused Gamma run passed 47 tests across these two files; `git diff --check`
was clean. The run used the isolated `/tmp/gamma-state-test-root-20260920` project
root and database with `PYTHONPATH=$PWD/src .venv/bin/python`.

A scoped OpenRouter coding-worker proposal was attempted. It returned no usable
content because its output was truncated. No worker patch was integrated; Codex
implemented and reviewed the scoped changes directly.
