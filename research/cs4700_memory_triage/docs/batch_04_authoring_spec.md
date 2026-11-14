# Batch 04 Memory-Triage Authoring Specification

Status: **Historical planning specification — owner reviewed, approved, and migrated.**

Scope: **Historical semantic inventory and completed migration provenance.**

Migrated size: **exactly 50 primary-eligible examples.**

Migrated balance: **exactly 25 `keep` and 25 `skip`.**

This document began as a pre-drafting plan based on direct inspection of the
207-row canonical dataset. The owner later reviewed the inventory and all 50
draft rows, approved them, and authorized their migration. No training,
evaluation, split assignment, notebook execution, or runtime integration
occurred.

## 1. Current Evidence Inspected

The review covered the repository-level `README.md`, specification hierarchy
and current-implementation inventory, the CS 4700 project specification, the
CS 4700 workspace and data READMEs, the labeling guide, dataset card, pilot
review, blinded relabel protocol, final expansion plan, frozen pilot CSV,
canonical primary CSV, all Batch 01 through Batch 03 authoring specifications,
candidate-draft CSVs, candidate reviews, and migration records, plus the
notebook structure without executing any cell.

Direct CSV inspection confirms that the canonical primary dataset contains
207 rows: 105 `keep` and 102 `skip`. Its provenance is 57 pilot v0.3 rows, 50
Batch 01 rows, 50 Batch 02 rows, and 50 Batch 03 rows. All 207 rows have
`primary_eligible=true` and `split=unassigned`.

Dataset-release provenance is recorded per row: the pilot and Batch 01 rows
carry `primary_v0_1`, Batch 02 carries `primary_v0_2`, and Batch 03 carries
`primary_v0_3`. Every current canonical row has a nonblank, unique
`source_group_id` and `paraphrase_group_id`; the pilot source file retains
shared groups for its three excluded policy-only paraphrases, but those rows
are not in the canonical primary dataset. Batch 4 planning therefore starts
from direct inspection of the current 207-row canonical dataset, not from a
reported checkpoint alone.

## 2. Constraints Carried Forward

A `keep` concept must support standalone information with likely value in a
separate future conversation: a lasting preference, reusable workflow rule,
stable scoped project fact, durable correction, continuing commitment, stable
safe relationship, or consequential milestone. A `skip` concept must be
text-solvably non-durable because it is bounded, vague, incidental,
non-authoritative, obsolete, speculative, artifact-specific, current-task
only, or otherwise lacks future behavioral value.

Every later example must be understandable from its candidate text alone.
Policy-only concepts and concepts requiring knowledge of an existing memory
store are excluded. Explicit save language cannot substitute for durability.
Project-specific architecture, tooling, reporting, workflow, compatibility,
or system rules must name the relevant project or system. An unscoped `keep`
is allowed only for a genuinely broad cross-project preference or safe
reusable rule.

Later wording must not turn project naming, approval status, completion,
future tense, obligation language, or words such as “temporary” and
“permanent” into label shortcuts. Named-system concepts must occur
meaningfully in both labels. `Keep` wording must demonstrate future value
without relying on permanence cues; `skip` wording must contain a precise
scope, provenance, authority, validity, or consequence reason without relying
on expiry vocabulary.

This semantic inventory is not a text template. Future Batch 4 drafting may
not convert its rows mechanically into repetitive sentences or reuse one
syntax with substituted projects, artifacts, roles, dates, or nouns.

## 3. Current Coverage And Gap Analysis

### Confirmed high-density families

The pilot and Batches 01–03 already provide dense coverage of answer and
report formatting preferences; accessibility presentation defaults; cautious
implementation, testing, approval, privacy, evidence, and review workflows;
named-system architecture, compatibility, fallback, migration, localization,
and data conventions; reviewer, ownership, and maintenance roles; and
commitments or milestones that establish baselines, support obligations, or
governance consequences.

