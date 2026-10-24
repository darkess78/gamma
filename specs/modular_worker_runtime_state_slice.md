# State ownership implementation checkpoint

This checkpoint is historical. The remaining writer migration and current
validation/backup contract are documented in
[modular_state_writer_inventory.md](modular_state_writer_inventory.md).
Default and explicit-path persona stores, Presence, stream state, trust controls
and publication records now use the authority. The old non-dispatching outbox
and writable legacy-file descriptions below describe the earlier checkpoint.

This report describes the isolated Gamma-PC implementation on 2026-09-19.
It does not establish completion of Phase 2 or replace the proposal's acceptance
requirements. The implementation remains in `/home/neety/Documents/gamma-modular-phase1`.
The production checkout and services have not been changed.

## Implemented behavior

Continuity exchanges now have explicit generation ownership and a session state
version. Beginning an exchange records its base version. Completion checks that
the generation owns the pending exchange, its session and privacy scope match,
and the base version is still current. Journal completion, working state, summary,
version advancement and one decision outbox entry commit in one transaction.
Automatic memory candidates from session-bound text conversations join this
transaction in both fast and standard modes. The fast path proposes its fallback
candidates synchronously instead of starting an unowned background save. A stale
decision or a later transaction failure leaves no candidate facts behind.
Default assistant feeling and emotion stores now use versioned SQLite documents.
Session-bound conversation commits update both in the same transaction. Existing
JSON files remain untouched, and the first committed update preserves their
parsed contents in a separate immutable legacy column. Invalid or oversized
legacy input aborts the transaction. Explicit file-path constructors remain
legacy compatibility adapters; normal application construction uses SQLite.
Session-bound `save_memory` and `save_core_memory` calls prepare state proposals
without calling direct tool writers. The authority commits them with the decision,
and successful response receipts change from pending to committed. Model-supplied
memory session identifiers cannot redirect those writes. Core memory uses the same
document store and preserves the original Markdown in place and in the legacy
column. The persona prompt reads committed core facts through that store.

The tool boundary now checks an immutable grant set derived from resolved speaker
identity for every invocation. Known memory reads require read permission; writes
require write permission and enabled memory configuration; core writes additionally
require owner identity. Unknown capabilities fail closed. Tool arguments cannot
change those grants. This is the local tool gate, not the complete Phase 3 external
capability broker or delivery authorization system.

Sessionless text requests now receive a unique transient transaction identity,
without reusing another request's conversation history. Image requests use the
same owned response path. The former background-memory and direct persona-write
helpers were removed from conversation inference. Pending transient exchanges
and unresolved receipts survive retention; expired completed transient records
lose their checkpoint and version records after their journal is pruned.

Operator memory edits and standalone document writes invalidate in-flight state
versions atomically with their changes. A failed operator edit rolls back both.
Decision commits that change shared memory/persona state also invalidate other
sessions' snapshots, so stale decisions cannot overwrite shared corrections.
Sessionless text is also checked through a real HTTP request. Image ownership is
verified through the real conversation method with a controlled vision fixture.
The HTTP mock provider correctly rejects image inference as unsupported; this
does not establish live vision-model acceptance.
Inference runs outside that transaction. A conflicting decision raises
`StateConflict`; the conversation facade marks its owned exchange failed.

Service construction no longer interrupts database-wide pending work. The core
application starts an explicit bounded recovery loop. Recovery accepts a retired
generation or an expired generation whose local process is provably dead. An
expired lease on a live or unverifiable process grants no recovery permission.
PID birth time protects against PID reuse. Recovery cancels that generation's
undispatched decisions and interrupts only its pending exchanges.

The new tables are additive. A serialized schema initializer first retains an
SQLite online backup, including WAL contents, verifies integrity, and refuses
to migrate if backup creation fails. Backup files use exclusive creation and
private file permissions. Existing readers can read the unchanged journal and
checkpoint columns. Old writers do not enforce the new ownership/version rules
and must not run concurrently against this database. Rollback is an offline
operation after stopping all writers. Neither a database backup nor an outbox
record grants permission to replay speech or external actions.

Session deletion removes its journal, checkpoints, ownership and outbox, while
retaining an incremented version tombstone. Retention preserves pending work
and unresolved external-action records. It removes undispatched outbox records
only after their journal exchange has expired.

## Behavioral evidence

`tests/test_state_authority.py` checks independent service construction, live
expired leases, retired generation recovery, foreign/stale/duplicate commits,
transaction rollback after an injected summary failure, a real process crash
mid-transaction, and two real processes competing for one state version. It
also checks deletion, retention, WAL backups, backup failure and pre-migration
recovery data.

`scripts/verify_modular_state.py` starts a disposable authenticated Shana HTTP
server on Gamma PC with mock inference and isolated state. A real text request
creates the journal, version, summary and undispatched outbox entry. An
authenticated deletion invalidates the version and removes the outbox. The
script verifies the pre-migration backup and unchanged production service PIDs.
This proves route and persistence integration, not real model or audio behavior.

The input/operation integration checkpoint passed the complete Gamma-PC suite with 764
tests, 3 skips and 84 subtests. Evidence is recorded in the review logs alongside
the cumulative patch. A real subprocess crash and an injected failure after
persona updates leave neither persona document committed. Accepted and stale
decisions are tested in both fast and standard conversation modes.
The existing Starlette test-client deprecation warning remains. The real
dashboard-to-worker lifecycle/log verifier passed again after these changes.

The SQLite backup helper began as a scoped coding-worker proposal using the
configured free OpenRouter route. Its two supplied tests passed. Review found
silent backup failure and nonexclusive destination creation; both were corrected
before integration and are covered by the Gamma-PC tests. A second scoped worker
job for extracting the memory transaction helper returned no usable code after
truncation. Codex completed that extraction directly and validated it using real
SQLite rollback, existing-fact update, and fast/standard conversation tests.
A subsequent small policy task succeeded on the configured free OpenRouter route.
Its three stdlib tests passed; Codex reviewed the diff and integrated it with real
conversation tests for accepted, stale, disabled and unauthorized tool writes.

## Remaining Phase 2 work

The outbox is deliberately non-dispatching, with status `awaiting_authorization`.
It does not yet authorize or deliver output. Explicit legacy persona file adapters
remain for compatibility. Presence Wake now records an owned runtime event.
The persistent autonomous brain is still Phase 4 work. TTS has moved after
continuity completion in the delivery checkpoint. Stream and live voice
call the owned conversation facade, but their downstream publication/playback
paths still require Phase 3 acceptance.
See `modular_worker_runtime_delivery_slice.md` for current delivery evidence.

The continuity journal's `completed` state currently means a generated decision,
not confirmed playback. Delivery receipts, target leases and stale-output
rejection remain Phase 3 work. Cross-worker provider admission and a persistent
brain queue remain Phase 4 work.

## Re-run on Gamma PC

```bash
cd /home/neety/Documents/gamma-modular-phase1
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_modular_state.py
PYTHONPATH=$PWD/src .venv/bin/python scripts/verify_modular_workers.py
git diff --check
```
