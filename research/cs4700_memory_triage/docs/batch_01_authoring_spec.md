# Batch 01 Memory-Triage Authoring Specification

Status: Proposed for owner approval; no batch rows or candidate text exist

Guide version: `v0.3`

## Purpose And Boundary

This document is the authoring and review specification for the first final-
dataset expansion batch. It is a plan and slot inventory only. It does not
reserve CSV rows, assign final example IDs or splits, or provide candidate
text, sentence stems, partial wording, or copy-ready examples.

Batch 01 plans exactly 50 new primary-eligible, standalone examples: 25
`keep`, 25 `skip`, and no policy-only examples. Every future candidate must be
decidable from its text alone under labeling guide v0.3. Existing-memory state,
conversation context unavailable in the candidate, and other external policy
signals must not determine a label.

The balanced research dataset supports model development and error analysis.
It does not estimate the prevalence of `keep` or `skip` candidates in Gamma
production use and must not be used to imply calibrated production
probabilities.

## Semantic-Inventory Revision Note

Before candidate drafting, the Batch 01 semantic inventory was revised to
replace 18 slots whose prior purposes materially overlapped pilot v0.3. The
replacements change the concepts and future classification decisions, not
merely their wording. No candidate text, partial candidate wording, dataset
rows, or split assignments were created during this revision.

## Count Arithmetic

The frozen pilot has 57 primary-eligible examples: 30 `keep` and 27 `skip`.
Batch 01 adds 25 primary-eligible examples per class. After future Batch 01
authoring and approval, the expected primary-eligible totals are therefore:

| Stage | `keep` | `skip` | Total |
| --- | ---: | ---: | ---: |
| Frozen pilot | 30 | 27 | 57 |
| Planned Batch 01 addition | 25 | 25 | 50 |
| Expected after Batch 01 | 55 | 52 | 107 |
| Approximate remaining need for a 200/200 target | 145 | 148 | 293 |
| Recommended final target | 200 | 200 | 400 |

Later batches must use the class-specific remaining need rather than adding
equal counts automatically; otherwise the pilot's three-example primary-class
imbalance would be preserved or amplified.

## Authoring Controls

- Each future row must be newly authored, Gamma-style, privacy-safe,
  standalone, `primary_eligible=true`, and consistent with guide v0.3.
- The slot's primary category controls its quota. Secondary characteristics do
  not move a slot between categories.
- Candidate types and rationale codes must use the existing dataset vocabulary
  shown in the inventory. A new value requires a separately approved contract
  change before drafting.
- Project-specific architecture, tooling, reporting, and workflow material
  must identify the relevant project or system in the future candidate.
  Broadly reusable preferences and safe cross-project rules need no artificial
  project scope.
- Every future candidate must be semantically independent. No Batch 01 slot is
  intended as a paraphrase, minimal pair, or lexical variant of another slot.
- Obvious permanence or transience cues may be semantically appropriate, but
  they must not make the class predictable. Review meaning, not isolated
  keywords.
- No train, validation, or test assignment may be made during Batch 01
  authoring.

The inventory uses provisional planning keys `S01` through `S50` for source
concepts and `P01` through `P50` for paraphrase concepts. They are not final
CSV values or reserved rows. During future materialization, each key receives
an opaque identifier. A source key may be merged with another group only when
the audit finds shared provenance or a common source concept. Every paraphrase
key is intentionally unique within Batch 01; if review finds a near duplicate,
the rows must be rewritten, removed, or grouped before approval.

## Keep Slot Inventory

All 25 keep concepts must be drafted without relying on `always`, `forever`,
`remember`, or `future` as evidence of durability. This exceeds the minimum
six durable concepts without obvious permanence language. Slots B01-01 through
B01-04 are deliberately flexible or revisable defaults; their future wording
must express usefulness as a default without claiming permanence.

Planning descriptions below are semantic metadata, not candidate wording or
sentence fragments.