The `skip` side is especially dense in short plans, casual reactions,
single-artifact instructions, generic closed tasks, unapproved proposals,
obsolete project facts, narrow or exhausted permissions, repair workarounds,
unsupported inferences, isolated technical observations, and completed events
with no continuing consequence. Batch 3 additionally covers borrowed
workflows, non-authoritative drafts, demonstration configurations, finite
programs, provisional taxonomies, non-adopted successful experiments,
rehearsal choices, inapplicable technical variants, transaction choices,
exercise pairings, consumed queue priorities, release-specific coordination,
local analogies, random assignment, closed access arrangements, unformed
maintenance roles, isolated compliance evidence, absence coverage, one-import
normalization, rejected-option rationale, and anomaly reconciliation. Batch 4
must not obtain novelty by renaming those concepts.

### Confirmed thinner boundaries

Direct counts and row review show comparatively thinner coverage for durable
corrections, stable safe relationship facts, consequential milestones, and
`skip` cases driven by attribution or provenance rather than simple event
closure. There is also room for distinctions involving ownership of a stated
preference, source authority versus source polish, continuing record-precedence
and data-state semantics, revocable user control, and invalidated evidence
where the validity defect is text-contained. Cross-label coverage of named
systems can be improved without adding another cluster of generic project
facts.

### Tentative opportunities requiring owner scrutiny

Some potentially useful boundaries are close to prior work: derived defaults
versus user preferences, training or simulation roles versus stable roles,
placeholders versus commitments, audience-scoped summaries versus governing
project sources, and technically credible results whose causal or subset scope
cannot support a broader conclusion. These remain tentative because weak
drafting could collapse into Batch 2 inference cases, Batch 3 exercise or
inapplicable-option cases, or generic completed-event skips. Owner review
should remove any slot whose distinct decision boundary cannot survive without
candidate-like explanation.

This plan proposes no model work, production integration, specification
change, or Gamma runtime change. Synthetic project renaming is not a source of
novelty. It specifically avoids another cluster of generic closed-event skips,
narrow-permission skips, temporary workflow skips, unsupported
technical-condition skips, or ordinary project-fact keeps.

## 4. Batch 4 Semantic Coverage Strategy

Batch 4 should add boundaries around attribution, authority, continuing
consequence, user control, evidence validity, causal interpretation, source
scope, and data semantics. Both labels should span preferences, boundaries,
named projects or systems, safe
roles, and episodic consequences instead of concentrating one label in a
single source style.

Later drafting must distribute lexical form, tense, project naming, approval
status, completion status, and event scope across both labels. Useful
ambiguity may test a genuine boundary, but the eventual label must still be
supported by text; missing decisive information cannot be manufactured merely
to make an example difficult. Each slot represents one concept. Template
clusters differing only by project name, artifact name, role name, date, or a
small wording change are prohibited.

High-confidence `skip` examples must not collapse into generic completed-event
descriptions. Their non-durability must follow from a precise, text-contained
reason such as incorrect attribution, non-governing provenance, invalid
evidence, bounded representativeness, absent authority, or lack of a
continuing consequence.

## 5. Fifty-Slot Semantic Inventory

The entries below are planning concepts only. They do not provide final
candidate text, source-record IDs, canonical IDs, CSV records, or final group
assignments.

