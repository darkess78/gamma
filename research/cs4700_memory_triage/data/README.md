# Memory Triage Data

`memory_triage_primary_dataset.csv` is the current canonical working
primary-eligible dataset. It contains 400 rows: 200 `keep` and 200 `skip`.
Its composition is 57 eligible pilot-derived rows, 50 rows from each of Batch
01 through Batch 06, and 43 rows from Batch 07. Batch 07 rows use release
`primary_v0_7`. All 400 canonical rows have `primary_eligible=true` and
`split=unassigned`; no training or evaluation has occurred.

`memory_triage_pilot.csv` remains unchanged as the privacy-safe `v0.3`
labeling-guide validation source dataset. It contains 60 rows, of which 57 are
primary eligible and represented in the canonical dataset. The Batch 01
through Batch 07 draft CSVs remain in `../docs/` as source-review provenance.
The canonical dataset is partly assistant-assisted and may contain
authoring-style bias.

## Columns

- `example_id`: opaque unique row ID.
- `source_record_id`: original pilot ID or Batch 01 through Batch 07 draft slot ID.
- `candidate_text`: the only planned v1 model input.
- `label`: `keep` or `skip`.
- `candidate_type`: descriptive candidate category.
- `candidate_source`: manually simulated generation path/style.
- `source_group_id`: opaque grouping for known shared source construction.
- `paraphrase_group_id`: opaque grouping for semantic near duplicates.
- `rationale_code`: labeling-guide reason.
- `labeler_id`: opaque labeler identifier; `draft_seed` records the
  assistant-assisted seed review and is not an independent owner-only label.
- `label_confidence`: `high`, `medium`, or `low`.
- `guide_version`: labeling-guide version.
- `privacy_checked`: `true` only after confirming the row is newly authored and
  contains no prohibited private content.
- `primary_eligible`: `true` only when the label is solvable from candidate
  text alone; `false` rows are policy-only and excluded from primary modeling
  and evaluation.
- `batch_id`: source batch (`pilot_v0_3` or `batch_01` through `batch_07`).
- `dataset_release`: canonical working release identifier.
- `notes`: preserved adjudication, boundary-case, or draft review note.
- `split`: `unassigned` until grouped cleaning and holdout selection.

Do not place raw exports or private derived data in this directory.

Rows whose label requires knowledge of the existing memory store use
`requires_existing_memory_context` and `primary_eligible=false`. Filter on
the eligibility field before any primary split, training, cross-validation, or
evaluation work. In v0.3 these policy-only rows are `mtp_0054`, `mtp_0055`,
and `mtp_0056`; none is present in the canonical primary dataset.
