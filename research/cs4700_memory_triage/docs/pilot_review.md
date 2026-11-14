# Memory Triage Pilot Quality Review

Pilot version: `v0.3`

Initial review date: 2026-06-29

Reconciliation date: 2026-07-03

Owner adjudication date: 2026-07-03

Status: Frozen as a labeling-guide validation milestone

## Purpose

This maintained record explains the quality revisions from pilot v0.1 through
v0.3.
It records decisions without duplicating the full CSV. The primary research
task remains binary `keep`/`skip` classification from one candidate text.

The 24-item comparison was an assistant-assisted pilot-label review. Although
the worksheet concealed the original labels during review, assistant
involvement means this was not an independent blinded self-relabel reliability
study. It is not ground truth, model evaluation, or proof that the labels are
objectively correct.

## Assisted Pilot-Label Review Reconciliation

The completed 24-row local worksheet was reconciled on 2026-07-03. Every row
had a valid `keep` or `skip` decision and a valid `high`, `medium`, or `low`
review confidence.

- Overall exact agreement: 22 of 24 (91.7%).
- Original `keep` agreement: 10 of 12 (83.3%).
- Original `skip` agreement: 12 of 12 (100.0%).
- High-confidence review agreement: 15 of 15 (100.0%).
- Medium-confidence review agreement: 7 of 9 (77.8%).
- No review row used low confidence.

Treating the original pilot labels as the reference solely for this quality
check, the confusion matrix is:

| Original label | Assisted review `keep` | Assisted review `skip` |
| --- | ---: | ---: |
| `keep` | 10 | 2 |
| `skip` | 0 | 12 |

### Disagreement Review

The two disagreements showed that guide v0.2 did not clearly define how
specifically a project, application, or tool must be identified for an
otherwise durable rule to pass the standalone-text test. The owner resolved
that ambiguity for v0.3: unscoped reusable preferences and broadly safe
cross-project rules may remain `keep`, while project-specific architecture,
tooling, reporting, and workflow rules must name the relevant system or
project.

| Example ID | Assisted review finding | Owner-approved v0.3 resolution |
| --- | --- | --- | --- |
| `mtp_0009` | Its prior generic application wording would be `skip` under v0.3 because the system was unnamed. | Historical ID retained; rewritten as a scoped Gamma status-reporting API constraint and retained as `keep`, `primary_eligible=true`. |
| `mtp_0013` | Its prior generic reporting-tool wording would be `skip` under v0.3 because the tool and project scope were unnamed. | Historical ID retained; rewritten as the scoped Gamma Shana API/dashboard process boundary and retained as `keep`, `primary_eligible=true`. |

Both disagreements cluster around vague or missing project scope and unclear
labeling-guide language about subject identification. They do not involve
explicit-save wording, relationship-versus-project-versus-episodic category
confusion, temporary-versus-durable ambiguity, or reliance on existing stored
memory. In both cases the assisted review recognized durable content but
rejected the row on standalone specificity.

The 91.7% agreement is useful because it located this missing guide rule. It
must not be reported as independent reliability evidence or proof of pilot
label quality.

### V0.3 Field Changes

Only the two adjudicated rows changed. All other CSV rows were left intact.

- `mtp_0009`:
  - `candidate_text`: replaced with the owner-approved Gamma
    status-reporting API wording;
  - `candidate_source`: `manual_llm_style` to `manual_review_revision`;
  - `guide_version`: `v0.2` to `v0.3`;
  - `notes`: replaced with the assisted-review adjudication note.
  - `label=keep`, `candidate_type=project`,
    `rationale_code=stable_project_fact`, `label_confidence=high`, and
    `primary_eligible=true` were retained.
- `mtp_0013`:
  - `candidate_text`: replaced with the owner-approved Gamma Shana
    API/dashboard process wording;
  - `candidate_source`: `manual_llm_style` to `manual_review_revision`;
  - `label_confidence`: `medium` to `high` because the clarified rule now
    determines the scoped example directly;
  - `guide_version`: `v0.2` to `v0.3`;
  - `notes`: replaced with the assisted-review adjudication note.
  - `label=keep`, `candidate_type=project`,
    `rationale_code=stable_project_fact`, and `primary_eligible=true` were
    retained.

These changes preserve all 60 historical IDs and the 30 `keep` / 30 `skip`
balance. Row-level `guide_version` values retain provenance: unchanged rows
remain marked with the guide version used for their prior review, while the two
v0.3 adjudicated rewrites are marked `v0.3`.

## V0.1 To V0.2 Changes

- Preserved exactly 60 historical `example_id` values and the 30 `keep` /
  30 `skip` overall balance.
- Added the required `primary_eligible` field.
- Marked 57 examples primary eligible and retained three existing-memory cases
  as policy-only examples.
- Replaced `duplicate_existing` with
  `requires_existing_memory_context` for those three policy rows.
- Normalized source and paraphrase group identifiers to opaque IDs.
- Kept at most one primary-eligible representative in each known highly
  similar paraphrase group.
- Revised 31 candidate texts to reduce missing context, improve candidate
  realism, diversify transient reasoning, and reduce simple lexical shortcuts.
