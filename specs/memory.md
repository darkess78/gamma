# Memory

Status: Current
Last verified: 2026-06-22

## Ownership

Memory is Shana-owned domain state stored through `MemoryService` in the SQLite
database under `data/memory/`. Dashboard clients must use Shana APIs rather
than opening the database directly.

## Stored Records

- profile facts for the owner, another person, Shana, or general scope
- episodic memories with importance, tags, session, and subject metadata
- known people with trust, relationship, notes, and linked platform accounts
- recent assistant emotion episodes and patterns in their own service
- owner-approved permanent core memories in authority documents, with `data/core_memories.md` preserved as a read-only legacy source

## Conversation Continuity

Ordinary session continuity is stored separately from long-term memory:

- grouped user/assistant journal entries with completion, speaker, trace, target, and privacy scope
- a token-bounded recent-turn view
- a rolling session summary
- a structured working-state checkpoint
- the last durable text output per performer target

Raw turns are not automatically promoted into profile or episodic memory.
Journal retention defaults to 30 days and 500 completed exchanges per session;
session deletion removes its journal, summary, and working checkpoint.

## Behavior

- retrieval is bounded and selective
- common facts and preferences are canonicalized and deduplicated
- contradictory preference facts replace older values
- near-duplicate episodes merge
- speaker identity is resolved before memory/tool permissions are applied
- memory candidates are filtered by write mode, confidence, importance, and subject
- users can inspect, create, edit, selectively delete, or clear records

## Safety

- public or guest identities cannot receive owner-only memory/tool access
- private identifying information is filtered before output
- raw untrusted stream text is not promoted into durable memory automatically
- future streamer memory must retain explicit scope, TTL/retention, and deletion controls
- continuity retrieval must match the current speaker/audience privacy scope

## State authority

All persistent conversational writes use `StateAuthority.transaction`.
Generation-owned decisions atomically commit journal, working state, persona,
approved memories, tool receipts and the decision outbox. Operator memory,
identity/trust and Presence edits invalidate pending snapshots and revoke delivery
permissions in the same transaction. Reads that materialize known people also
use the authority and invalidate only if they actually insert a record.

Stream context and autonomous goals use version-checked, deduplicated effects.
Publication records bind committed decisions to their generation, session, grant
and target. Newer target output cannot be overwritten by delayed older output.
A publication record does not establish playback or permit replay after recovery.

See `modular_state_writer_inventory.md` for the complete writer inventory,
crash/concurrency evidence, schema backup and offline rollback procedure.

## Conversation delivery evidence checkpoint

Task 6A, 2026-09-25, in the isolated candidate. Core-live cancellation now
shares a per-turn boundary with the complete state commit and authorization.
Cancellation that wins first leaves the original user input canceled and
creates no decision, proposed memory, or grant. A commit that wins first remains
in the audit; subsequent cancellation revokes delivery without deleting history.

The prompt projection qualifies assistant history using the original
decision/target/audience-bound playback receipts and completed text WebSocket
send accounting. Completed audio is distinguished from text sent, whose reading
remains unconfirmed. Interrupted or missing playback outcomes do not establish
exact heard words. Generated replies without delivery evidence are omitted.
Rolling-summary and last-action prompt views use the same projection; stored
decision audit rows remain intact. Canceled, interrupted and failed user inputs
remain available as qualified context. Legacy or unacknowledged outputs have
unknown delivery, not assumed silence or assumed successful speech.

Thirteen deterministic integration cases exercise the real conversation, state,
review and delivery components with controlled providers. They cover cancellation
before and after commit, playback outcomes, late/foreign receipts, text delivery
despite audio cancellation, fictional memory correction/retrieval permissions,
and HTTP review timeout with retained uncertain admission until the controlled
request finishes. These tests do not establish real-model or physical acceptance.
