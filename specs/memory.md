# Memory

## Staged candidate response views, Slice 2B-3B

`AssistantResponse.memory_candidates` is an empty list for shared, default and
unbound recipients. Explicit authorized private recipients receive detached
copies of the full eligible candidate list, including nested tags. Both fast
structured and normal structured, metadata and heuristic response paths use
the same validated internal recipient context. Caller/model flags cannot select
the private view. The existing field and list schema are unchanged.

Persistence uses the original internal candidates and existing write eligibility
before response selection. Shared or unbound turns still learn eligible facts;
empty candidate metadata does not mean that no memory was saved. Guest/unknown
and evaluation no-write behavior remains unchanged. Returned private candidates
can be mutated without changing persistence inputs or another recipient's view.

Unbound operator HTTP clients, stream responses and Discord forwarding lose
candidate inspection detail until trusted private ingress is bound. Operator
timing logs count the selected response candidate view; the separate saved count
continues to describe persistence. Evaluation consumers must not treat empty
candidate metadata alone as proof of no writes. No private grant, learning ban,
storage migration, subject/session change or prompt change is introduced.

This response-view slice did not add attribution/provenance; the later
[source-aware attribution repair](#staged-source-aware-private-memory-attribution)
closes that bounded gap. Foreign-record authority, private ingress and broader
output/trace disclosure remain separate limits.
This staged response view is not a full-privacy guarantee and is not activated.

## Staged core-memory execution authority, Slice 2B-3A

The shared conversation tool executor checks `save_core_memory` before calling
the tool. It requires a present resolved speaker profile with `is_owner is True`
and `trust == "owner"`, matching the existing strict coding-tool owner predicate.
Structured and metadata-generated requests use the same check. Missing,
nonowner or contradictory profiles receive a bounded `owner_required` denial
without touching the core file. Model arguments cannot supply this authority.

Execution authority remains separate from recipient disclosure. Valid owners
can save core memory in normal, fast, brief and micro modes, including shared or
unbound recipient contexts. Shared results retain the existing finite
acknowledgments; explicit authorized private views retain full results. Inferred
owner remember calls and the existing omitted-speaker local-owner resolution
remain compatible. General tool permissions, evaluation no-effects behavior and
coding checks are unchanged. This staged repair has not been activated.

Foreign-subject search/save authority, private HTTP/voice ingress and
output/trace authorization remain separate limits. Structured/background candidate
provenance is covered by [source-aware attribution](#staged-source-aware-private-memory-attribution). The post-execution stream action plan does not add manual
approval for owner core writes. No new approval workflow or identity migration
is introduced.

## Staged background and wake context views, Slice 2B-2C

ConversationService selects unclassified free-form `background_context` and
`wake_context` after validating the existing immutable server recipient and before
reading or assembling these strings. Shared/default views omit the strings;
explicit authorized private views retain them with the existing prompt budgets.
Initial prompts, budget rebuilds, repair/fallback and finalization use the selected
view. Owner recognition, audience, interaction mode, model/request fields and
context text cannot grant private disclosure. No work-content placeholder is
inserted. Safe authored Shana context and generic wake generation remain available.

StreamBrain/Twitch/Discord, voice roundtrip/live worker and PresenceService still
produce their context and run normally. Their unbound/shared ingress supplies no
private grant, so their free-form enrichment is omitted until a trusted binding or
reviewed public provenance is established. This changes contextual richness, not
execution authority or work lifecycle. No work is canceled, reset, erased or
disabled. Caller interfaces and private token compaction are unchanged.

See [dynamic narrative views](#staged-dynamic-narrative-views-slice-2b-2a) and
[authored persona views](persona.md#staged-recipient-views-for-authored-persona)
for config/relationship disclosure. Foreign-subject authority, historical journal
provenance, general output/trace authorization and trusted private ingress remain
separate limits. This staged view is not
a full-privacy guarantee and has not been activated.

## Staged tool result views, Slice 2B-2B

Tool execution and disclosure are separate. ConversationService binds the existing
internal immutable recipient context before execution, then passes only its selected
result view to direct replies, tool finalization, AssistantResponse and journal tool
metadata. The metadata-generated tool branch uses the same boundary. Tool/model
arguments cannot select a private view. Explicit authorized private views preserve
full result fields and existing execution arguments; shared/default views copy only
finite known tool names, success/failure and constant operation acknowledgments.
Unknown/new tool names become `unclassified_tool` until reviewed. No raw output,
nested metadata, stored counts, saved candidates, person/task/artifact names,
provider endpoints or exception details are public by default. Verified finite
`coding_goal` queued status is deliberately shareable. An acknowledgment confirms
the operation, never claims that private memory does not exist.

Public response/journal tool calls retain finite names with empty arguments. Actual
execution uses the original calls, and structured draft tool arguments are removed
before shared finalization. The API result/call shapes remain unchanged. Views copy
data without resetting stored state or mutating concurrent readers. Existing speaker
and verified-owner execution rules remain in force; private disclosure does not
create execution authority.

Later [dynamic narrative](#staged-dynamic-narrative-views-slice-2b-2a),
[persona](persona.md#staged-recipient-views-for-authored-persona) and
[background/wake](#staged-background-and-wake-context-views-slice-2b-2c) views
close the corresponding prompt-disclosure gaps. Historical journal provenance,
general output/trace authorization and trusted private ingress remain separate.
Foreign-subject search/save authority is distinct from source-aware attribution;
core-save owner authority is enforced by Slice 2B-3A. Private disclosure does not
grant execution authority or semantic verification. No full-privacy or activation
claim follows from this tool-result slice.

## Staged dynamic narrative views, Slice 2B-2A

The internal recipient context selects a read-only prompt view. Shared and
unbound/default turns exclude `data/core_memories.md`, emotional causes, targets,
notes, timestamps, episodes and patterns, plus mapped private speaker names and
known-person notes. These are dynamic remembered narratives, not authored core
persona. Explicit authorized private recipients retain the existing narratives.

Public feeling state retains only the seven existing finite emotion labels,
validated recent emotion labels, finite numeric intensity clamped to 0..1 and
exact integer decay clamped to 0..10000 for display. Invalid scalar/enum values
render neutral/zero and cannot introduce arbitrary persisted text. The view does
not mutate state, disable tracking, rewrite files, reset memory, or change identity,
subject or tool authority. Resolved speaker profiles do not retain a separate
public input alias; the prompt uses finite generic role labels rather than mapped
private names. Private identity display remains available in explicit private views.

Authored `core.md` and boundaries remain byte-identical. The complete reviewed
sixteen-field YAML character is explicitly declared in `authored_public` under
`contract_version: 1`; recipient views and migration are specified in `persona.md`.
Shared/unbound prompts exclude legacy/private persona layers, relationship-state
JSON, operational memory-config dumps, and invalid/unknown numeric style fields.
Private content and runtime memory controls remain intact. Tool-result and
background/wake disclosures have separate staged recipient gates described above.
Remaining gaps include tool memory subject binding, public journal provenance,
output/trace authorization, and private
HTTP/voice bindings. These staged slices are not a full privacy or compatibility
guarantee and have not been activated.

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

## Staged journal ownership and recovery

This finite repair is implemented and tested in the isolated
`gamma-foundation-rework` branch. Production activation and live database
migration have not been performed.

Each new pending journal exchange carries an opaque service owner token.
An internal owner registry records the local host, PID, and exact process creation
time. Constructors initialize schema and storage; they do not interrupt pending
exchanges. Process identity fields are excluded from continuity API payloads.

Shana's application lifespan owns startup reconciliation and a 30-second
maintenance loop. Each pass examines at most 100 pending rows and removes at most
100 owner records without pending references. Inspection order advances so live
owners do not permanently hide dead owners. Batch failures log only an error
class and retry on the next pass. Shutdown signals the loop and waits for the
current batch; it does not detach a database thread. SQLite writer waits use the
existing five-second busy timeout. Table lookups and writer scheduling can still
take time proportional to database size and contention; the batch bounds rows
examined and changed, not a hard wall-clock deadline.

Recovery requires same-host evidence that the recorded process no longer exists
or is an exact matching zombie/dead process. A live process, changed PID creation
identity, other-host identity, inaccessible process, or invalid metadata is
preserved. There is no timeout or lease that silently turns stalled work into
dead work. Live-but-stalled owners require a separate operational decision.

Completion and failure require the original service/process owner and a pending
user journal entry. Completion also checks session and privacy scope. A rejected
terminal operation cannot write an assistant journal row, summary, or checkpoint.
The terminal transition, optional assistant text, working checkpoint and summary
commit in one SQLite transaction. Concurrent duplicate completion commits at
most once. Silent completion still records no fabricated assistant utterance.
Retention preserves all pending rows, including legacy rows. Explicit session
deletion remains available. Registry cleanup preserves every pending reference;
a service with no pending work may have its unused registry record removed and
recreates it on its next exchange.

Migration adds a nullable indexed `owner_id` column and the internal
`conversationjournalowner` table. SQLite schema checks and migration use a
serialized transaction. Completed legacy journal, summary, checkpoint and output
records retain their values. Legacy pending rows have no verifiable owner:
ordinary construction, reconciliation and retention preserve them. A controlled
conversion requires stopping every legacy writer, taking and verifying a SQLite
backup including WAL contents, inventorying ownerless pending rows, and explicitly
classifying those stopped exchanges. This repair does not perform that conversion.

Before activation, stop affected writers through their existing lifecycle
controls, retain scoped source baselines and a verified SQLite backup, and avoid
mixed old/new writer code. The old constructor globally interrupts pending work.
A source rollback therefore requires stopping writers first and retaining a
fresh database backup. Do not restore the pre-activation database over newer
valid turns without a deliberate recovery decision.

Staged Slice 3D closes an owned pending journal as `interrupted` when a
non-Exception control-flow interruption escapes `ConversationService.respond`
after journal creation, including `KeyboardInterrupt`, `SystemExit` and
`asyncio.CancelledError`. The original interruption is re-raised unchanged.
Existing owner/pending fences preserve foreign work and terminal rows; cleanup
does not create assistant text, summaries or checkpoints. If cleanup raises an
ordinary persistence error, a warning records only its class and the primary
interruption still propagates. The row may remain pending; no cleanup success
is claimed. Ordinary exception handling, evaluation/no-session behavior and the
image facade remain unchanged. This is staged, not production activation, task
cancellation policy or rollback of prior tool/global-state effects.

This journal contract does not serialize all valid concurrent Shana activities.
Completed journals remain valid history. The staged receive-order fence below
now prevents an older completion from replacing newer scoped working context. Persona JSON, presence confirmation, tools and
other effects performed during generation remain outside this journal
transaction. This slice is not the broader state-authority or delivery repair.

## Staged scope-preserving continuity, Slice 2A

The isolated branch adds `scopedsessionsummary` and
`scopedworkingstatecheckpoint`, each keyed by `(session_id, privacy_scope)`.
New completions update only their own scope in the existing atomic terminal
transaction. They never overwrite or relabel the legacy tables or another scope.
Omitted objective, relationship and deferred-intention fields can retain only
the same scope's previous values. Journal/history and summary effects retain their existing transaction order.
Working-checkpoint updates instead obey the [scoped receive-order fence](#staged-scoped-working-checkpoint-receive-ordering) below; general tool/output cancellation remains separate.

Summary counts advance once per accepted completion within their exact scope.
They survive journal size pruning while the scoped summary remains retained.
The first scoped summary is built from retained completed turns in that scope;
later text refreshes use that scope's configured exchange interval. Its
`updated_at` records the latest accepted completion, including a count update
without a text refresh. Public exchanges cannot advance a private refresh boundary.

Staged Slice 3B makes a refreshed summary's `through_entry_id` the greatest
journal entry ID among the completed rows selected for that exact session and
scope, or null when the selection is empty. A skipped text refresh preserves
both text and coverage marker. The marker describes the stored summary; it is
not a turn-precedence or freshness fence. Retention and SQLite ID reuse may
allow a later refresh to have a lower marker. Existing completion counters,
legacy compatibility and retention rules remain unchanged. This does not
rewrite existing summaries or require a schema migration.


`snapshot(session_id, privacy_scope=...)` selects one exact scope and preserves
the existing `session_id`, `summary`, and `working_state` response shape. Its
compatibility default is `local_private` for the existing private-owner operator
view. An unknown or public internal caller must explicitly choose its authorized
scope. Missing scoped state returns null, with no private fallback. Prompt-context
assembly selects snapshots only for the supplied scope set.

Legacy summary and checkpoint rows are private-only compatibility data regardless
of their stored label, which the previous bug may already have changed to public.
Private fallback payloads report `local_private`; public/generic views never read
those rows. A first private scoped checkpoint may copy legacy continuation fields
without changing the legacy row. New scoped summaries rebuild from same-scope
journal entries instead of trusting the legacy combined-session count. There is
no public-to-private mirroring and no bulk classification of old state.

Staged size retention applies the configured exchange cap independently to each
session/scope. Pending rows remain exempt from age and size deletion. Scoped
state expires only after its activity timestamp exceeds the retention period
and no journal row remains in that exact scope. Thus a fresh silent completion
of an old pending exchange can retain its newly committed state even if its old
user entry is age-pruned. An expired legacy state row is retained while that
session has any pending work. Explicit session deletion removes every scope's
state, legacy state, and journal entries. Ownership cleanup remains bounded and
preserves pending references.

This is additive staged storage, not a production migration. Preserve a verified
database backup before activation and avoid mixed old/new writers. Old binaries
ignore the scoped tables and cannot continue new scoped state; a code rollback
does not reconstruct private/public progress in the legacy tables. Retain the
scoped data and review any recovery/export separately rather than mirroring it.

At the Slice 2A boundary, audience enforcement was still outstanding. The later
[recipient binding](#staged-recipient-binding-slice-2b-1) now carries recipient
scope through the conversation facade, and the narrative/tool/background/candidate
views above constrain disclosure. Slice 2A alone never established full public
privacy; private ingress and general output authorization remain separate.

## Staged recipient binding, Slice 2B-1

The isolated branch adds a small frozen, slotted `RecipientContext` value in
`schemas/conversation.py`. It is internal metadata, not a field in
`ConversationRequest`. The allowed kinds are `unknown`, `shared`, and
`owner_private`. Targets are unbound, `dashboard_monitor`, `stream_public`, or
`discord_call`. A private kind requires an explicit trusted owner authorization
and cannot name a shared target. Unknown/shared kinds cannot carry an owner grant.
Frozen metadata is not authentication; trusted ingress must establish both
caller authority and the actual private recipient before constructing a grant.

The explicit private constructor is
`RecipientContext(kind="owner_private", owner_authorized=True)` for an authorized
internal owner turn, optionally targeting `dashboard_monitor`. The user-invoked
`run_llm_test` CLI supplies that explicit private context. Public/shared callers
use `RecipientContext(kind="shared", target_policy="stream_public")` or the
`discord_call` target. Absence defaults to `RecipientContext()` with unknown kind.
Speaker hints, bearer access, loopback, `speaker=None`, evaluation mode, and
speech/delivery flags never construct private recipient authorization.

Conversation, image and wake facades carry the same immutable recipient through
ordinary prompt assembly, captured context-budget/fallback factories, structured
repair, finalization and safe terminal fallback. Private recipient/public-stream
delivery conflicts are rejected before identity resolution, journal begin or
prompt assembly. Model/tool arguments cannot replace this internal value.
Ordinary SQL memory injection requires private recipient authorization and the
existing identity/subject access check. Shana's authored character, speaker/tool
permissions and response capabilities remain present.

StreamBrain binds Twitch/public stream and shared Discord recipients from their
actual source/target before conversation. Public source wins a conflicting
private claim in client metadata, and unknown output targets reject before
conversation. It cannot grant private access from presence mode or a monitor
target. Background owner work can use an explicit private turn while those
shared turns run; there is no global audience flag.

Journal writes use recipient scope: private owner maps to `local_private`, shared
to `public`, and unknown to `local_generic`. Explicit private turns retrieve only
`local_private` continuity. Shared/default turns retrieve no historical
continuity until journal provenance is repaired. A legacy public label, including
one copied into a scoped summary by earlier code, cannot prove shared provenance.
This staged constraint prevents replay through this conversation boundary; it
does not reclassify or migrate old journal records.

Unbound HTTP, voice, image and presence adapters retain response capability with
generic ordinary/continuity context. Their authenticated private recipient
bindings require a subsequent finite ingress slice before activation. Existing
HTTP request/response shapes and routes are unchanged. The shared API bearer is
not a recipient credential, and client JSON cannot select the private context.

The earlier expected-failure probe recorded the pre-view prompt/finalizer gaps.
Later dynamic narrative, persona, tool, background and candidate sections close
those specific disclosure paths; the historical probe is not a current failure
claim. This remains short of full public privacy: trusted private ingress,
historical journal provenance, foreign-subject authority, general output/trace
control and semantic privacy of free-form content remain separate boundaries.

## Staged source-aware private memory attribution

Automatic eligible memory writes capture immutable resolved ingress metadata:
reporter, strict owner status, source, resolution method, recipient privacy scope,
and available session/turn identifiers. Conversation structured, metadata,
heuristic, background, image and incremental live-voice paths retain that context.
Reporter and subject are separate: a trusted non-owner self-report belongs to that
person; a report about the owner remains attributed hearsay. Model candidates and
tool arguments cannot grant reporter identity, privacy or verification authority.
Guest, unknown and evaluation no-write policy and strict owner core-save authority
remain in effect. Unbound tools and legacy/manual records have explicit unknown,
tool or manual origin rather than a fabricated owner.

Profile facts and episodes add nullable private `attribution` storage. Profile
facts separately add `importance` for salience ranking; automatic profile
`confidence` is zero, not the candidate importance score. Historical/manual scores
are retained but do not establish truth. The existing episodic compatibility
response key named `confidence` still carries episode importance, not verification.
These attribution and profile-importance fields are excluded from ordinary model
serialization. Already-authorized private prompt and search views qualify claims
as self-reported, reported, manual or unknown, always unverified. Shared views keep
their existing private-memory omission. Manual text edits retain original source,
privacy, identity-resolution and correlation fields privately while labeling the
new content manual. Original reporter fields are not prompt/tool/wire content.

Deduplication, canonical replacement and conflicting preference cleanup are
restricted to the same reporter, origin kind, source and privacy scope. Repeated
claims keep their original and latest available session/turn correlation; a new
reporter or source cannot delete another source's record. This is provenance, not
semantic validation of claims or the extractor. Identity remains name-based
because SpeakerProfile exposes no stable person ID; name collisions/renames are
not solved here. Incremental local voice records the existing assumed local
identity and resolution method, not voice authentication. Image ingress without a
journal exchange does not invent a turn ID. Foreign-subject tool read/write
authorization remains a separate boundary and is not redesigned by attribution.

This additive compatibility schema was exercised only on disposable SQLite and a
copy of a synthetic legacy database, including reopening that copy. No live
database was opened, initialized or migrated. Before any separately approved
activation, retain a database backup and account for old writers that cannot supply
attribution. The staged patch does not activate services or change local config.

## Staged scoped working-checkpoint receive ordering

Within the same working-state session key and exact privacy scope, only the
latest received input may update working context. An older valid completion still
commits its completed journal/history, tool metadata and summary effects under
existing process-owner fences. A newer pending, failed or interrupted input still
supersedes older working updates, preserving the last saved context until a known
newest input completes. This does not invent a newer objective at receive time.

A private ConversationInputCursor row allocates a positive integer receive
sequence inside the existing serialized write transaction. The journal adds one
nullable, serialization-excluded input_sequence column. Cursor keys match working
checkpoint keys; inherited journal session-ID normalization is unchanged. Cursor
lifetime checks conservatively account for that normalization so outstanding work
cannot lose its marker. Counters do not use timestamps, UUID ordering or SQLite
row IDs. They survive service initialization, journal retention and row-ID reuse
while any corresponding journal work or retained scoped checkpoint remains.
Metadata expires only after its receive timestamp is old and neither work nor
checkpoint remains; explicit deletion preserves a marker if inherited journal
normalization left outstanding work. Numbers can restart only once no older work
or retained context can use them. Counter exhaustion/invalid data fails without
partial receipt. Allocation and journal receipt roll back together on interruption.

The cursor and nullable column are additive. Pre-change null markers have unknown
receive order, especially after retention has removed newer rows. Initialization
does not guess or backfill order. Such completions retain history but cannot alter
saved working context; legacy private fallback remains available. A fresh
post-change input allocates normally and can update context. New internal ordering
markers do not enter snapshots, prompt context or API/model serialization.

Schema and migration checks used only fresh/disposable and copied synthetic
pre-change SQLite, including concurrent initialization and reopen. No live database
was migrated. Back up and quiesce old writers before separately authorized
activation; mixed old/new writers cannot establish this ordering contract.
This is scoped working context, not human authentication, general turn/tool/output
cancellation, emotion ordering, automatic progress delivery or production activation.
Non-SQLite migration and runtime behavior are not verified by this SQLite proof.


## Staged cooperative owned-tool admission checkpoint

A journaled ConversationService response carries an explicit internal checkpoint
bound to its exact exchange, continuity owner, normalized journal session and
privacy scope. Each authorized known tool rechecks that one owned user entry is
still pending immediately before admission, including between successive calls
and both primary/extra tool paths. The bounded SQL read closes before execution;
no database lock is held over an arbitrary tool. Ownership/cancellation failure
retains the existing JournalOwnershipError and journal terminal guards.

Explicit cancellation blocks later admissions. Receiving newer input alone does
not cancel older valid effects/history. Already admitted or committed work may
finish and is never rolled back by this check. The check/start race is cooperative,
not an atomic reservation or hard interruption of in-flight work. The checkpoint
uses local per-response arguments, not a mutable shared active-exchange flag or
model/tool kwargs. A foreign or differently scoped pending row cannot satisfy it.

Sessionless/evaluation responses and direct internal tool calls retain their
existing absent-context behavior; no binding or authority is inferred. Existing
owner restrictions and recipient disclosure are unchanged. This does not provide
general voice/output/transport/global-state/background-memory cancellation or
stronger human authentication. Tests use copied synthetic SQLite and real core
file effects with controlled providers, not model/Monitor/audio/production proof.
The repair is staged and awaits independent acceptance; no activation occurred.
