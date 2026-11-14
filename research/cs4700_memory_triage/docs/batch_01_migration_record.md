# Batch 01 Migration Record

Status: Working primary-eligible dataset migration completed
Dataset release: `primary_v0_1`

## Sources And Inclusion

The canonical working dataset was assembled from:

- `data/memory_triage_pilot.csv`, the frozen pilot v0.3 guide-validation source;
- `docs/batch_01_candidate_drafts.csv`, the owner-approved, assistant-assisted
  Batch 01 draft source.

Pilot rows were included only when `primary_eligible=true`. All 50 approved
Batch 01 rows were included. The three policy-only pilot examples were
excluded.

## Current Counts

`data/memory_triage_primary_dataset.csv` contains 107 rows: 55 `keep` and 52
`skip`. Every row is primary eligible and every `split` is `unassigned`.

## Provenance And Status

The 57 pilot-derived rows came from the manually authored, privacy-safe pilot
process. The 50 Batch 01 rows are assistant-assisted drafts reviewed with the
owner. This mixture may contain authoring-style bias and is not representative
of real Gamma production-memory prevalence.

This release is a working primary-eligible research dataset. It is not final,
is not a production-memory dataset, and is not approved for model training.
Further authoring, cleaning, duplicate and grouping review, and final-holdout
decisions must be completed first. No claim about model performance,
calibration, or real-world deployment may be made from this migration.

## Remaining Expansion

The approximate remaining authoring need is 145 additional `keep` examples
and 148 additional `skip` examples. The target remains approximately 400
primary-eligible examples.

## Privacy And Policy Boundary

No policy-only examples are included. No private conversation data, secrets,
credentials, names, account identifiers, runtime logs, or raw private
conversation content are included. Explicit memory-storage policy remains
outside this dataset's text-only classification task.
