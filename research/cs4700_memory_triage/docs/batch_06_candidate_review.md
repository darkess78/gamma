# Batch 06 Candidate Review

## Status And Scope

Status: **Owner-approved review and canonical migration complete.**

Scope: **Candidate review, approval, and completed migration status.**

Batch: **Batch 06.**

Theme: **Stream and game memory-triage coverage.**

The candidate set contains exactly 50 privacy-safe, assistant-assisted
synthetic rows: 25 `keep` and 25 `skip`. All 50 remain
`primary_eligible=true` and have been migrated into canonical release
`primary_v0_6`. The canonical dataset now contains 357 rows: 180 `keep` and
177 `skip`, consisting of 57 pilot-derived rows and 50 rows from each of Batch
01 through Batch 06. All canonical rows remain primary eligible with
`split=unassigned`.

Batch 6 source records B06-01 through B06-50 map in order to canonical IDs
`mtb06_0001` through `mtb06_0050`. No training, evaluation, split assignment,
notebook execution, runtime integration, staging, commit, or push occurred.

## Evidence Reviewed

The review inspected the full current contents of the Batch 6 authoring
specification and candidate-draft CSV; the current 307-row canonical dataset;
the 60-row pilot CSV and its relevant frozen-pilot protections; the labeling
guide; the dataset card; the final expansion plan; and the relevant Batch 01
through Batch 05 authoring specifications, candidate drafts, candidate reviews,
and migration records. It also considered the Batch 6 validation evidence from
the drafting and two focused revision passes.

The full candidate set was reviewed against the actual current canonical
corpus, not a hypothetical future dataset.

## Owner-Review Decision

The owner approved all 50 Batch 6 rows for canonical migration. The approved
migration subsequently verified dataset integrity, row mapping, provenance,
duplicate checks, documentation, notebook state, and exact-file allowlist
safety. This record makes no claim of independent inter-rater reliability,
external review, or model performance and does not authorize a commit or push.

## Batch Composition

| Candidate range | Label | Rows | Shared review attributes |
| --- | --- | ---: | --- |
| B06-01 through B06-25 | `keep` | 25 | `assistant_assisted_draft`, high confidence, primary eligible |
| B06-26 through B06-50 | `skip` | 25 | `assistant_assisted_draft`, high confidence, primary eligible |

Every row has a unique provisional `source_group_id` and
`paraphrase_group_id`. The draft CSV contains no canonical IDs, split values,
dataset-release values, or other canonical-only metadata. Its post-migration
`review_status` is `approved_for_migration`, matching the established Batch 5
convention while this record retains the owner-approval history and the draft
CSV remains source-review provenance.

## Semantic Coverage Reviewed

The approved `keep` coverage includes spoiler and hint boundaries;
coaching/help style; gameplay goals; challenge and account restrictions;
progression milestones with future consequences; recurring play preferences;
durable mechanic interpretations; series commitments; audience interaction
preferences; accessible gameplay presentation; recurring stream formats;
stable stream and community preparation responsibilities; and shared
game-stream continuity concepts.

The approved `skip` coverage includes encounter-specific equipment; active
boss, quest, and match state; opponent-formation tactics; mode-issued companion
state; a match-only result; quest-issued identity; bracket-specific rank
display; a generated-instance navigation mark; quest-required appearance;
episode framing; a preview-inclusive exposure count; a directory category; a
chat moment; encounter-caused commentary behavior; in-world quest-clock
wording; consumable tabletop set-piece state; an incidental game-scene color
cast; a one-attempt direct-help request; replay-camera-limited advice; a
one-stream recording-break location; and a subtitle-timing display alias.

Across both labels, the decision follows future cross-session consequence. A
reference to a game, stream, chat, account, audience, or series does not by
itself determine whether a candidate should be kept.

## Focused Semantic-Review History

Initial review found several concepts too close to retained Batch 6 material or
earlier canonical hard-negative families. Nineteen rows were revised in a
review-only pass before the final approval pass. A second narrow review-only
revision then replaced four remaining rows: B06-40, B06-42, B06-44, and B06-45.
Their replacement concepts were encounter-caused commentary behavior; an
in-world time reference contrasted with broadcast scheduling; consumable
tabletop set-piece state; and an incidental game-scene lighting effect. The
final owner review approved all 50 rows.

## Privacy, Scope, And Content Safety

All content is assistant-assisted synthetic material. The work used no raw
conversations, private memories, runtime databases, logs, traces, secrets,
account credentials, screenshots, local mappings, or real stream or game
records. No real game title, platform, channel name, username, account, viewer,
or identifiable community appears. The candidates include no sensitive health,
identity, private-chat, purchase-history, location, or real-life relationship
detail.

Every candidate is intended to be self-contained and labelable from its own
text. No row is policy-only, and no row requires knowing whether a related fact
already exists in memory. This review does not decide runtime retention policy
or create a player profile or tracker system.

## Duplicate, Overlap, And Shortcut-Risk Review

Migration re-verification found zero normalized exact duplicates within Batch
6, against the prior 307 canonical rows, or in the resulting 357-row dataset.
The established 0.72 normalized character-sequence similarity audit found zero
flagged pairs within Batch 6, against prior canonical data, or in the resulting
dataset.

Manual semantic-neighbor review covered all rows, with focused attention on
bridges and previously held hard negatives. No blocking semantic overlap
remained after revision. Final special scrutiny included B06-21 through B06-25;
B06-30; B06-32 through B06-35; B06-37 through B06-38; B06-40 through B06-45;
B06-47; and B06-49 through B06-50.

Game and stream terminology appears in both labels and cannot serve as the
label rule. Direct-negation and absence cues remain within the documented safe
limit of no more than eight `skip` rows; the latest drafting audit reported
seven. No single rewritten `skip` phrase family dominated more than four rows.
Prohibited time and approval vocabulary was absent from revised target rows;
one retained non-target occurrence in B06-28 was documented for future
awareness.

These checks are data-review safeguards. They do not prove model quality,
generalization, fairness, or reliability.

## Completed Migration Validation And Continued Blocks

The owner-approved migration verified:

- the frozen pilot hash;
- the exact canonical schema and pre-migration row counts and balance;
- the exact B06-01 through B06-50 to new canonical-ID mapping;
- preservation of all existing canonical rows in order;
- exact-file allowlist safety;
- duplicate and semantic-overlap checks against the actual then-current corpus;
- privacy and provenance fields;
- factual documentation updates; and
- notebook validity, code-source preservation, empty outputs, and null
  execution counts.

Candidate review statuses were updated according to the established Batch 5
convention with accurate provenance. Training
remains blocked until the final data target, data cleaning, grouping review,
and a frozen untouched group-aware holdout are complete. Evaluation, split
assignment, and notebook execution remain blocked until separately approved.
Runtime integration remains blocked and requires a separate explicit task. Any
future classifier remains advisory-only and disabled by default unless a later
approved runtime task changes that status.
