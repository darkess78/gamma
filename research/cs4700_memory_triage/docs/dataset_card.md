# Dataset Card: Gamma Memory Triage Working Primary Dataset

Status: Canonical working release through `primary_v0_7`; initial 400-row expansion target complete, not training-ready
Guide version: `v0.3`

## Purpose

This dataset supports a binary text-classification study that recommends
whether one Gamma-style memory candidate should be kept or skipped. It is for
offline class-project exploration only. The primary task uses candidate text
alone; policy examples that require unavailable context are recorded but
excluded from primary modeling and evaluation.

## Data Source

The canonical dataset combines 57 primary-eligible pilot v0.3 rows with 50
owner-approved, assistant-assisted Batch 01 drafts, 50 owner-approved,
assistant-assisted Batch 02 drafts, 50 owner-approved, assistant-assisted
Batch 03 drafts, 50 owner-approved, assistant-assisted Batch 04 drafts, 50
owner-approved, assistant-assisted Batch 05 drafts, 50 owner-approved,
assistant-assisted Batch 06 drafts, and 43 owner-approved, assistant-assisted
Batch 07 drafts. The pilot remains unchanged as the guide-validation source,
and all seven batch draft CSVs remain source-review
provenance. All examples are newly written generic candidates, not
copied from conversations, runtime memory, logs, traces, accounts, or private
documents. Some examples imitate broad candidate styles visible in Gamma's
public source structure, such as preferences, project facts, rules, or episodic
phrasing. Two primary-eligible skip rows deliberately preserve missing
referents because unusable context is itself part of the text-only decision.

Pilot `draft_seed` labeler identifiers preserve the provenance of the original
assistant-assisted pilot-label review. It does not represent an independent
owner-only annotation pass. The owner subsequently approved the `v0.3`
subject-specificity rule and the two scoped row rewrites recorded in the pilot
review. Batch 01 through Batch 07 use `owner_assisted_review` while retaining
`assistant_assisted_draft` as candidate-source provenance.

## Privacy Rules

Prohibited content includes:

- raw or lightly edited private conversations;
- stored Gamma memory rows or continuity journals;
- runtime logs and stream traces;
- real usernames, account identifiers, addresses, or private identifiers;
- secrets, credentials, tokens, private file paths, or machine-local details;
- copied personal, academic, medical, employment, or financial facts.

Rows that fail privacy review must be removed, not relabeled as `skip`.

## Intended Use

- validate the labeling guide;
- measure class balance, text length, duplicates, and rationale coverage;
- build rule and majority baselines later;
- train and compare small TF-IDF classifiers only after further expansion,
  cleaning, grouping review, and final-holdout freeze.

## Primary-Eligibility Field

`primary_eligible` is a required boolean column introduced in v0.2 and retained
in v0.3:

- `true` means the row can be labeled from the candidate text alone and may be
  considered for the primary text-only workflow after review;
- `false` means the decision requires stored-memory context or another signal
  outside the single-candidate task.

Rows marked `false` are retained only for policy or limitations analysis. They
must be excluded before primary training, cross-validation, final holdout
selection, held-out testing, and headline metric calculation. Eligibility is
not a confidence score: a low-confidence but text-solvable row can remain
eligible.

## Prohibited Use

- production memory filtering;
- claims about real Gamma user behavior or production prevalence;
- identity, privacy, safety, or access-control decisions;
- training on the pilot alone or before expanded-data review and holdout
  freeze;
- tuning on or repeatedly inspecting the final test set.

## Representativeness And Known Biases

- The pilot is intentionally close to balanced, while real candidate prevalence
  is unknown. Model probabilities may therefore be miscalibrated for runtime
  use.
- Manually authored examples may be cleaner and more grammatical than actual
  model-generated or rule-generated candidates.
- Rationale categories are deliberately broad and may overrepresent obvious
  cases. V0.3 reduces but cannot eliminate shortcut risk from manually authored
  phrasing.
- Individual-text classification cannot reliably detect whether a candidate is
  already stored, contradicted by newer information, or explicitly requested by
  the owner.
- Candidate type/source columns are provenance fields, not planned v1 model
  features.

The current canonical balance is 200 `keep` and 200 `skip`. The source pilot's
30 `keep` / 30 `skip` balance was intentional for
early model comparison, not an estimate of production prevalence. Collection
of production-like prevalence data and probability calibration are deferred
until a separate privacy-reviewed sample exists.

The pilot primary-eligible subset contains 30 `keep` and 27 `skip` rows because all
three policy-only examples have conditional `skip` labels. This documented
pilot imbalance is not a prevalence estimate and must be handled explicitly in
later grouped development splits.

## V0.3 Review And Freeze Status

The frozen pilot contains 60 rows: 30 `keep` and 30 `skip`. Fifty-seven rows
are primary eligible and three are policy-only. `mtp_0054`, `mtp_0055`, and
`mtp_0056` use `requires_existing_memory_context` and
`primary_eligible=false`; each shares a paraphrase group with its one retained
primary representative.