| Slot | Primary category | Label | Type | Rationale | Semantic purpose | Scope requirement | Lexical-diversity requirement | Source-group strategy | Paraphrase-group strategy | Review risk or ambiguity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B01-01 | Preference / communication / accessibility | `keep` | `preference` | `durable_preference` | Clarification-before-action default for ambiguous requests | Broad cross-conversation assistance preference | Express the question-versus-action decision without response-length or detail-level framing | `S01`: independent interaction preference; merge only on discovered common provenance | `P01`: unique concept; no variant planned | Accidental one-request scope or drift into a generic preference for more or less detail |
| B01-02 | Preference / communication / accessibility | `keep` | `preference` | `durable_preference` | Preferred order of a recommendation and its supporting rationale | Broad assistance preference | Ordering semantics without confirmed-fact, assumption, or uncertainty-presentation framing | `S02`: independent communication preference | `P02`: unique concept; no variant planned | Confusion with a task-specific report format rather than a reusable assistance preference |
| B01-03 | Preference / communication / accessibility | `keep` | `preference` | `accessibility_preference` | Navigable information organization | Broad accessibility use across suitable outputs | Flexible-default framing; accessibility meaning without absolute quantifiers | `S03`: independent accessibility concept | `P03`: unique concept; no variant planned | Unsupported medical inference or excessive personal sensitivity |
| B01-04 | Preference / communication / accessibility | `keep` | `preference` | `durable_preference` | Correction-feedback style | Cross-conversation default | Flexible-default framing; conditional structure unlike pilot preference templates | `S04`: independent feedback preference | `P04`: unique concept; no variant planned | One-review instruction disguised as a general preference |
| B01-05 | Preference / communication / accessibility | `keep` | `preference` | `durable_preference` | Comparison-presentation preference | Broadly reusable for option analysis | Preference semantics without preference cue words or permanence terms | `S05`: independent comparison preference | `P05`: unique concept; no variant planned | Too little behavioral value to justify retention |
| B01-06 | Preference / communication / accessibility | `keep` | `preference` | `accessibility_preference` | Non-color information encoding | Broad accessibility requirement for visual material | Constraint syntax distinct from pilot chart and caption concepts | `S06`: independent accessibility concept | `P06`: unique concept; no variant planned | Drifting into a single presentation artifact |
| B01-07 | Reusable workflow rule | `keep` | `boundary` | `reusable_rule` | Review-order convention | Safe cross-project workflow; human or decision review rather than test execution | Procedural noun/verb variety; avoid the pilot's focused-before-broad testing sequence | `S07`: independent workflow concept | `P07`: unique concept; no variant planned | Merely conventional advice rather than a user-specific rule |
| B01-08 | Reusable workflow rule | `keep` | `boundary` | `reusable_rule` | Decision-recording practice | Safe cross-project workflow | Outcome-focused construction unlike pilot approval rules | `S08`: independent workflow concept | `P08`: unique concept; no variant planned | Project-specific reporting rule without named scope |
| B01-09 | Reusable workflow rule | `keep` | `boundary` | `reusable_rule` | Small isolated changes before broader implementation work | Cross-project workflow preference | Scope-and-isolation framing without rollback, reversible-action, or approval concepts | `S09`: independent workflow concept | `P09`: unique concept; no variant planned | Generic incremental-development advice with insufficient user-specific workflow value |
| B01-10 | Reusable workflow rule | `keep` | `boundary` | `reusable_rule` | Evidence-before-escalation workflow | Safe cross-project workflow | Evidence vocabulary and sentence shape distinct from testing-order examples | `S10`: independent workflow concept | `P10`: unique concept; no variant planned | Excessively broad rule or hidden one-task context |
| B01-11 | Reusable workflow rule | `keep` | `boundary` | `reusable_rule` | Approval handoff sequence | Safe cross-project workflow with a clear recurring internal decision point | Role-neutral process phrasing; exclude publication and external-contact decisions | `S11`: independent workflow concept | `P11`: unique concept; no variant planned | Drifting into either pilot approval rule instead of an internal handoff sequence |
| B01-12 | Security or project constraint | `keep` | `boundary` | `recurring_constraint` | Least-disclosure handling of shared artifacts | Broadly safe cross-project artifact-sharing rule, not a dataset-content rule | Data-minimization meaning without the pilot's secret and private-identifier exclusion concept | `S12`: independent security concept | `P12`: unique concept; no variant planned | Privacy policy claim broader than the intended safe sharing rule |
| B01-13 | Security or project constraint | `keep` | `boundary` | `recurring_constraint` | Approval boundary for destructive operations | Broadly safe cross-project constraint | Risk-and-consent framing distinct from external-service approval | `S13`: independent safety concept | `P13`: unique concept; no variant planned | Duplication of general approval concepts in the pilot |
| B01-14 | Security or project constraint | `keep` | `project` | `recurring_constraint` | CS 4700 dataset release constraint | Explicit CS 4700 memory-triage project scope and release gate distinct from content-level privacy exclusion | Named-project release-control concept without permanence words or pilot privacy-list wording | `S14`: independent scoped security concept | `P14`: unique concept; no variant planned | Restating existing project documentation too closely |
| B01-15 | Security or project constraint | `keep` | `project` | `recurring_constraint` | CS 4710 screenshot-data redaction before dataset admission | Explicit CS 4710 project scope and durable image-admission rule | Redaction and image-provenance framing without Gamma integration or advisory-mode concepts | `S15`: independent scoped dataset constraint | `P15`: unique concept; no variant planned | Collapsing into a one-time redaction task instead of a continuing class-project rule |
| B01-16 | Ongoing project fact | `keep` | `project` | `stable_project_fact` | Named project's adopted annotation workflow | Explicit project name and adopted status | Declarative project fact without modal permanence cues | `S16`: independent adopted-design concept | `P16`: unique concept; no variant planned | Draft proposal misrepresented as an accepted fact |
| B01-17 | Ongoing project fact | `keep` | `project` | `stable_project_fact` | Named project's canonical dataset-manifest or record-source convention | Explicit project and canonical artifact scope | Provenance-record convention without API, dashboard, interface-ownership, or process-separation framing | `S17`: independent artifact-convention concept | `P17`: unique concept; no variant planned | Artifact or project left unnamed, or convention described as an unadopted proposal |
| B01-18 | Ongoing project fact | `keep` | `project` | `durable_correction` | Named project's replaced design assumption | Explicit project name, prior assumption, and adopted correction outside label-count or classification-design choices | Correction semantics without superseded-skip phrasing | `S18`: independent correction concept | `P18`: unique concept; no variant planned | History only, with no continuing behavioral value |
| B01-19 | Ongoing project fact | `keep` | `project` | `stable_project_fact` | Named project's active multi-stage objective | Explicit project name and concrete continuing objective | Objective framing without `ongoing` or open-ended time words | `S19`: independent objective concept | `P19`: unique concept; no variant planned | Short task mistaken for a project-level fact |
| B01-20 | Stable relationship or role fact | `keep` | `profile` | `stable_relationship` | Recurring technical-review role | Safe role description with technical responsibilities distinct from methodology and accessibility review; no identity | Responsibility-centered structure unlike mentor and accessibility-review pilot rows | `S20`: independent role concept | `P20`: unique concept; no variant planned | Role may be temporary or lack future assistance value |
| B01-21 | Stable relationship or role fact | `keep` | `profile` | `stable_relationship` | Recurring data-provenance or dataset-quality review role | Safe, identity-free project role with recurring provenance or quality responsibility | Dataset-review vocabulary without accessibility or mentor-role framing | `S21`: independent role concept | `P21`: unique concept; no variant planned | Responsibility described as a one-time review rather than a stable project role |
| B01-22 | Stable relationship or role fact | `keep` | `profile` | `stable_relationship` | Stable project decision authority | Safe role description tied to a named project; no identity | Authority-focused syntax without approval-template repetition | `S22`: independent role concept | `P22`: unique concept; no variant planned | Confusing a process rule with a relationship fact |
| B01-23 | Milestone or ongoing commitment | `keep` | `episodic` | `meaningful_milestone` | Formal acceptance or signoff milestone establishing an approved operating boundary | Named project, completed formal approval, and continuing relevance of the approved boundary | Acceptance-boundary framing without evaluation-baseline, testing, or model-performance concepts | `S23`: independent milestone concept | `P23`: unique concept; no variant planned | Routine approval mistaken for a durable boundary-setting milestone |
| B01-24 | Milestone or ongoing commitment | `keep` | `project` | `ongoing_commitment` | Recurring audit commitment | Named project, responsible group, and repeatable audit artifact distinct from evaluation reporting | Commitment meaning without frequency shortcuts or future cue words | `S24`: independent commitment concept | `P24`: unique concept; no variant planned | Unaccepted aspiration or report-publication commitment rather than an audit obligation |
| B01-25 | Milestone or ongoing commitment | `keep` | `project` | `ongoing_commitment` | Continuing compatibility obligation | Explicit named system and concrete interoperability or artifact-compatibility obligation, excluding runtime-version support | Obligation phrasing unlike pilot support-version and reporting commitments | `S25`: independent commitment concept | `P25`: unique concept; no variant planned | Architecture constraint category overlap; primary category must remain commitment |

