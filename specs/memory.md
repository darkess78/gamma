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
- owner-approved permanent core memories in `data/core_memories.md`

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