| Slot | Planned label | Semantic family | Decision boundary being tested | Scope requirement | Candidate type or source-style target | Novelty / anti-overlap note | Planned grouping boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B04-01 | `keep` | Durable preference: decision visibility | User preference for seeing which assumptions materially drive a recommendation | May be broad cross-project | Preference; assistance-default extraction | Extends evidence calibration beyond B03-02 by focusing on decision sensitivity, not confidence | Keep separate from source traceability, option comparison, and correction style |
| B04-02 | `keep` | Durable preference: control | User preference for retaining an explicit stop or defer choice during multi-step assistance | May be broad cross-project | Preference; interaction-control summary | Distinct from previews, destructive-action approval, and long-job warnings; concerns continuing agency during execution | No variants based only on task length, button name, or tool name |
| B04-03 | `keep` | Durable preference: information recovery | User preference for resumable explanations after interruptions without repeating settled material | May be broad cross-project | Preference; communication-continuity style | New boundary beyond focus windows and status summaries; later value is conversational recovery | Do not group with scheduling, concise-answer, or handoff-record concepts |
| B04-04 | `keep` | Durable preference: counterevidence | User preference for surfacing credible evidence that conflicts with an initial choice | May be broad cross-project | Preference; recommendation style | Distinct from balanced comparisons and evidence limits because it governs disconfirming evidence | One preference only; exclude source citation and correction-feedback variants |
| B04-05 | `keep` | Accessibility preference: spatial orientation | Reusable need for location or navigation changes to be described without relying solely on visual position | May be broad cross-project | Preference; accessibility extraction | Adds spatial orientation beyond color, captions, timing, and document navigation | Exclude one venue, one diagram, and generic alternative-format variants |
| B04-06 | `keep` | Reusable rule: decision reversibility | Cross-project rule to record the practical point after which a consequential choice becomes costly to reverse | May be broad cross-project | Boundary; planning-rule style | Different from smallest-safe-change and rollback-trigger rows; identifies a decision boundary rather than a recovery action | No project-name substitutions or generic risk-review paraphrases |
| B04-07 | `keep` | Reusable rule: delegated action | Standing rule that delegated work must preserve the delegator's stated constraints and return unresolved choices | May be broad cross-project | Boundary; collaboration-workflow style | Distinct from handoff ownership and reviewer approval; tests constraint preservation across delegation | Exclude external contact, role assignment, and implementation ownership |
| B04-08 | `keep` | Reusable rule: evidence lineage | Standing rule to distinguish directly observed information from transformed or summarized evidence | May be broad cross-project | Boundary; evidence-handling style | Goes beyond source traceability by preserving transformation status | Do not turn into citation formatting, failed-case preservation, or dataset provenance |
| B04-09 | `keep` | Reusable boundary: automated interpretation | User retains review authority when automation converts qualitative input into consequential categories | May be broad cross-project | Boundary; safe control rule | New authority boundary; not a preview requirement, destructive action, or label-policy example | One human-review boundary; exclude production memory retention and model integration |
| B04-10 | `keep` | Reusable rule: uncertainty escalation | Standing cross-project trigger for seeking owner direction when equally supported choices have materially different consequences | May be broad cross-project | Boundary; decision-workflow style | Narrower than generic ambiguity clarification and safer-reversible-option preferences | Group only genuine consequence-based escalation; exclude routine approvals |
| B04-11 | `keep` | Named-system contract: identity continuity | Adopted system distinction between stable entity identity and changeable presentation metadata | Later wording must name the relevant project/system | Project; architecture-fact extraction | Related to B02-18 stable keys but tests identity semantics across presentation changes, not an identifier format | Avoid asset-key, canonical-record, and localization paraphrases |
| B04-12 | `keep` | Named-system contract: degraded write safety | Adopted behavior preventing uncertain partial writes from being represented as successful state | Later wording must name the relevant project/system | Project; operational-boundary style | New failure-semantics boundary beyond fallback access, rollback, and alerts | Exclude API-schema, retry, and transaction-implementation templates |
| B04-13 | `keep` | Durable project correction: boundary ownership | Corrected understanding of which subsystem owns validation versus enforcement, with continuing design consequence | Later wording must name the relevant project/system | Project; correction-style fact | Adds a responsibility correction without repeating component maintenance ownership or content/implementation division | One corrected boundary; no renamed team/component variants |
| B04-14 | `keep` | Named-system fact: provenance semantics | Adopted distinction between original records and later annotations that affects interpretation | Later wording must name the relevant project/system | Project; data-contract fact | Different from source traceability and dataset manifests; concerns semantic status within one system | Exclude generic lineage rules and CS 4700 dataset policy wording |
| B04-15 | `keep` | Named-system constraint: user-visible state | Adopted rule that externally reported status must reflect committed state rather than an internal attempt | Later wording must name the relevant project/system | Project; reporting-contract style | New state-meaning boundary; not alert impact, schema preservation, or success metrics | Avoid status-report template clusters and implementation-specific variants |
| B04-16 | `keep` | Stable relationship: bounded-preference liaison | Safe recurring role may clarify routine, non-consequential preferences in a named project or recurring context; material, consequential, personally sensitive, or consent decisions remain with the user | Later wording must identify the relevant project or recurring context without a real identity | Profile; role summary | Distinct from B03-18 tie-break authority, B03-20 support intake, B04-18 independent verification, and B04-19 stakeholder representation; grants no legal, medical, financial, relationship, privacy-consent, or other sensitive decision authority | One routine-preference clarification role and explicit authority limit; exclude one-event proxies and private identities |
| B04-17 | `keep` | Stable relationship: continuity role | Recurring role responsible for carrying unresolved decisions across project phases | Later wording must name the relevant project | Profile; collaboration summary | Distinct from handoff artifact contents and maintenance ownership; the role maintains decision continuity | Exclude one handoff, meeting notes, and release coordination |
| B04-18 | `keep` | Stable relationship: independent verifier | Recurring safe role that verifies a specific class of claims without owning implementation or acceptance | Later wording must name the relevant project/system | Profile; role-boundary extraction | Adds verification-only responsibility not covered by technical review, accessibility review, or decision authority | Do not vary only claim class or role title; no real identities |
| B04-19 | `keep` | Stable relationship: community interface | Recurring role that represents an identified stakeholder group in a named project's planning while lacking unilateral authority | Later wording must name the project and use a generic safe group | Profile; governance-role summary | Distinct from support intake and governance tie-break roles; tests representation versus authority | Exclude sensitive group identity, one consultation, and generic liaison paraphrases |
| B04-20 | `keep` | Durable role correction | Corrected role scope that changes where future requests or decisions must be directed | Later wording must name the relevant project/system | Profile; correction-style relationship fact | Thinner correction/relationship intersection; not a former role or ownership handoff | One current correction; exclude obsolete-role history and maintenance reassignment |
| B04-21 | `keep` | Consequential milestone: record precedence | Completed reconciliation between named records or systems establishes a continuing precedence rule for resolving future conflicts among those sources | Later wording must name the relevant project/system or named source relationship | Episodic milestone or stable project-fact extraction | Durable value is the established precedence rule, not the reconciliation event; distinct from B04-08 evidence lineage, B04-14 original-versus-annotation semantics, B03 canonical-content/localization boundaries, ordinary migration work, and generic governance authority | One named source relationship and precedence rule; exclude event-centered, migration, and governance variants |
| B04-22 | `keep` | Consequential milestone: evidence standard | Completed project event that established a continuing minimum evidence standard for later decisions | Later wording must name the relevant project | Episodic; milestone summary | Different from a baseline metric or isolated compliance success; consequence is a maintained evidentiary threshold | No evaluation metrics, certification, or routine check variants |
| B04-23 | `keep` | Ongoing commitment: decommission stewardship | Continuing responsibility to preserve necessary interpretive access while a named system retires a component | Later wording must name the relevant project/system | Project; lifecycle commitment | Adds retirement stewardship beyond deprecation status and backward compatibility | Exclude one migration, archival schedule, and obsolete-fact descriptions |
| B04-24 | `keep` | Ongoing commitment: correction propagation | Continuing obligation to carry an authoritative correction into all maintained representations of the same project fact | Later wording must name the relevant project/system | Project; maintenance commitment | New propagation consequence, distinct from glossary reconciliation and support-guide feedback | One correction class; no artifact-list template variants |
| B04-25 | `keep` | Consequential milestone: data-state semantics | Formally adopted data-model or schema decision establishes a continuing distinction between unknown, not-applicable, and confirmed-negative states in a named system | Later wording must name the relevant system/project and the continuing interpretive consequence | Project fact or consequential milestone | Useful retained fact is the ongoing semantic distinction, not the adoption event; distinct from B04-14 provenance semantics, B04-13 validation/enforcement ownership, B03-10 units/reference frames, B03-13 time interpretation, and generic schema compatibility | One named system and three-way state distinction; exclude provenance, validation ownership, and compatibility variants |
| B04-26 | `skip` | Misattributed preference: group choice | Group-level selection that provides no text-contained basis for attributing a lasting preference to the user | Must remain explicitly bounded/context-specific | Preference; attribution-error style | Hard negative beyond B02-45 one-action inference; error is individual attribution from collective choice | Exclude votes that establish project rules and one-artifact user choices |
| B04-27 | `skip` | Derived default: system-selected | A system-generated default presented in preference-like form without a user choice | Must name the relevant system and remain bounded | Preference; extraction-error style | New provenance boundary: automation, not observed behavior, supplied the value | Do not vary only setting names; exclude assistant inference and random assignment |
| B04-28 | `skip` | Quoted preference: source subject mismatch | Preference belongs to an explicitly identified example or third party rather than the user | Must remain explicitly bounded and privacy-safe | Preference; source-attribution style | Confirmed attribution gap; not vague context or existing-memory dependence | One subject mismatch; no private identities or template quotations |
| B04-29 | `skip` | Forced-choice response | Choice produced by a bounded calibration or form design that did not measure a general preference | Must identify the bounded elicitation context | Preference; structured-response summary | Different from trials and transaction choices; constrained measurement invalidates generalization | Exclude exercise pairing, surveys that establish durable preferences, and artifact appearance choices |
| B04-30 | `skip` | Source-fidelity format adaptation | Formatting or wording choice is required only to preserve fidelity to a named bounded source artifact or example and indicates no user preference, recurring accommodation, standing system format, or adopted style rule | Later wording must identify the bounded source artifact or example context | Preference-like extraction or artifact-provenance summary | Distinct from B03-26 physical-layout constraints, B03-41 local explanatory analogies, translation/localization rules, and broad formatting preferences; non-durability comes from bounded source fidelity rather than event closure | One source-bound adaptation; exclude physical constraints, accessibility needs, localization, and adopted style rules |
| B04-31 | `skip` | Non-governing source: tutorial rule | Polished rule belongs to instructional sample material rather than the named project's adopted operation | Must name the relevant project/system and the non-governing source relation | Boundary; source-provenance style | Beyond B03-28 draft authority: source is authoritative as teaching material but not for this project | Do not create handbook, proposal, or borrowed-workflow paraphrases |
| B04-32 | `skip` | Non-governing source: vendor recommendation | External recommendation is relevant but text establishes no adoption or binding effect in the named system | Must name the relevant project/system | Boundary or project; recommendation-extraction style | Tentative boundary distinct from generic unapproved proposals because provenance and governance are explicit | Group away from dependency lifecycle duties and rejected-option rationales |
| B04-33 | `skip` | Simulated representation authority | An apparent spokesperson or representative role exists only in a clearly identified simulation and never grants real project authority | Must identify the safe simulated context | Profile; role-attribution hard negative | Avoids B03-38 pairing by testing simulated apparent authority rather than collaboration | No exercise-role noun swaps; exclude temporary roles, absence coverage, former real roles, and unaccepted role offers |
| B04-34 | `skip` | Placeholder commitment | Calendar or planning placeholder reserves attention but text establishes no accepted obligation or recurring schedule | Must remain explicitly bounded/context-specific | Episodic or boundary; planning-record style | New commitment lookalike beyond tentative proposals and important meetings | Exclude cancelled events, finite recurring programs, and dates as the main cue |
| B04-35 | `skip` | Unestablished causal attribution | An observed project outcome follows a change or condition, but a concrete text-contained causal limitation prevents the sequence from establishing a durable causal claim or project rule | Later wording must name the relevant project/system and make the causal limitation text-contained | Analytical summary, incident interpretation, or project observation | Must rely on competing plausible causes, uncontrolled context, or another explicit causal limitation; distinct from B04-36 source contamination, B04-37 measurement substitution, B04-38 non-representative surrogate evidence, B03 closed diagnostic/anomaly examples, and generic assistant inference errors | One outcome and explicit causal limitation; exclude vague uncertainty and durable causal conclusions |
| B04-36 | `skip` | Invalidated evidence: source contamination | Technically specific result cannot support a durable conclusion because the text identifies contamination of its evidence source | Must name the relevant project/system | Project; analytical-result style | Adds a concrete validity defect beyond single observation and mismatched workloads | One validity defect; exclude generic failed experiments and anomaly fixes |
| B04-37 | `skip` | Invalidated evidence: measurement substitution | Reported measure answers a different question than the proposed durable claim | Must name the relevant project/system | Project; analytical-summary style | Cross-label contrast with corrected metric meanings; here no valid corrected conclusion is supplied | Do not draft a lasting replacement criterion or metric correction |
| B04-38 | `skip` | Non-representative compatibility result | Credible compatibility result applies only to a textually identified surrogate that cannot establish support for the target | Must name the relevant project/system and surrogate boundary | Project; technical-result hard negative | Distinct from absent features and demo configurations; tests evidentiary representativeness | Exclude emulator-name substitutions and generic unsupported-condition wording |
| B04-39 | `skip` | Scope collision: same-name subject | Durable-looking fact concerns a different explicitly distinguished system sharing a name with the target project | Must identify both safe synthetic scopes clearly | Project; subject-resolution style | New standalone subject-attribution boundary; not an unnamed-system failure | One collision only; reject if wording becomes confusing rather than text-solvable |
| B04-40 | `skip` | Snapshot state mistaken for project fact | Accurate checkpoint state is useful for current task continuity but has no long-term project meaning | Must name the relevant project/system and bounded checkpoint scope | Project; continuity-summary style | Adds current-working-state distinction without using runtime data or logs | Exclude repair workarounds, queue state, and closed task summaries |
| B04-41 | `skip` | Agreement without mandate | Consensus-like statement records alignment among participants who textually lack authority to set the named project's rule | Must name the project and use safe generic roles | Boundary or project; meeting-summary style | Authority gap differs from unapproved proposals and random assignment | One decision and authority boundary; exclude routine approvals and governance tie-breaks |
| B04-42 | `skip` | Example-data relationship | Apparent collaborator or stakeholder relation exists only inside a clearly identified synthetic example dataset | Must remain explicitly bounded and use no real identity | Profile; extraction-error style | New relationship provenance boundary; not an exercise pairing or inferred ownership | No domain-name swaps; exclude policy-only privacy decisions |
| B04-43 | `skip` | Third-party promise misbound | A continuing-sounding promise is explicitly made by an outside party and creates no obligation for the user or named project | Must identify safe generic parties and project scope | Project or profile; commitment-attribution style | Adds obligation ownership rather than cancellation or non-adoption | Exclude vendor recommendations, role offers, and access authorization |
| B04-44 | `skip` | Audience-scoped public summary | Public-facing or orientation-focused summary presents an intentionally simplified project statement for a named audience but does not govern internal operation, implementation, or future project decisions | Later wording must name the project/system, the audience-oriented artifact, and the non-governing limitation | Project communication or source-provenance summary | Distinct from B04-31 instructional/tutorial material, B03-28 non-authoritative draft rules, B03-41 local explanatory analogies, and B03-26 one-artifact physical constraints; skip follows from audience-limited authority rather than history or completion | One audience-scoped artifact and authority limit; exclude instructional rules, drafts, analogies, and physical-format constraints |
| B04-45 | `skip` | Imported label without local meaning | Category name appears in received material, but the text establishes no mapping or adopted meaning in the named project | Must name the relevant project/system and received-material scope | Project; data-intake style | Different from one-import normalization and provisional taxonomy; the issue is uninterpretable semantics | One unmapped label; exclude vague missing referents and glossary adoption |
| B04-46 | `skip` | Milestone-like ceremony without consequence | Formal recognition marks participation but creates no project status, authority, obligation, or maintained artifact | Must remain explicitly bounded and privacy-safe | Episodic; milestone hard negative | Tests formality without returning to generic task completion | Do not use completion words as the reason; exclude certifications and awards with real consequences |
| B04-47 | `skip` | Incident narrative without reusable finding | Detailed account remains only illustrative because text says it was selected as an example, not retained as project evidence | Must name the relevant project or training context | Episodic; source-purpose style | Distinct from closed investigations: non-durability comes from illustrative provenance | Avoid generic resolved-incident language and tutorial-rule overlap |
| B04-48 | `skip` | Forecast recorded as fact | Precise future estimate is explicitly model-produced and has no adopted planning authority | Must name the relevant project/system | Project; forecast-extraction style | Adds source-status boundary beyond speculation and preliminary comparison | One forecast; exclude model evaluation, metrics, and unapproved design plans |
| B04-49 | `skip` | Subset-derived status overreach | A correct statistic, status, or conclusion applies only to an explicitly bounded subset and cannot establish the named system's overall condition | Later wording must name the relevant project/system and make the subset boundary clear | Analytical report, status summary, or evidence-provenance extraction | Literal subset result may be accurate; failure is overgeneralizing its valid scope. Distinct from B04-37 wrong-question measurement, B04-38 non-representative surrogate compatibility, B04-40 bounded checkpoint state, and B04-50 local-environment behavior | One bounded subset and prohibited system-wide inference; exclude incorrect measures, surrogate compatibility, snapshots, and environment exceptions |
| B04-50 | `skip` | Local exception reported as norm | A single bounded environment's behavior is accurately reported but text establishes that it is not the named system's general contract | Must name the system and bounded environment | Project; operational-observation style | Hard negative distinct from repair workaround and demo configuration; tests environment-to-system overgeneralization | One environment boundary; exclude unsupported hypotheses and one-run capacity state |

