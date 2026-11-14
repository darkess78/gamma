# Batch 04 Migration Record

Status: Working primary-eligible dataset migration complete
Dataset release: `primary_v0_4`

## Sources And Approval

The Batch 04 migration used
`docs/batch_04_candidate_drafts.csv` after explicit owner review and approval
of all 50 rows. The owner approved migration of all 50 reviewed Batch 4 rows
into the canonical primary dataset. No policy-only row was added.

## Counts And Mapping

Before migration, `data/memory_triage_primary_dataset.csv` contained 207 rows:
105 `keep` and 102 `skip`. Batch 04 appended 50 rows: 25 `keep` and 25 `skip`.
After migration, the canonical dataset contains 257 rows: 130 `keep` and 127
`skip`. Its composition is 57 pilot rows, 50 Batch 01 rows, 50 Batch 02 rows,
50 Batch 03 rows, and 50 Batch 04 rows. All 257 rows remain
`primary_eligible=true` and all 257 splits remain `unassigned`.

Source records B04-01 through B04-50 map one-to-one, in order, to canonical IDs
`mtb04_0001` through `mtb04_0050`. Batch 04 uses `batch_id=batch_04`,
`candidate_source=assistant_assisted_draft`,
`labeler_id=owner_assisted_review`, `guide_version=v0.3`,
`privacy_checked=true`, and row-level `dataset_release=primary_v0_4`, following
the Batch 03 canonical convention. Earlier row-level release values remain
unchanged. The preexisting 207 canonical rows were preserved byte-for-byte and
in their original order.

## Integrity And Audit Results

The frozen pilot SHA-256 remained
`ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`.
Normalized exact comparison found no duplicate within Batch 04, against the
prior 207 rows, or anywhere in the resulting 257-row dataset. Normalized
character-sequence similarity at the established 0.72 review threshold flagged
no Batch 04 pair. Manual semantic review of B04-15, B04-35 through B04-38,
B04-41 through B04-45, B04-49, and B04-50 against nearby prior examples found
no likely duplicate, paraphrase, or blocking overlap. Surface checks do not
replace final-dataset semantic and grouping review.

All Batch 04 rows use privacy-safe, newly authored, assistant-assisted
synthetic text. Schema, allowlist, unique-ID, group, label, eligibility,
release, and split checks passed. No private material, secret, credential, raw
conversation, raw memory, log, trace, database content, private path, local
review mapping, or sensitive personal fact was used.

The exact-file allowlist was extended only for the Batch 04 authoring
specification, candidate drafts, candidate review, and migration record.
Representative prohibited screenshot, local mapping, model, export,
checkpoint, raw-data, and unrelated-notebook paths remain ignored for both CS
4700 and CS 4710.

## Boundary

No model training, tuning, evaluation, metric calculation, split assignment,
notebook execution, model, checkpoint, export, chart, or other generated
artifact, Gamma runtime change, staging, commit, or push occurred. This
migration does not authorize Batch 5.