## Skip Slot Inventory

Skip drafting must not depend mainly on `today`, `temporary`, `current`,
`just`, or `once`. Slots B01-26, B01-29, B01-41, B01-43, and B01-45 through
B01-49 are designated important- or future-sounding cases whose subject,
scope, durability, acceptance, or actionable detail remains insufficient. This
provides nine planned cases, exceeding the minimum six. Slots B01-38 through
B01-40 are the required one-time-instruction versus durable-general-preference
distinctions; their concepts are independent and are not paired paraphrases.

| Slot | Primary category | Label | Type | Rationale | Semantic purpose | Scope requirement | Lexical-diversity requirement | Source-group strategy | Paraphrase-group strategy | Review risk or ambiguity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B01-26 | Temporary plan | `skip` | `episodic` | `temporary_state` | High-importance preparation plan with a near endpoint | Complete plan and endpoint; text-only decision | Importance framing without obvious transient cue words | `S26`: independent short-plan concept | `P26`: unique concept; no variant planned | Mistaking urgency or importance for durability |
| B01-27 | Temporary plan | `skip` | `episodic` | `temporary_state` | Session-bounded work sequence | Explicitly self-contained scope without deictic context | Sequential syntax; avoid time-adverb shortcut | `S27`: independent session-plan concept | `P27`: unique concept; no variant planned | Missing context rather than transience becoming the real reason |
| B01-28 | Temporary plan | `skip` | `episodic` | `temporary_state` | Temporary venue, room, or meeting-space arrangement for one event | Complete event arrangement with a natural endpoint | Event-space vocabulary without recurring-workspace or equipment framing | `S28`: independent event-arrangement concept | `P28`: unique concept; no variant planned | Accidentally describing a recurring workspace preference |
| B01-29 | Temporary plan | `skip` | `episodic` | `temporary_state` | Future-facing review schedule limited to one cycle | Concrete event scope and natural endpoint | Future orientation without `future`, date, or temporary markers | `S29`: independent schedule concept | `P29`: unique concept; no variant planned | Recurring commitment implied unintentionally |
| B01-30 | One-time event or incident | `skip` | `episodic` | `one_off_detail` | Completed one-time document conversion or formatting task with no reusable consequence | Complete closed task and bounded document artifact | Transformation vocabulary without milestone or recurring-template implications | `S30`: independent document-task concept | `P30`: unique concept; no variant planned | Routine task presented as an approved template or meaningful milestone |
| B01-31 | One-time event or incident | `skip` | `episodic` | `one_off_detail` | Resolved scheduling or coordination disruption without continuing project effect | Self-contained closed incident with clear resolution | Coordination-incident wording without tooling, defect, or recurrence framing | `S31`: independent coordination-incident concept | `P31`: unique concept; no variant planned | Recurring process problem or continuing obligation implied |
| B01-32 | One-time event or incident | `skip` | `episodic` | `one_off_detail` | Incidental seating or location choice during one closed activity | Complete event context with no continuing venue implication | Spatial-choice vocabulary without beverage, equipment, or display framing | `S32`: independent incidental-event concept | `P32`: unique concept; no variant planned | Choice reads as a lasting location or workspace preference |
| B01-33 | One-time event or incident | `skip` | `episodic` | `one_off_detail` | Completed coordination event without continuing obligation | Standalone participants by safe roles only | Event structure without dates or expiry vocabulary | `S33`: independent coordination-event concept | `P33`: unique concept; no variant planned | Stable relationship inferred from one interaction |
| B01-34 | Casual reaction or acknowledgement | `skip` | `episodic` | `casual_reaction` | Positive reaction to one diagnostic artifact | Named artifact class; no missing referent | Reaction expressed without generic praise adjective | `S34`: independent reaction concept | `P34`: unique concept; no variant planned | Durable feedback preference inferred from one reaction |
| B01-35 | Casual reaction or acknowledgement | `skip` | `episodic` | `casual_reaction` | Acknowledgement of a completed explanation | Complete interaction object | Indirect acknowledgement syntax unlike thanks in pilot | `S35`: independent acknowledgement concept | `P35`: unique concept; no variant planned | Useful feedback about explanation quality hidden in wording |
| B01-36 | Casual reaction or acknowledgement | `skip` | `episodic` | `casual_reaction` | Momentary surprise tied to one sample or demonstration artifact | Named artifact class and closed context | Surprise vocabulary without amusement, laughter, praise, or broad tone framing | `S36`: independent reaction concept | `P36`: unique concept; no variant planned | One reaction generalized into a broad tone or demonstration preference |
| B01-37 | Casual reaction or acknowledgement | `skip` | `episodic` | `casual_reaction` | Neutral receipt confirmation | Complete object and completed handoff | Minimal but grammatical acknowledgement without greeting/thanks cues | `S37`: independent acknowledgement concept | `P37`: unique concept; no variant planned | Too fragmentary to satisfy standalone design |
| B01-38 | One-draft or one-task instruction | `skip` | `boundary` | `current_turn_only` | Single-answer explanation-depth constraint | Explicit one-answer scope | Contrast case: task-local instruction without transience keywords | `S38`: independent instruction concept | `P38`: unique concept; no durable-preference pair planned | General explanation preference inferred from one answer |
| B01-39 | One-draft or one-task instruction | `skip` | `boundary` | `current_turn_only` | One-deliverable bounded content-inclusion decision | Explicitly limited to one named artifact and one section or appendix decision | Content-scope framing without color, heading, chart, layout, or display-density concepts | `S39`: independent artifact instruction concept | `P39`: unique concept; no durable-preference pair planned | One-artifact decision mistaken for a reusable document policy |
| B01-40 | One-draft or one-task instruction | `skip` | `boundary` | `current_turn_only` | Single-review feedback format | Explicit named review/task scope | Contrast case: instruction syntax distinct from standing workflow rules | `S40`: independent review instruction concept | `P40`: unique concept; no durable-preference pair planned | Recurring review habit implied accidentally |
| B01-41 | One-draft or one-task instruction | `skip` | `project` | `one_off_detail` | Important-sounding exception for one deliverable | Named deliverable and bounded exception | Formal constraint vocabulary without obvious one-time markers | `S41`: independent deliverable instruction concept | `P41`: unique concept; no variant planned | Exception becomes a durable project constraint |
| B01-42 | Vague or missing-context statement | `skip` | `boundary` | `context_dependent` | Instruction referring to an unnamed document or deliverable | Grammatical and realistic instruction whose missing artifact referent is visible from the text | Document-action ambiguity without option-selection, explicit-save, or temporary-task framing | `S42`: independent missing-referent concept | `P42`: unique concept; no variant planned | Temporary scope, rather than the missing object, becoming the reason for `skip` |
| B01-43 | Vague or missing-context statement | `skip` | `project` | `context_dependent` | Named project direction lacking an actionable success criterion or decision rule | Project is identified, but the required metric, threshold, success criterion, or decision rule is absent | Technical assessment framing without configuration reuse or existing-memory dependence | `S43`: independent missing-criterion concept | `P43`: unique concept; no variant planned | Mere lack of implementation detail mistaken for a genuinely missing decision criterion |
| B01-44 | Vague or missing-context statement | `skip` | `episodic` | `context_dependent` | Incomplete correction lacking corrected subject | Missing subject evident from text alone | Correction form unlike explicit-save and pronoun-heavy pilot cases | `S44`: independent incomplete-correction concept | `P44`: unique concept; no variant planned | Potentially recoverable meaning from unstated conversation context |
| B01-45 | Superseded, speculative, or unaccepted information | `skip` | `project` | `outdated_or_superseded` | Superseded communication, submission, or stakeholder plan with an explicit replacement | Named project, former plan, and internally stated replacement | Plan-transition vocabulary without architecture, model-design, or generic obsolete-fact framing | `S45`: independent superseded-plan concept | `P45`: unique concept; no variant planned | Replacement itself may carry durable value if the purpose shifts from rejecting the former plan |
| B01-46 | Superseded, speculative, or unaccepted information | `skip` | `project` | `speculative_or_unclear` | Unadopted data-collection, documentation, or study extension | Named project and clear non-adoption | Research-scope proposal framing without integration or feature concepts | `S46`: independent unaccepted-extension concept | `P46`: unique concept; no variant planned | Wording accidentally signals an approved study plan or roadmap item |
| B01-47 | Superseded, speculative, or unaccepted information | `skip` | `project` | `speculative_or_unclear` | Preliminary interpretation, assessment, or planned direction discussed before evidence or approval was complete | Clear subject and explicit lack of final adoption | Provisional-analysis framing without user-preference or option-selection concepts | `S47`: independent preliminary-assessment concept | `P47`: unique concept; no variant planned | Preliminary status omitted, causing the content to read as an adopted project fact |
| B01-48 | Difficult borderline case | `skip` | `project` | `context_dependent` | Durable-sounding architecture rule with unnamed system | Deliberately unscoped project-specific subject; label text-solvable under v0.3 | Strong durability semantics without permanence terms | `S48`: independent scope-borderline concept | `P48`: unique concept; no scoped counterpart planned | Reviewer may repair scope mentally instead of judging written text |
| B01-49 | Difficult borderline case | `skip` | `project` | `speculative_or_unclear` | Future-facing obligation pending authorization | Named project, concrete obligation, and explicit lack of acceptance | Commitment-like register without tentative modal shortcuts | `S49`: independent acceptance-borderline concept | `P49`: unique concept; no accepted counterpart planned | Concrete detail may obscure missing approval state |
| B01-50 | Difficult borderline case | `skip` | `episodic` | `one_off_detail` | Impressive completed activity without reusable consequence | Complete event, named safe project, and no continuing value | Achievement register distinct from routine-event and milestone templates | `S50`: independent value-borderline concept | `P50`: unique concept; no milestone counterpart planned | Reviewer may equate effort or impressiveness with future utility |

