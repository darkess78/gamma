# Batch 01 Candidate Review

## Purpose And Provenance

This artifact supports owner review of Batch 01 for the CS 4700 Gamma memory-triage project. It summarizes newly authored, privacy-safe candidate drafts produced from the owner-approved B01-01 through B01-50 semantic inventory in [the Batch 01 authoring specification](batch_01_authoring_spec.md). It does not add rows to the pilot or final training dataset and does not assign dataset splits.

All 50 rows in [the candidate draft CSV](batch_01_candidate_drafts.csv) were reviewed and revised through an assistant-guided owner review. That review focused on standalone clarity, explicit scope, durable usefulness, natural Gamma-style wording, privacy safety, and the distinction between one-time feedback and durable preferences. The rows remain assistant-assisted drafts rather than naturally collected production memory candidates. The owner approved migration, and their workflow status is now `approved_for_migration`; the draft CSV remains source-review provenance.

## Draft Status And Current Summary

The draft CSV remains a source-review artifact and was migrated into the separate canonical working dataset as Batch 01. It is not itself the canonical dataset. The canonical release is still not approved for training, tuning, splitting, or evaluation.

The current file contains 50 rows: 25 proposed `keep` and 25 proposed `skip`. All 50 are primary eligible by design, 41 have high label confidence, and 9 have medium label confidence. No policy-only examples were added, and every split remains unassigned because the draft schema contains no split-assignment field.

## Compact Inventory

| Slot | Label | Type | Rationale | Short semantic purpose | Risk to inspect |
| --- | --- | --- | --- | --- | --- |
| B01-01 | keep | preference | durable_preference | Clarify ambiguous requests before action | May read as task-local |
| B01-02 | keep | preference | durable_preference | Recommendation before rationale | May resemble report formatting |
| B01-03 | keep | preference | accessibility_preference | Navigable long-form organization | Avoid medical inference |
| B01-04 | keep | preference | durable_preference | Direct correction with revision | May read as one-review feedback |
| B01-05 | keep | preference | durable_preference | Symmetric option comparisons | Behavioral value may be weak |
| B01-06 | keep | preference | accessibility_preference | Non-color visual encoding | May narrow to one artifact |
| B01-07 | keep | boundary | reusable_rule | Consequences before implementation detail | May be generic advice |
| B01-08 | keep | boundary | reusable_rule | Record decisions and reasons | Recurrence may be unclear |
| B01-09 | keep | boundary | reusable_rule | Isolated change before broad work | May overlap reversible workflows |
| B01-10 | keep | boundary | reusable_rule | Gather evidence before escalation | May hide incident context |
| B01-11 | keep | boundary | reusable_rule | Internal review before final approval | May overlap publication approval |
| B01-12 | keep | boundary | recurring_constraint | Least-disclosure artifact sharing | Could imply broad privacy policy |
| B01-13 | keep | boundary | recurring_constraint | Approval for destructive operations | General approval overlap |
| B01-14 | keep | project | recurring_constraint | CS 4700 dataset release gate | Could mirror project documentation |
| B01-15 | keep | project | recurring_constraint | CS 4710 screenshot redaction | Could read as one cleanup task |
| B01-16 | keep | project | stable_project_fact | Adopted annotation workflow | Adoption must be credible in text |
| B01-17 | keep | project | stable_project_fact | Canonical dataset manifest | Artifact scope may be unclear |
| B01-18 | keep | project | durable_correction | Adopted missing-metadata correction | History may lack continuing value |
| B01-19 | keep | project | stable_project_fact | Multi-stage project objective | Could read as a short task |
| B01-20 | keep | profile | stable_relationship | Recurring technical reviewer | Recurrence may be overstated |
| B01-21 | keep | profile | stable_relationship | Dataset provenance steward | Could read as one release review |
| B01-22 | keep | profile | stable_relationship | Named-project scope authority | Could be confused with a rule |
| B01-23 | keep | episodic | meaningful_milestone | Signoff establishes use boundary | Routine approval risk |
| B01-24 | keep | project | ongoing_commitment | Revision-level provenance audit | Commitment acceptance risk |
| B01-25 | keep | project | ongoing_commitment | Performer schema compatibility | Architecture-category overlap |
| B01-26 | skip | episodic | temporary_state | Preparation ending with presentation | Importance may imply durability |
| B01-27 | skip | episodic | temporary_state | Editing-session sequence | Session scope must stay explicit |
| B01-28 | skip | episodic | temporary_state | Event-bounded room arrangement | Could imply venue preference |
| B01-29 | skip | episodic | temporary_state | Single selection-round schedule | Could imply recurring review |
| B01-30 | skip | episodic | one_off_detail | Completed document conversion | Could imply reusable template |
| B01-31 | skip | episodic | one_off_detail | Resolved coordination disruption | Could imply recurring problem |
| B01-32 | skip | episodic | one_off_detail | Incidental event seating | Could imply location preference |
| B01-33 | skip | episodic | one_off_detail | Completed role-based handoff | Could imply stable relationship |
| B01-34 | skip | episodic | casual_reaction | Reaction to diagnostic trace | Could encode artifact preference |
| B01-35 | skip | episodic | casual_reaction | Explanation acknowledgement | Could encode explanation feedback |
| B01-36 | skip | episodic | casual_reaction | Surprise at sample result | Could generalize to demonstrations |
| B01-37 | skip | episodic | casual_reaction | Receipt confirmation | May be too terse |
| B01-38 | skip | boundary | current_turn_only | Single-answer depth constraint | Could imply general preference |
| B01-39 | skip | boundary | current_turn_only | One-document appendix choice | Could imply document policy |
| B01-40 | skip | boundary | current_turn_only | One-review feedback format | Could imply review habit |
| B01-41 | skip | project | one_off_detail | Deliverable-specific exception | Could become project constraint |
| B01-42 | skip | boundary | context_dependent | Unnamed document instruction | Missing referent is intentional |
| B01-43 | skip | project | context_dependent | Evaluation direction lacks criterion | Detail gap may seem repairable |
| B01-44 | skip | episodic | context_dependent | Correction lacks subject | Context may seem recoverable |
| B01-45 | skip | project | outdated_or_superseded | Replaced submission plan | Replacement has durable value |
| B01-46 | skip | project | speculative_or_unclear | Rejected study extension | Could sound like roadmap work |
| B01-47 | skip | project | speculative_or_unclear | Preliminary causal assessment | Could sound adopted |
| B01-48 | skip | project | context_dependent | Unscoped architecture rule | Reviewer may supply missing system |
| B01-49 | skip | project | speculative_or_unclear | Unauthorized reporting obligation | Detail may conceal non-acceptance |
| B01-50 | skip | episodic | one_off_detail | Large completed demonstration | Scale may resemble milestone |

