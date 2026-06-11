# Memory Spec

## Phase 1
Only minimal/no real memory required beyond future-proof interfaces.

## Later target
- profile facts
- episodic memory
- retrieval layer
- consolidation jobs

## Current dashboard identity records
- Known people are first-class SQLite records with name, relationship, trust, and operator notes.
- A person can link multiple platform identities, including Twitch, Discord, game, or future source names.
- Speaker identity resolution checks those linked account IDs before falling back to `config/users.toml`.
- Dashboard operators can edit or delete individual memory rows and known-person records.
- Existing `other_person` profile facts are materialized into known-person records without deleting the original facts.

## Rule
Memory writes should be selective and meaningful, not raw full-log dumping.
