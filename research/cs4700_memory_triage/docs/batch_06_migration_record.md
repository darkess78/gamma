# Batch 06 Migration Record

Status: Working primary-eligible dataset migration complete
Dataset release: `primary_v0_6`

## Sources And Approval

The Batch 06 migration used `docs/batch_06_candidate_drafts.csv` after explicit
owner review and approval of all 50 rows. The owner approved migration of all
50 reviewed Batch 6 rows into the canonical primary dataset. The draft and
review artifacts retain the assistant-assisted drafting, review history, and
owner-approval provenance. No policy-only or existing-memory-context row was
added.

## Counts And Mapping

Before migration, `data/memory_triage_primary_dataset.csv` contained 307 rows:
155 `keep` and 152 `skip`. Batch 06 appended 50 rows: 25 `keep` and 25 `skip`.
After migration, the canonical dataset contains 357 rows: 180 `keep` and 177
`skip`. Its composition is 57 pilot rows and 50 rows from each of Batch 01
through Batch 06. All 357 rows remain `primary_eligible=true` and all 357
splits remain `unassigned`.

Source records B06-01 through B06-50 map one-to-one, in order, to canonical IDs
`mtb06_0001` through `mtb06_0050`. Batch 06 uses `batch_id=batch_06`,
`candidate_source=assistant_assisted_draft`,
`labeler_id=owner_assisted_review`, `guide_version=v0.3`,
`privacy_checked=true`, and row-level `dataset_release=primary_v0_6`, following
the Batch 05 canonical convention. Earlier row-level release values remain
unchanged. The preexisting 307 canonical rows were preserved byte-for-byte and
in their original parsed order.

## Integrity And Audit Results

The frozen pilot SHA-256 remained
`ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`.
Normalized exact comparison found no duplicate within Batch 06, against the
prior 307 rows, or anywhere in the resulting 357-row dataset. Normalized
character-sequence similarity at the established 0.72 suggestion threshold
produced no flagged pair. Focused manual semantic-neighbor review found no
likely duplicate, paraphrase, or blocking overlap, including B06-21 through
B06-25; B06-30; B06-32 through B06-35; B06-37 through B06-38; B06-40 through
B06-45; B06-47; and B06-49 through B06-50.

The final approved wording reflects nineteen review-only revisions followed by
the narrow replacement of B06-40, B06-42, B06-44, and B06-45. Migration did
not rewrite any candidate text. All canonical Batch 06 text exactly matches
the final approved draft CSV, and draft review statuses now use
`approved_for_migration` following the Batch 05 post-migration convention.

All Batch 06 rows use privacy-safe, newly authored, assistant-assisted
synthetic text. Schema, exact-ID mapping, source and paraphrase groups, labels,
eligibility, release, split, privacy, and exact-file allowlist checks passed.
The narrow allowlist permits only the four approved Batch 06 provenance files;
representative prohibited screenshot, local mapping, model, export,
checkpoint, raw-data, and unrelated-notebook paths remain ignored for both the
CS 4700 and CS 4710 research workspaces.

## Boundary

No model training, tuning, evaluation, metric calculation, split assignment,
notebook execution, model, checkpoint, export, chart, or other generated
artifact, Gamma runtime integration or change, staging, commit, or push
occurred. This migration does not authorize or begin Batch 7.
