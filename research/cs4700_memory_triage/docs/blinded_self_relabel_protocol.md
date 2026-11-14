# Blinded Self-Relabel Protocol

Status: Prepared for pilot `v0.2`; owner review not yet completed

Selection seed: `47000248`

## Purpose

This check measures whether the owner applies the Memory Triage labeling guide
consistently after a delay. It is a labeling-quality exercise for the
provisional pilot. It is not model evaluation, independent annotation,
external validation, or proof that either the guide or dataset is correct.

No model training may begin until the worksheet is completed, reconciled
against the original labels, and any resulting dataset decisions are recorded.

## Timing

The owner must wait at least 48 hours after the last review or approval of the
pilot v0.2 labels before opening the worksheet. During the relabel session the
owner must not consult the pilot CSV, hidden mapping, pilot review table, or
other prior-label notes.

## Sampling Method

The package contains 24 examples selected reproducibly from the 57
`primary_eligible=true` rows using fixed seed `47000248`.

The hidden source-label composition is balanced at 12 `keep` and 12 `skip`.
The worksheet does not identify that composition row by row. The constrained
selection also:

- includes at least four examples whose original confidence was below
  `high`;
- includes both intentionally eligible context-dependent candidates;
- covers preference, profile, project, episodic, and boundary candidate types;
- covers multiple rationale categories;
- includes no policy-only row;
- includes no more than one primary-eligible representative from any
  paraphrase group.

Candidate order was randomized before assigning `review_01` through
`review_24`. The seed and constraints make regeneration auditable without
placing selected source IDs in tracked documentation.

## Blinding

The worksheet exposes only:

- randomized `review_item_id`;
- `candidate_text`;
- blank fields for the owner's new label, confidence, and optional notes.

It hides the original example ID, label, rationale, candidate source, candidate
type, original confidence, primary eligibility, source group, paraphrase group,
notes, and every other source field. The reconciliation mapping is stored only
under the ignored local `.local/` directory and must remain closed until the
worksheet is finalized.

## Owner Procedure

1. Confirm that the 48-hour separation requirement has been met.
2. Open only the instruction file and worksheet.
3. Judge each candidate as standalone text using the current labeling guide.
4. Record `keep` or `skip`, confidence, and an optional uncertainty note.
5. Leave an item blank instead of guessing when it cannot be judged.
6. Preserve worksheet order and review IDs.
7. Save and close the worksheet before any reconciliation begins.

## Later Agreement Calculation

Reconciliation occurs in a separate pass:

1. Lock a copy or checksum of the completed worksheet before opening the
   mapping.
2. Join worksheet and mapping by the unique `review_item_id`.
3. Report completion as answered rows divided by 24.
4. Report exact agreement as matching `keep`/`skip` decisions divided by
   all 24 selected rows; blank rows are unresolved and are not agreements.
5. Also report completed-row agreement and a 2-by-2 original-versus-review
   label table so the direction of disagreements is visible.
6. If Cohen's kappa is reported, calculate it only on completed rows and
   describe it as a supplementary self-consistency statistic rather than
   independent inter-rater reliability.
7. Summarize confidence changes and disagreement themes without treating
   confidence as a class label.

No agreement value has been calculated in this preparation pass.

## Disagreement Review

After comparison, review each disagreement against the guide without assuming
the original or new label is automatically correct. Record whether the outcome
is:

- retain the original label;
- adopt the self-relabel;
- revise candidate wording or metadata;
- lower confidence and request later adjudication;
- change the guide and re-review every affected row.

Dataset changes must be made transparently in a later revision and recorded in
the maintained pilot review. Do not silently edit labels during reconciliation.

## Limitations

- The same owner supplies both label passes, so this measures delayed
  self-consistency rather than independent agreement.
- The subset is constrained and source-label balanced rather than sampled to
  estimate production prevalence.
- Twenty-four examples cannot validate the full future dataset.
- Blinding removes source metadata but cannot prevent the owner from
  recognizing wording.
- Strong agreement can coexist with a systematically incorrect labeling rule.

The result should be used to improve the guide and pilot before dataset
expansion. It does not authorize runtime integration or support a model-quality
claim.