## Pilot V0.3 Semantic-Overlap Review

The following table records the 18 concept-level replacements. The pilot
descriptions are abstract overlap categories, not copied pilot wording.

| Slot | Prior overlap category | New semantic direction | Why the revised purpose is distinct from pilot v0.3 |
| --- | --- | --- | --- |
| B01-01 | Response-length or response-density preference | Clarification before action on ambiguous requests | Tests whether to ask before acting, not how much detail an answer should contain. |
| B01-02 | Separation of confirmed information and assumptions | Ordering of recommendation and supporting rationale | Tests presentation sequence for advice, not uncertainty classification or fact status. |
| B01-09 | Reversible-option workflow | Small isolated changes before broader implementation | Tests implementation scope and increment size without rollback, reversibility, or approval semantics. |
| B01-15 | Advisory integration safety boundary | CS 4710 screenshot redaction before dataset admission | Tests a durable image-data admission rule in a different named class project, not runtime integration status. |
| B01-17 | API, dashboard, or process responsibility | Canonical dataset-manifest or record-source convention | Tests an adopted project artifact convention, not software interface ownership or process separation. |
| B01-21 | Accessibility review role | Data-provenance or dataset-quality review role | Tests a different recurring project responsibility without accessibility or mentor duties. |
| B01-23 | Evaluation-baseline milestone | Formal acceptance milestone establishing an operating boundary | Tests continuing relevance from formal signoff, not reusable model or testing performance. |
| B01-28 | Temporary equipment setup | One-event venue, room, or meeting-space arrangement | Tests event-bounded space logistics rather than borrowed or short-lived equipment use. |
| B01-30 | Routine completion with no follow-up | Closed document-conversion or formatting task | Tests a bounded artifact transformation, not worksheet completion or a milestone. |
| B01-31 | Isolated tool failure | Resolved scheduling or coordination disruption | Tests a closed human-coordination incident without a technical defect or durable workaround. |
| B01-32 | Incidental resource or consumption choice | Incidental seating or location choice in one activity | Tests a bounded spatial choice without implying an enduring preference. |
| B01-36 | Amusement at a sample artifact | Momentary surprise at a sample or demonstration artifact | Uses a different one-event emotional state and excludes humor or laughter preference. |
| B01-39 | Artifact-specific visual style or density | One-deliverable bounded content-inclusion decision | Tests whether one artifact includes a section or appendix, not color, headings, charts, layout, or density. |
| B01-42 | Unnamed option or action-selection reference | Instruction with an unnamed document or deliverable | The missing reusable object is an artifact referent, not an option identity or explicit-save object. |
| B01-43 | Reuse of an unnamed option or configuration | Named project direction missing an actionable criterion | The project is named; the failure is an absent metric, threshold, success criterion, or decision rule rather than missing stored context. |
| B01-45 | Superseded architecture or model design | Replaced communication, submission, or stakeholder plan | Tests obsolescence in project coordination with an internal replacement, not technical architecture. |
| B01-46 | Unapproved integration or feature proposal | Unadopted data-collection, documentation, or study extension | Tests non-adoption of research scope rather than a software integration or product feature. |
| B01-47 | Discussed but unchosen user preference | Preliminary interpretation, assessment, or planned direction | Tests incomplete evidence or approval for a project judgment, not absence of a user preference. |