## 6. Coverage And Balance Audit

| Planned label | Semantic-family distribution | Count |
| --- | --- | ---: |
| `keep` | Assistance, control, continuity, counterevidence, and accessibility preferences | 5 |
| `keep` | Reversibility, delegation, evidence, review-authority, and escalation rules | 5 |
| `keep` | Named-system identity, write, ownership, provenance, and state contracts | 5 |
| `keep` | Bounded-preference, continuity, verification, representation, and corrected role facts | 5 |
| `keep` | Record precedence, evidence, retirement, correction, and data-state consequences | 5 |
| **`keep` total** |  | **25** |
| `skip` | Misattributed, derived, quoted, forced, and source-fidelity-limited preferences | 5 |
| `skip` | Non-governing sources, simulated authority, placeholders, and causal overreach | 5 |
| `skip` | Invalid evidence, subject collision, checkpoint state, and authority gaps | 5 |
| `skip` | Synthetic relationships, third-party obligations, audience scope, and unmapped semantics | 5 |
| `skip` | Consequence-free formality, illustrative provenance, forecasts, subset overreach, and local exceptions | 5 |
| **`skip` total** |  | **25** |

Batch 4 differs from earlier batches by concentrating on who or what a fact is
actually about, whether its source can govern the named scope, whether evidence
supports the durable-looking conclusion, and whether formal status creates a
continuing consequence. It adds thinner corrections, relationship boundaries,
and consequential milestones without filling the batch with ordinary project
facts or generic closed events.