The revision reduced the context-dependent cluster, added artifact-specific
and speculative skip cases that can sound superficially durable, strengthened
role-based relationship and milestone/commitment coverage, and removed several
obvious absolute or temporary keyword cues. The maintained row-by-row record
is in [pilot_review.md](pilot_review.md).

The 24-item assistant-assisted pilot-label review was reconciled on 2026-07-03.
Exact agreement was 22 of 24 (91.7%), with two disagreements that exposed a
missing rule for project/tool subject and scope. The concealed original labels
made the comparison useful for finding guide ambiguity, but assistant
involvement means it is not an independent blinded self-relabel reliability
study or proof that the labels are objectively correct.

The owner clarified that unscoped reusable preferences and broadly safe
cross-project rules can remain `keep`, while project-specific architecture,
tooling, reporting, or workflow rules must name the relevant system or project.
The prior wording of `mtp_0009` and `mtp_0013` would be `skip` under this rule;
both historical IDs were preserved and their candidate text was rewritten as
scoped standalone `keep` examples. No label or eligibility count changed.

Pilot v0.3 is frozen for use as a labeling-guide validation milestone. It is
not large enough for final model training or final performance claims. It
guided the completed initial expansion documented in
[final_dataset_expansion_plan.md](final_dataset_expansion_plan.md).

## Primary V0.7 Migration Status

`memory_triage_primary_dataset.csv` is the current canonical working
primary-eligible dataset. It contains 400 rows: 200 `keep`, 200 `skip`, 400
`primary_eligible=true`, zero policy-only examples, and 400
`split=unassigned`. It is partly assistant-assisted and may contain
authoring-style bias. It is not representative of real Gamma production-memory
prevalence.

Batch 07 finalized the intended exact 200/200 balance, so the initial 400-row
expansion target is complete. This milestone is not approval for model
training, tuning, split assignment, evaluation, deployment, or Gamma runtime
integration. The future classifier remains advisory-only and disabled by
default unless separately authorized. No model performance or calibration
result exists. See [batch_07_migration_record.md](batch_07_migration_record.md).

## Quality Checks Before Training

1. Confirm required headers, nonblank IDs/text/labels, and unique IDs.
2. Confirm labels are only `keep` or `skip`.
3. Confirm privacy and primary-eligibility fields are explicit booleans.
4. Review class counts and candidate length distributions.
5. Detect exact duplicates after normalized whitespace/case folding.
6. Review `paraphrase_group_id` and find missed near duplicates.
7. Confirm every highly similar paraphrase group has at most one primary-
   eligible representative.
8. Review low-confidence rows and owner adjudication notes.
9. Completed 2026-07-03: reconciled the 24-item assistant-assisted review and
   resolved both disagreements in v0.3.
10. Document every exclusion or correction without manufacturing results.

## Leakage Prevention

- Keep every `source_group_id` and `paraphrase_group_id` within one split.
- Filter to `primary_eligible=true` before constructing primary folds or the
  final holdout.
- Assign splits only after duplicate and paraphrase review.
- Prefer grouped stratification for development folds and the final holdout.
- Fit TF-IDF vocabulary and all preprocessing only on training/development
  partitions.
- Keep the final test set untouched during feature, model, hyperparameter, and
  threshold selection.
- Exclude all `primary_eligible=false` rows from primary training and every
  primary score or final-test claim. The rationale code provides the reason;
  the boolean field is the enforced boundary.

## Planned Size

- Draft pilot: approximately 60 rows.
- Completed initial expansion target: 400 primary-eligible rows, with 200 per
  class.

No evaluation result, calibration claim, or model performance is currently
available.

## Version History

- `v0.1`: initial 60-row provisional draft.
- `v0.2`: added `primary_eligible`; isolated existing-memory policy examples;
  revised weak, vague, duplicate, and shortcut-prone rows; normalized group IDs
  to opaque values; and expanded relationship and ongoing-commitment coverage.
- 2026-07-03 reconciliation note: recorded 22/24 assistant-assisted agreement
  and two unresolved subject-specificity disagreements before owner
  adjudication.
- `v0.3`: described the review accurately as assistant-assisted, added the
  owner-approved subject-specificity rule, rewrote `mtp_0009` and `mtp_0013`
  as scoped standalone `keep` examples, and froze the pilot as a guide
  validation milestone.
- `primary_v0_4`: appended the 50 owner-approved Batch 04 rows to the canonical
  working dataset without changing the frozen pilot or prior canonical rows.
- `primary_v0_5`: appended the 50 owner-approved Batch 05 rows to the canonical
  working dataset without changing the frozen pilot or prior canonical rows.
- `primary_v0_6`: appended the 50 owner-approved Batch 06 rows to the canonical
  working dataset without changing the frozen pilot or prior canonical rows.
- `primary_v0_7`: appended the 43 owner-approved Batch 07 rows to the canonical
  working dataset without changing the frozen pilot or prior canonical rows,
  completing the initial 400-row target at an exact 200/200 balance.
