# Batch 05 Migration Record

Status: Working primary-eligible dataset migration complete
Dataset release: `primary_v0_5`

## Sources And Approval

The Batch 05 migration used `docs/batch_05_candidate_drafts.csv` after explicit
owner review and approval of all 50 rows. The owner approved migration of all
50 reviewed Batch 5 rows into the canonical primary dataset. No policy-only or
existing-memory-context row was added.

## Counts And Mapping

Before migration, `data/memory_triage_primary_dataset.csv` contained 257 rows:
130 `keep` and 127 `skip`. Batch 05 appended 50 rows: 25 `keep` and 25 `skip`.
After migration, the canonical dataset contains 307 rows: 155 `keep` and 152
`skip`. Its composition is 57 pilot rows and 50 rows from each of Batch 01
through Batch 05. All 307 rows remain `primary_eligible=true` and all 307
splits remain `unassigned`.

Source records B05-01 through B05-50 map one-to-one, in order, to canonical IDs
`mtb05_0001` through `mtb05_0050`. Batch 05 uses `batch_id=batch_05`,
`candidate_source=assistant_assisted_draft`,
`labeler_id=owner_assisted_review`, `guide_version=v0.3`,
`privacy_checked=true`, and row-level `dataset_release=primary_v0_5`, following
the Batch 04 canonical convention. Earlier row-level release values remain
unchanged. The preexisting 257 canonical rows were preserved byte-for-byte and
in their original order.

## Integrity And Audit Results

The frozen pilot SHA-256 remained
`ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`.
Normalized exact comparison found no duplicate within Batch 05, against the
prior 257 rows, or anywhere in the resulting 307-row dataset. Normalized
character-sequence similarity at the established 0.72 suggestion threshold
and focused manual semantic-neighbor review found no likely duplicate,
paraphrase, or blocking overlap. Any surface-similarity flags and their
resolutions are recorded in the migration validation report; surface checks
do not replace final-dataset semantic and grouping review.

The final wording-diversity pass revised `candidate_text` only for 17 selected
skip rows and reduced direct-negation or absence-cue rows from 23 to 6 under
the documented case-insensitive word-boundary method. This was a data-quality
and shortcut-risk check only, not a model-performance or reliability claim.

All Batch 05 rows use privacy-safe, newly authored, assistant-assisted
synthetic text. Schema, exact-ID mapping, source and paraphrase groups, labels,
eligibility, release, split, privacy, and exact-file allowlist checks passed.
Representative prohibited screenshot, local mapping, model, export,
checkpoint, raw-data, and unrelated-notebook paths remain ignored for both the
CS 4700 and CS 4710 research workspaces.

## Boundary

No model training, tuning, evaluation, metric calculation, split assignment,
notebook execution, model, checkpoint, export, chart, or other generated
artifact, Gamma runtime change, staging, commit, or push occurred. This
migration does not authorize Batch 6.