The other 32 slots were reviewed against all primary-eligible pilot v0.3
concepts. No remaining direct semantic overlap was found. Four slots required
no additional guardrail: B01-04, B01-05, B01-16, and B01-19. The previously
identified retain-with-guardrails group remains conceptually unchanged:
B01-03, B01-06--B01-08, B01-10--B01-14, B01-18, B01-20, B01-22,
B01-24--B01-27, B01-29, B01-33--B01-35, B01-37--B01-38, B01-40--B01-41,
B01-44, and B01-48--B01-50. Where needed, its scope, lexical-diversity, or
review-risk fields now explicitly exclude the neighboring pilot decision; no
underlying purpose in that group was replaced.

## Privacy And Provenance Requirements

Future drafting must use manually authored, privacy-safe Gamma-style material.
The following are prohibited:

- raw conversation content or lightly edited conversation content;
- real names, usernames, account identifiers, or other private identifiers;
- passwords, tokens, credentials, API keys, or private paths;
- copied runtime logs, traces, databases, stored memories, or continuity data;
- private project details unsuitable for a class dataset;
- sensitive personal facts unless a separate, explicit privacy decision has
  approved the exact category and handling before drafting.

A privacy failure is removed; it is not converted to a `skip` example.

Future provenance must distinguish authorship from labeling. Before rows are
materialized, the owner must approve controlled `candidate_source` values that
unambiguously separate fully human-authored material from assistant-assisted
draft material. The recommended semantics are `manual_human_authored` for text
written without assistant drafting and `assistant_assisted_draft` for any text
whose wording originated from or was materially shaped by an assistant. Human
editing does not erase assistant assistance. `labeler_id` must separately use
stable, non-identifying reviewer IDs, and `notes` must record owner
adjudication when applicable. These future values do not alter or reinterpret
the pilot's current provenance fields.