Shortcut risk is reduced by placing named systems, formal language, future
orientation, apparent authority, technical detail, and consequential-looking
facts in both labels. The planned decisions turn on attribution, scope,
validity, authority, and future behavioral value rather than keywords, tense,
project presence, approval language, or event completion.

All 50 slots became `primary_eligible=true` after successful drafting, review,
and migration. No slot is policy-only, requires existing-memory context, or
represents a production retention-policy decision.

The maintained project target remains approximately 200 `keep` and 200 `skip`.
Migration of this balanced batch produced 130
`keep` and 127 `skip`, leaving approximately 70 `keep` and 73 `skip` toward
that target. These are planning figures, not authority to retain weak or
overlapping examples.

## 7. Completed Owner Review Gates

1. The owner explicitly reviewed and approved this inventory before candidate
   drafting.
2. Drafting occurred in a separate review-only CSV, not in this specification.
3. The drafts were re-audited against the then-current canonical dataset
   for privacy, scope, exact duplicates, conceptual overlap, lexical shortcuts,
   and source/paraphrase grouping.
4. The owner explicitly approved all 50 reviewed Batch 4 rows before canonical
   migration.
5. Training remains blocked until the final target, cleaning, group review,
   and frozen untouched group-aware holdout steps are completed. Evaluation,
   metric calculation, split assignment, and notebook execution also remain
   blocked.
6. Runtime integration remains blocked and requires a separate explicitly
   approved task. Any future classifier remains advisory-only and disabled by
   default unless a later approved runtime task changes that status.