- Reduced `context_dependent` examples from five to two while retaining a
  deliberately varied pair of real missing-context failure modes.
- Increased `stable_relationship` examples from one to two and added an
  `ongoing_commitment` example alongside the meaningful milestone.
- Reworked reaction, artifact-specific preference, one-off incident, obsolete
  fact, and speculative proposal examples so the skip class is not dominated
  by explicit words such as “today” or “temporary.”

No new example IDs were introduced. Rows described as replaced below retain
their historical ID but contain a different candidate scenario.

## Previously Flagged Rows

| Example ID | Original issue | V0.2 action | Primary eligible | Reason |
| --- | --- | --- | --- | --- |
| `mtp_0004` | Near-duplicate/conflicting group | Retained | `true` | It is the sole text-only representative for the weekly-summary preference; related policy rows share its paraphrase group. |
| `mtp_0007` | Near-duplicate/conflicting group | Retained | `true` | It is the sole text-only representative for the Python compatibility fact; the related policy row is excluded. |
| `mtp_0022` | Borderline relationship value | Rewritten | `true` | The role and recurring accessibility-review responsibility now make future value explicit without using a real identity. |
| `mtp_0026` | Borderline milestone value | Rewritten | `true` | The text now explains that the completed evaluation established a reusable comparison baseline. |
| `mtp_0037` | Vague/missing context | Rewritten | `true` | It is now a standalone reaction to one generated artifact rather than an unresolved “that.” |
| `mtp_0039` | Vague/missing context | Rewritten | `true` | It is now a standalone acknowledgement of one completed formatting action. |
| `mtp_0041` | Vague/missing context | Rewritten | `true` | It deliberately remains unusable because the option is unnamed while “future” wording makes it a less trivial skip. |
| `mtp_0042` | Vague/missing context | Rewritten | `true` | It is now a clear but artifact-specific preference and therefore a text-solvable skip. |
| `mtp_0043` | Vague/missing context | Rewritten | `true` | It is now a standalone one-off failure during a restart rather than an unnamed issue and feature. |
| `mtp_0044` | Vague/missing context | Retained | `true` | It is a useful explicit-save failure case: intent cannot repair candidate text with no referent. |
| `mtp_0045` | Vague/missing context | Rewritten | `true` | It is now a clear one-showcase layout decision rather than an unnamed general preference. |
| `mtp_0049` | Unnatural/immediate action | Replaced in place | `true` | The new text is a plausible generated candidate about one draft artifact while remaining non-durable. |
| `mtp_0052` | Borderline completed event | Rewritten | `true` | The event is explicitly routine with no follow-up and is distinct from the meaningful milestone example. |
| `mtp_0054` | Near duplicate; existing-memory dependent; weak task fit | Policy-only | `false` | Its skip decision assumes the equivalent preference is already stored and cannot be inferred from text alone. |
| `mtp_0055` | Near duplicate; existing-memory dependent; weak task fit | Policy-only | `false` | It remains a second policy paraphrase but cannot enter any primary model or metric. |
| `mtp_0056` | Near duplicate; existing-memory dependent; weak task fit | Policy-only | `false` | Its skip decision assumes the equivalent project fact is already stored and cannot be inferred from text alone. |
| `mtp_0057` | Unnatural/assistant-centric candidate | Replaced in place | `true` | The revised assistant status is a more plausible extracted candidate while still being inappropriate user/project memory. |
| `mtp_0059` | Vague speculation | Rewritten | `true` | It is now a standalone project proposal with no adopted decision. |
| `mtp_0060` | Vague and borderline preference | Rewritten | `true` | Discussion of reminders no longer claims an unsupported durable user preference. |

## Additional Representation And Shortcut Revisions

- `mtp_0014` and `mtp_0015` keep their durable permission meanings without
  relying on “always” or “never.”
- `mtp_0025` now supplies a second safe role-based relationship candidate.
- `mtp_0028` now supplies a recurring project commitment distinct from a
  completed milestone.
- `mtp_0031` through `mtp_0035` describe transient states through scenario
  meaning rather than a repeated set of obvious time words.
- `mtp_0038`, `mtp_0040`, `mtp_0046` through `mtp_0048`, `mtp_0050`,
  `mtp_0051`, `mtp_0053`, and `mtp_0058` were revised for standalone
  clarity and more varied skip reasoning.

## Eligibility And Group Review

The only policy-only rows are `mtp_0054`, `mtp_0055`, and `mtp_0056`.
Their labels require existing-memory context. The known multi-row paraphrase
groups are:

- `pg_0004`: primary representative `mtp_0004`; policy-only
  `mtp_0054` and `mtp_0055`;
- `pg_0007`: primary representative `mtp_0007`; policy-only `mtp_0056`.

No known highly similar group has more than one primary-eligible row.

## Freeze Decision

No pilot-label adjudications remain open. Pilot v0.3 is frozen as the
labeling-guide validation milestone and may guide final-dataset expansion after
the owner approves the
[final-dataset expansion plan](final_dataset_expansion_plan.md). The 60-row
pilot is not large enough for final model training or final performance claims.
The `draft_seed` value is retained as provenance for the assistant-assisted seed
review, not represented as an independent owner-only labeler ID.
