# Batch 02 Migration Record

Status: Working primary-eligible dataset migration completed
Dataset release: `primary_v0_2`

## Sources And Inclusion

The Batch 02 migration used
`docs/batch_02_candidate_drafts.csv`, after all 50 owner-reviewed rows were
explicitly approved for migration. The frozen pilot and the existing 107
canonical rows were preserved unchanged. No policy-only row was added.

## Counts And Mapping

Before migration, `data/memory_triage_primary_dataset.csv` contained 107 rows:
55 `keep` and 52 `skip`. Batch 02 appended 50 rows: 25 `keep` and 25 `skip`.
After migration, the canonical dataset contains 157 rows: 80 `keep` and 77
`skip`. Every row is primary eligible and every `split` is `unassigned`.

Source records B02-01 through B02-50 map one-to-one, in order, to canonical
IDs `mtb02_0001` through `mtb02_0050`. Batch 02 uses `batch_id=batch_02`,
`labeler_id=owner_assisted_review`, `guide_version=v0.3`,
`privacy_checked=true`, and row-level `dataset_release=primary_v0_2`, matching
the Batch 01 canonical mapping convention while identifying the release in
which these rows entered the dataset. Historical pilot and Batch 01 release
values remain unchanged.

## Integrity And Audit Results

The frozen pilot SHA-256 was verified before and after migration as
`ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`.
Normalized exact comparison found no duplicate within Batch 02 or against the
pre-migration canonical dataset. Because Batch 01 records similarity
suggestions and manual semantic review without a reusable algorithm, this
migration used normalized token-Jaccard and character-sequence similarity as
surface suggestions and manually reviewed the highest-scoring pairs. No likely
duplicate, paraphrase, or blocking conceptual overlap was found; the method
does not replace later final-dataset semantic and grouping review.

All Batch 02 rows use newly authored, assistant-assisted synthetic text. The
review found no private source material, secret, credential, account ID, raw
log, raw memory, private local path, sensitive personal content, policy-only
case, or existing-memory-context dependency.

The repository exact-file allowlist was extended for the Batch 02 authoring
specification, draft CSV, candidate review, and migration record.
Representative prohibited screenshot, local mapping, model, export,
checkpoint, raw-data, and unrelated-notebook paths remain ignored for both the
CS 4700 and CS 4710 research workspaces.

## Boundary

This migration was based on explicit owner approval. No model training,
tuning, evaluation, split assignment, notebook execution, Batch 3 work, or
Gamma runtime integration occurred.