## Required Gates Before CSV Addition

1. **Owner allocation review:** Approve all 50 slots, the 25/25 balance, the
   category quotas, and the provenance vocabulary before candidate drafting.
2. **Drafting and guide review:** Draft all candidates outside the tracked CSV
   and check every text, label, eligibility decision, type, rationale, and
   confidence against labeling guide v0.3.
3. **Privacy review:** Independently confirm every candidate satisfies all
   privacy and provenance requirements; remove failures rather than relabeling
   them.
4. **Exact and near-duplicate audit:** Run normalized exact checks, similarity
   suggestions, and manual semantic review against both the pilot and Batch 01.
5. **Scope and standalone-meaning audit:** Confirm each label is decidable from
   the candidate alone, each project-specific keep names its project or
   system, and no policy-only existing-memory decision entered the batch.
6. **Lexical-shortcut audit:** Check permanence/transience vocabulary,
   template repetition, class-specific sentence forms, and important- or
   preference-sounding skips. Rewrite shortcuts rather than balancing keyword
   counts mechanically.
7. **Field and grouping audit:** Validate candidate type, rationale code,
   candidate source, source group, paraphrase group, unique example ID,
   privacy flag, guide version, confidence, eligibility, notes, and unassigned
   split. Confirm all highly similar material is grouped and at most one
   primary-eligible row represents a highly similar paraphrase group.
