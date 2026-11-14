# CS 4700 Memory Triage Offline Workspace

Status: Primary v0.7 working dataset migrated; initial 400-row expansion target reached

This offline workspace supports the project defined by
[`specs/class-projects/cs4700-memory-triage.md`](../../specs/class-projects/cs4700-memory-triage.md).
The specification is the maintained source for Gamma integration decisions;
this folder contains the working labeling and dataset materials.

## Current Phase

- The 24-item assistant-assisted pilot-label review produced 22 exact
  agreements (91.7%) and exposed a missing subject/scope rule.
- Guide v0.3 now permits unscoped reusable preferences and broadly safe
  cross-project rules, but requires project-specific rules to name their
  system or project.
- `mtp_0009` and `mtp_0013` were rewritten as scoped standalone `keep`
  examples with their historical IDs preserved.
- Pilot v0.3 remains frozen and unchanged as the guide-validation source.
- `memory_triage_primary_dataset.csv` is the canonical 400-row working
  primary-eligible dataset: 200 `keep`, 200 `skip`, and all splits unassigned.
- Batch 01 through Batch 07 drafts remain source-review provenance after
  owner-approved migration. Batch 07 is the latest completed canonical batch,
  and the initial planned expansion target is reached. The working dataset is
  synthetic, privacy-safe research data that is partly assistant-assisted and
  may contain authoring-style bias.
- No model training, tuning, splitting, evaluation, or Gamma runtime
  integration has occurred.

No training code, model artifact, runtime hook, or dependency is present.

## Contents

- [`docs/labeling_guide.md`](docs/labeling_guide.md) — keep/skip rules and edge cases.
- [`docs/dataset_card.md`](docs/dataset_card.md) — provenance, intended use,
  limitations, and quality plan.
- [`docs/pilot_review.md`](docs/pilot_review.md) — maintained v0.1 through v0.3
  row-review decisions.
- [`docs/final_dataset_expansion_plan.md`](docs/final_dataset_expansion_plan.md)
  — proposed authoring, grouping, review, and split-freeze process.
- [`data/README.md`](data/README.md) — CSV field definitions and handling rules.
- [`data/memory_triage_pilot.csv`](data/memory_triage_pilot.csv) — 60 manually
  authored pilot examples frozen for guide validation.
- [`data/memory_triage_primary_dataset.csv`](data/memory_triage_primary_dataset.csv)
  — current canonical working primary-eligible dataset.
- [`docs/batch_01_migration_record.md`](docs/batch_01_migration_record.md) —
  migration sources, counts, provenance, boundaries, and remaining expansion.
- [`docs/batch_02_migration_record.md`](docs/batch_02_migration_record.md) —
  Batch 02 approval, mapping, audit, and migration provenance.
- [`docs/batch_03_migration_record.md`](docs/batch_03_migration_record.md) —
  Batch 03 approval, mapping, audit, and migration provenance.
- [`docs/batch_04_migration_record.md`](docs/batch_04_migration_record.md) —
  Batch 04 approval, mapping, audit, and migration provenance.
- [`docs/batch_05_migration_record.md`](docs/batch_05_migration_record.md) —
  Batch 05 approval, mapping, audit, and migration provenance.
- [`docs/batch_06_migration_record.md`](docs/batch_06_migration_record.md) —
  Batch 06 approval, mapping, audit, and migration provenance.
- [`docs/batch_07_migration_record.md`](docs/batch_07_migration_record.md) —
  Batch 07 approval, mapping, audit, and migration provenance.
- [`notebooks/CS4700_Gamma_Memory_Triage.ipynb`](notebooks/CS4700_Gamma_Memory_Triage.ipynb)
  — execution-gated exploration, baseline, model-comparison, and evaluation
  scaffold with no committed results.

## Boundaries

- Use only manually authored, anonymized Gamma-style candidates.
- Never copy private conversations, memory rows, logs, traces, usernames,
  identifiers, file paths, credentials, or secrets into this workspace.
- Treat the frozen pilot as a guide-validation artifact, not a final training
  dataset or source of final performance claims.
- Exclude `primary_eligible=false` policy examples from every primary model
  split, training run, cross-validation result, final test, and headline claim.
- Keep explicit owner-save examples separate during later evaluation.
- Optimize the first advisory candidate for high `keep` precision.
- During development, rank by mean cross-validation `keep` precision only among
  models with mean cross-validation `keep` recall of at least `0.50`.

## Next Steps

1. Complete any final corpus-wide cleaning and guide-consistency review needed
   before split assignment.
2. Freeze group-aware development and untouched test assignments only after
   cleaning is complete and separately authorized.
3. Enable notebook training work only after the final holdout is frozen and
   model work is separately authorized.
