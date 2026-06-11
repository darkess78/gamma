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

## Storage layout and scale
- The portable SQLite database lives under `data/memory/gamma.db`; runtime backups and legacy copies stay under the same ignored runtime folder.
- Known people, linked identities, profile facts, episodic memory, stream temporary memory, Twitch trust, and stream self-goals remain separate relational tables in one database.
- SQLite uses WAL mode, a busy timeout, foreign-key enforcement, and composite indexes for subject/time, session/time, and platform identity lookups.
- Dashboard statistics report the resolved path, file size, journal mode, and row counts.
- SQLite fits the current single-host writer model. Move to PostgreSQL before introducing multiple Shana writer processes or sustained high-volume remote writes.

## Rule
Memory writes should be selective and meaningful, not raw full-log dumping.