8. **Owner row approval:** Obtain explicit owner approval of the reviewed row
   set before adding any Batch 01 row to the tracked CSV.
9. **No split assignment:** Leave every Batch 01 split unassigned throughout
   authoring and review. Split assignment occurs only after all planned
   batches, grouping, privacy review, and duplicate review are complete.

Batch 01 is not a final holdout. It must not be treated as untouched test data,
used for final-test decisions, or used to make model-performance claims. No
model fitting, feature selection, threshold selection, or performance
evaluation is part of this authoring specification.

## Allocation Check

| Label | Primary category | Planned slots | Count |
| --- | --- | --- | ---: |
| `keep` | Preferences, communication constraints, or accessibility preferences | B01-01--B01-06 | 6 |
| `keep` | Reusable workflow rules | B01-07--B01-11 | 5 |
| `keep` | Security or project constraints | B01-12--B01-15 | 4 |
| `keep` | Ongoing project facts | B01-16--B01-19 | 4 |
| `keep` | Stable relationships or roles | B01-20--B01-22 | 3 |
| `keep` | Milestones or ongoing commitments | B01-23--B01-25 | 3 |
| `skip` | Temporary plans | B01-26--B01-29 | 4 |
| `skip` | One-time events or incidents | B01-30--B01-33 | 4 |
| `skip` | Casual reactions or acknowledgements | B01-34--B01-37 | 4 |
| `skip` | One-draft or one-task instructions | B01-38--B01-41 | 4 |
| `skip` | Vague or missing-context statements | B01-42--B01-44 | 3 |
| `skip` | Superseded, speculative, or unaccepted information | B01-45--B01-47 | 3 |
| `skip` | Difficult borderline cases | B01-48--B01-50 | 3 |
|  | **Total** | **B01-01--B01-50** | **50** |

Keep quotas total 25; skip quotas total 25. Every planned slot is primary-
eligible by design, and no slot depends on knowledge of Gamma's existing
memory store.

## Readiness

The revised 50-slot semantic inventory is ready for owner approval. Actual
candidate drafting should begin only after that approval and must follow the
authoring, privacy, duplicate-review, grouping, and no-split gates above.
