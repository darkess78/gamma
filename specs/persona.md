# Persona

Status: Current
Last verified: 2026-08-30

Shana is Gamma's single primary persona. Prompt assembly loads versioned persona
documents and structured configuration, then adds bounded identity, memory,
emotion, voice, safety, and request context.

## Requirements

- character identity remains stable across text, voice, and streamer inputs
- external users cannot redefine Shana's core identity or owner relationship
- hidden emotion/voice tags may guide output but are removed from spoken text
- one stable emotion controls each synthesized reply
- private deliberation is represented only by a compact structured decision;
  raw chain-of-thought, hidden analysis, and scratchpad prose are not requested,
  displayed, logged, or persisted
- only `final_text` selected by the structured decision may become display or
  speech text; a silent turn may still apply validated emotion and working-state
  updates
- persona changes are evaluated against conversation, safety, and voice tests
- additional characters or multi-persona routing are outside the current milestone

## Staged recipient views for authored persona

This foundation slice is staged and has not been activated in production.
The reviewed character wording is preserved; this is not a new product-priority
decision or a persona rewrite.

`config/persona.yaml` declares `contract_version: 1` and an
`authored_public` mapping containing the complete sixteen-field authored Shana
definition. All current strings, nested fields, lists and dialogue pairs remain
unchanged. Trusted authoring of this section deliberately publishes its content.
A recognized key or a string type alone is not disclosure authority. Text
intentionally placed in a valid authored-public field is public by that contract;
structural validation cannot infer its semantic privacy.

Shared and unbound recipients receive only the validated authored-public
definition. Every baseline field/container shape is required. Unknown fields
are excluded at every nesting level. Missing, invalid or unsupported public
contracts raise the constant `persona_public_contract_invalid` before model or
speech generation; there is no public fallback to legacy YAML or private data.
Parser diagnostics, values and paths are not included in the error. The
conversation error wrapper may add its existing constant operation prefix.
The image facade validates before image preparation/vision inference, and the
ordinary facade validates before a privacy-refusal TTS branch.

Authorized private recipients retain unwrapped legacy YAML behavior: a truthy
mapping is preserved, a truthy nonmapping becomes `{"value": ...}`, and empty
content becomes an empty object. A declared wrapper must be valid even for a
private recipient. Its private view recursively merges full `authored_public`,
nonreserved root legacy fields, then optional `private_overlay`, in that order.
Mappings merge; later lists/scalars replace. The overlay must be a mapping.
Root control keys `contract_version`, `authored_public` and `private_overlay`
are omitted from the character view; private nested extension data is retained.
Selection produces detached views and never edits source or cached structures.

Public style contains only `humor`, `teasing`, `affection`, `verbosity`,
`confidence` and `entertainment`, with exact integer/float values excluding
booleans, finite and in [0,1]. Invalid/unknown fields are omitted. Private style
remains complete. Relationship-state JSON and operational memory configuration
render as empty objects for shared/unbound recipients and remain complete
privately. Memory configuration still controls runtime reads unchanged.
Stored files and the byte content of `core.md` and `boundaries.md` are preserved;
both authored text documents remain public.

Migration requires wrapping only the explicitly reviewed baseline definition,
without promoting personal additions or learned experiences. Public fixtures
must declare the contract. Legacy private fields can remain at the root or in
`private_overlay`. Persona YAML keeps its existing path, snapshot inclusion and
protected mutation policy. No database migration, memory reset, or automatic
source-data rewrite is part of this slice. Existing prompt-budget priorities
and compaction behavior remain unchanged.

## Staged shared state file integrity

This slice is staged and has not been activated in production.
`AssistantStateStore.update` and `EmotionMemoryService.update_from_turn` hold
a per-canonical-file operating-system lock across the complete read, mutation
and save. The destination is normalized when the store is constructed,
so existence checks, reads, locking and publication use the same path even
for lexical aliases containing absent intermediate components. Stable sidecar lock files remain in place; independent state paths
can progress independently. Each save publishes complete UTF-8 JSON through
a unique temporary file and replacement in the same directory. Readers see
the previous or next complete file, and a failed save preserves the previous
file and releases the writer lock. Process termination releases its lock.

Existing schemas, normalization, learning rules, bounded histories and
recipient views remain unchanged. This provides per-file writer integrity,
not a transaction across the two stores or a power-loss durability guarantee.
Per-file integrity alone does not define cross-turn emotion ordering or general
state/effect cancellation. The later [canonical writer boundary](#staged-canonical-emotion-writer-boundary)
isolates the existing prompt/status authority from compatibility-file failures;
[scoped checkpoint ordering](memory.md#staged-scoped-working-checkpoint-receive-ordering)
is a separate working-context repair.

## Staged canonical emotion writer boundary

Prompt and status readers already use EmotionMemoryService. Conversation emotion
bookkeeping now gives that store and the legacy AssistantStateStore independent
ordinary-error boundaries, so a failed compatibility write cannot skip canonical
state, history or session persistence. Canonical failure still permits the existing
compatibility write. Both stores and their algorithms, schemas, histories, privacy
views and locking/publication helpers remain unchanged. Enabled/disabled and
external evaluation gating remain unchanged. BaseException interruptions, including
cancellation, propagate; completed earlier writes are not rolled back. This is not
a cross-store transaction, new personality model, turn-precedence repair, live
migration or production activation.