## Post-Revision Audit

- Schema and fields: the file has the exact 12-column draft schema; all required fields are populated. All 50 rows use `assistant_assisted_draft`, `primary_eligible=true`, and `approved_for_migration`. Source-group, paraphrase-group, slot, and candidate-text values are unique.
- Exact duplicates: none within the 50 drafts and none against pilot v0.3 after normalized exact comparison.
- Near duplicates: no known direct semantic duplicates remain internally or against pilot v0.3 after the owner-guided revision. Automated surface comparison still identifies low-overlap thematic neighbors, especially recurring reviewer roles, bounded plans, acknowledgements, one-output instructions, and unaccepted information. Manual review found distinct subjects, scopes, or classification decisions rather than paraphrases. The closest pilot neighbors likewise share broad themes without duplicating the same memory decision.
- Existing-memory context: no row relies on whether Gamma already stores an equivalent memory.
- Policy and splits: no policy-only row was included, and no train, validation, or test assignment exists.

### Lexical-Shortcut Review

The audit checked class-level concentration and repeated reliance on shortcut wording; it did not require every potentially predictive term to be absent. Isolated obligation language such as “must” can express a legitimate durable project rule, and label quality must not depend on one word alone. B01-25 uses “must” as part of a real Gamma performer-event compatibility requirement; that occurrence is not by itself a dataset flaw. Other recurring terms, including approval language, occur on both sides of the label boundary, and no reviewed class-level pattern replaces the semantic keep/skip decision.

## Scope And Privacy Review

Keep rows were rewritten where needed to identify the user, project, system, artifact, or operational scope. Every retained project-specific architecture, tooling, reporting, or workflow rule names its relevant project or system as required by guide v0.3. Context-dependent skip rows intentionally retain missing information only when that absence is itself the diagnosable reason to skip: the missing document scope, evaluation criterion, corrected subject, or owning system is visible from the candidate alone. B01-48 deliberately omits the system and must not be repaired mentally during review.

The drafts are newly authored and synthetic. They contain no private names, usernames, account identifiers, credentials, secrets, private paths, copied logs, raw or copied personal conversations, runtime database content, hidden mappings, actual private Gamma memories, or sensitive personal facts. Synthetic project names and safe role descriptions are used where scope is required.

## Remaining Concerns

No audit defect required a row change before the owner-approved migration. Thematic neighbors and the nine medium-confidence rows remain relevant to later cleaning, but the audit found no exact duplicate, direct semantic duplicate, policy-only example, scope failure, or privacy failure that blocked migration.

## Approval Gate

The owner approved migration into the working primary-eligible dataset. This is not approval for model training, tuning, split assignment, evaluation, or a final class-submission dataset. Assistant-assisted provenance remains retained wherever the assistant materially shaped wording.
