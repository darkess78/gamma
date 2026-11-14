# Batch 02 Memory-Triage Authoring Specification

Status: **Historical authoring specification — reviewed Batch 02 migrated with explicit owner approval.**

Scope: **Semantic inventory only.**

Planned size: **exactly 50 future primary-eligible examples.**

Planned balance: **exactly 25 `keep` and 25 `skip`.**

This specification records the pre-drafting inventory and its review gates.
The resulting 50 candidate rows were later owner-reviewed and migrated; no
training, evaluation, split assignment, or runtime integration occurred.

## 1. Current Evidence Inspected

Planning used the repository state and these maintained project artifacts:

- `README.md`
- `specs/README.md`
- `specs/current_implementations.md`
- `specs/class-projects/cs4700-memory-triage.md`
- `research/cs4700_memory_triage/README.md`
- `research/cs4700_memory_triage/data/README.md`
- `research/cs4700_memory_triage/data/memory_triage_pilot.csv`
- `research/cs4700_memory_triage/data/memory_triage_primary_dataset.csv`
- `research/cs4700_memory_triage/docs/labeling_guide.md`
- `research/cs4700_memory_triage/docs/dataset_card.md`
- `research/cs4700_memory_triage/docs/pilot_review.md`
- `research/cs4700_memory_triage/docs/blinded_self_relabel_protocol.md`
- `research/cs4700_memory_triage/docs/final_dataset_expansion_plan.md`
- `research/cs4700_memory_triage/docs/batch_01_authoring_spec.md`
- `research/cs4700_memory_triage/docs/batch_01_candidate_drafts.csv`
- `research/cs4700_memory_triage/docs/batch_01_candidate_review.md`
- `research/cs4700_memory_triage/docs/batch_01_migration_record.md`
- `research/cs4700_memory_triage/notebooks/CS4700_Gamma_Memory_Triage.ipynb`

The notebook was inspected as a JSON document only; no cell was run. The
canonical CSV contains 107 rows: 55 `keep` and 52 `skip`. It contains 57
eligible pilot rows and 50 approved Batch 01 rows. All 107 rows are marked
`primary_eligible=true`, all splits are `unassigned`, and none uses the
`requires_existing_memory_context` rationale. Batch 2 planning therefore uses
the actual 107-row canonical dataset, not the earlier checkpoint summary or an
assumed distribution.

## 2. Constraints Carried Forward

A `keep` concept must be understandable by itself and likely to improve help
in a separate future conversation. Suitable concepts include lasting
preferences, stable accessibility needs, reusable rules, durable project
facts or corrections, ongoing commitments, stable roles, and consequential
milestones. A `skip` concept is temporary, vague, casual, current-task-only,
obsolete, unapproved, isolated, or otherwise lacks durable future value.

All 50 slots must remain solvable from candidate text alone. Policy-only
concepts and any decision requiring knowledge of already stored memory are
excluded. Explicit requests to remember or save information cannot substitute
for durability analysis.

Project-specific architecture, tooling, reporting, workflow, compatibility,
or system rules must identify the relevant project or system in later
wording. A non-project-specific `keep` is allowed only for a clearly broad
cross-project preference or broadly safe reusable rule. Named-project presence
is not itself evidence for either label; named projects may appear in both
classes when semantic scope requires them.

The planned balance supports research and does not estimate production
prevalence. Later drafting must protect `keep` recall potential as well as the
high-precision model-selection goal. Future Batch 2 candidate drafting must
not make `skip` labels depend mainly or repeatedly on literal surface words
such as "temporary," "one-time," "currently," "trial," "expired," or
"completed." Those words are not forbidden, but boundedness should instead be
established through varied contextual evidence where appropriate, such as a
finished named deliverable, a cancelled prerequisite, an ended handoff, a
limited authorization, a superseded local exception, or a condition that
never occurred. Likewise, `keep` examples must not depend mainly on permanence
words, obligation phrasing, or the presence of a named project as the label
signal.

## 3. Batch 2 Semantic Coverage Strategy

Batch 2 adds decision boundaries, not paraphrases. The current dataset is
dense in answer-format preferences, approval and safe-change workflows,
dataset privacy/release rules, generic project facts, recurring reviewer
roles, temporary event plans, closed one-time activities, casual reactions,
single-artifact instructions, missing referents, and unapproved or superseded
project proposals. Batch 2 avoids allocating more slots whose main distinction
is conclusion ordering, clarification, test order, destructive-action
approval, reviewer signoff, presentation logistics, document formatting,
acknowledgement, or proposal non-adoption.

Inspection found thinner coverage around stable personal work routines,
language and interaction accessibility beyond visual presentation, adopted
conditional operating rules, durable environment and compatibility facts,
corrections that govern future interpretation, stable operational ownership,
commitments triggered by recurring events, and milestones that change future
maintenance duties. On the `skip` side, thinner boundaries include temporary
workarounds that sound procedural, provisional measurements that sound like
facts, expired permissions, short trials of preference-like behavior, stale
role assignments, conditional hypotheticals without adoption, isolated
resource states, and completed investigations without reusable findings.

The inventory deliberately mixes candidate styles and scopes within each
label. Later wording should vary grammatical form, tense, subject placement,
and source style. Synthetic project names may be introduced only where a
genuinely distinct system context is needed; superficial renaming cannot be
used to manufacture novelty.

## 4. Fifty-Slot Semantic Inventory

The entries below are planning metadata, not candidate sentences. No final
source-group or paraphrase-group identifiers are assigned.

| Slot | Planned label | Semantic family | Decision boundary being tested | Scope requirement | Candidate type or source-style target | Novelty / anti-overlap note | Planned grouping boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B02-01 | `keep` | Stable work routine | Repeated focus-window convention with future scheduling value | May be a clearly broad cross-project preference/rule | Preference; concise profile-like extraction | Unlike pilot weekly-report cadence and Batch 1 session plan; concerns recurring work conditions, not reporting or one session | Exclude calendar-event, break, and deadline variants; one routine concept only |
| B02-02 | `keep` | Stable work routine | Durable interruption-handling preference across technical work | May be a clearly broad cross-project preference/rule | Preference; user-centered behavioral summary | Distinct from B01-01 clarification and B01-10 escalation; concerns interruption timing, not ambiguity or evidence | Keep separate from notification-channel and focus-window slots |
| B02-03 | `keep` | Communication accessibility | Persistent preference for plain-language definitions before specialized terminology | May be a clearly broad cross-project preference/rule | Preference; natural LLM-extracted style | Extends beyond pilot concrete-example and Batch 1 organization preferences; tests terminology access | Do not pair with answer length, heading structure, or example inclusion |
| B02-04 | `keep` | Communication accessibility | Stable need for written confirmation of consequential spoken decisions | May be a clearly broad cross-project preference/rule | Preference; accessibility-oriented summary | Different from caption and visual-encoding rows; concerns decision trace across modalities | Exclude general meeting notes and decision-log workflow paraphrases |
| B02-05 | `keep` | Language preference | Consistent language or translation default for technical material | May be a clearly broad cross-project preference/rule | Preference; profile-like extraction | No current language-choice boundary; avoid metric-unit and tone/format neighbors | One language-use default, without locale, identity, or sensitive inference variants |
| B02-06 | `keep` | Adopted conditional rule | Recurring validation action triggered by a defined risk condition | May be a clearly broad cross-project preference/rule | Boundary; conditional procedural extraction | Unlike focused-before-broad tests and evidence-before-escalation; tests durable conditionality rather than action sequence | Trigger and response must be unique; no generic testing-order restatement |
| B02-07 | `keep` | Adopted conditional rule | Reusable fallback behavior when an external dependency is unavailable | Later wording must name Gamma or another named system/project | Project or boundary; system-scoped operational fact | New availability/fallback boundary; not approval, process separation, or temporary workaround | Separate permanent fallback policy from B02-31 temporary workaround |
| B02-08 | `keep` | Adopted conditional rule | Stable escalation recipient determined by failure severity | Later wording must name Gamma or another named system/project | Project; operational workflow extraction | Distinct from B01 evidence-before-escalation and reviewer roles; concerns adopted routing ownership | Avoid generic contact approval and one-incident escalation history |
| B02-09 | `keep` | Reusable quality rule | Cross-project requirement to preserve failed-case evidence for later analysis | May be a clearly broad cross-project preference/rule | Boundary; rule-style candidate | Different from reproducibility reports, provenance audits, and diagnostic evidence before escalation; concerns failure corpus retention purpose | No dataset release, logging implementation, or private-data handling variants |
| B02-10 | `keep` | Reusable quality rule | Standing distinction between exploratory output and approved conclusions | May be a clearly broad cross-project preference/rule | Boundary; review-oriented extraction | Related to confirmed-fact reporting but tests artifact status and downstream use, not sentence presentation | Keep separate from owner-approval gates and speculative-skip slots |
| B02-11 | `keep` | Environment constraint | Adopted platform or execution-environment limitation affecting future work | Later wording must name Gamma or another named system/project | Project; stable technical fact | Extends beyond Python version and source layout; concerns deployment/runtime environment without networking details | One named system and one environment boundary; exclude version-support paraphrases |
| B02-12 | `keep` | Environment constraint | Stable offline-capability requirement for a named tool | Later wording must name Gamma or another named system/project | Project; requirement-style extraction | New connectivity-capability boundary; not unavailable-dependency fallback or advisory-mode status | Do not duplicate privacy, credentials, or external-contact approval rules |
| B02-13 | `keep` | Data lifecycle constraint | Adopted retention or deletion schedule for a named research artifact class | Later wording must name Gamma or another named system/project | Project; scoped governance fact | Current data rules emphasize admission/privacy and release approval, not approved lifecycle duration | One artifact class and lifecycle policy; no raw-data or secret examples |
| B02-14 | `keep` | Compatibility constraint | Persistent backward-reading requirement for a named system's historical artifacts | Later wording must name Gamma or another named system/project | Project; compatibility fact | Different from API response and performer-event compatibility; concerns historical artifact readability | Exclude schema-consumer compatibility and runtime-version support variants |
| B02-15 | `keep` | Resource constraint | Stable hardware-budget boundary shaping a named project's design | Later wording must name Gamma or another named system/project | Project; stable constraint | New resource-budget boundary; not long-job warning or temporary borrowed equipment | One accepted budget constraint, without machine-local paths or actual private hardware details |
| B02-16 | `keep` | Durable correction | Corrected ownership of a named system component with continuing planning value | Later wording must name Gamma or another named system/project | Project; corrective extraction | Unlike pilot label-design correction and Batch 1 missing-metadata correction; concerns responsibility ownership | Must retain the adopted correction, not only obsolete history; no process-separation paraphrase |
| B02-17 | `keep` | Durable correction | Corrected interpretation of a recurring project metric | Later wording must name Gamma or another named system/project | Project; analytical correction | New metric-meaning boundary; avoids performance result or evaluation execution | One metric definition correction; no numeric result, threshold tuning, or model claim |
| B02-18 | `keep` | Stable project convention | Canonical naming or identifier convention required across a named system | Later wording must name Gamma or another named system/project | Project; stable convention | Different from canonical dataset manifest and source layout; concerns semantic identity, not file authority | Avoid file naming, candidate IDs for this dataset, and superficial syntax variants |
| B02-19 | `keep` | Stable project objective | Adopted, concrete, continuing interoperability objective across independently maintained tools; later wording must establish an ongoing planning consequence through a maintained interface, contract, exchange boundary, or compatibility obligation, rather than a general wish for tools to work together | Later wording must name Gamma or another named system/project and identify the independently maintained tools or ecosystem boundary | Project; adopted-objective extraction with a concrete continuing consequence | Broader than a single technical compatibility rule; distinct from process merging, a one-time integration task, or performer-event compatibility | One adopted ecosystem objective and its continuing planning consequence; do not restate a concrete compatibility rule from another slot |
| B02-20 | `keep` | Stable operational ownership | Recurring responsibility for incident communication in a named system | Later wording must name Gamma or another named system/project | Profile or project; safe role summary | Existing roles cover technical, data, methodology, accessibility, and scope review; this adds operations communication | Use role only, no real identity; exclude escalation-routing rule from B02-08 |
| B02-21 | `keep` | Stable operational ownership | Recurring responsibility for maintaining a shared glossary or taxonomy | Later wording must name Gamma or another named system/project | Profile; safe role fact | New knowledge-maintenance role, distinct from provenance and review authority | No one-time documentation task or named private person variant |
| B02-22 | `keep` | Ongoing commitment | Repeated accessibility check triggered by each public demonstration | Later wording must name Gamma or another named system/project | Project; commitment-style extraction | Unlike accessibility reviewer role and dataset revision audit; combines recurring trigger with accessibility duty | One trigger/duty pair; no reviewer identity or visual-only preference paraphrase |
| B02-23 | `keep` | Ongoing commitment | Durable recurring obligation to reconcile feedback from an identified source or stakeholder category with a specified release artifact, release cycle, or maintained deliverable | Later wording must name Gamma or another named system/project, identify the feedback source or stakeholder category, and identify the affected release artifact, cycle, or maintained deliverable | Project; recurring commitment extraction with a defined feedback-to-deliverable relationship | Different from publishing evaluation reports and provenance audits; must establish recurring feedback reconciliation, not a vague intention to listen | Exclude release approval, issue escalation, and one-time review variants; group only wording with the same recurring source-to-deliverable commitment |
| B02-24 | `keep` | Consequential milestone | Completed handoff that permanently changes maintenance ownership | Later wording must name Gamma or another named system/project | Episodic; durable milestone summary | Unlike baseline completion and access signoff; future value comes from changed ownership | Must encode continuing consequence; no routine completed handoff like B01-33 |
| B02-25 | `keep` | Consequential milestone | Completed deprecation milestone establishing the supported replacement | Later wording must name Gamma or another named system/project | Episodic or project; milestone extraction | Distinct from superseded-plan skips because the supported successor governs future work | Group away from obsolete-history-only rows; one transition with ongoing support consequence |
| B02-26 | `skip` | Temporary workaround | Short-lived manual procedure used while a named system is repaired | Must remain explicitly temporary/context-bound; name system only for standalone clarity | Project; operational episode | New hard negative against durable workflow rules; not an isolated failure or adopted fallback | Keep separate from B02-07 permanent fallback and B02-31 local setup workaround |
| B02-27 | `skip` | Temporary workaround | Bounded data-conversion step for one incompatible delivery | Must remain explicitly temporary/context-bound | Boundary or episodic; task extraction | Different from completed formatting and submission-channel changes; procedure sounds reusable but is delivery-specific | No standing compatibility rule or recurring export convention |
| B02-28 | `skip` | Preference trial | Limited experiment with an interaction style before any lasting preference is chosen | Must remain explicitly temporary/context-bound | Preference; tentative user-summary style | New boundary between preference-shaped language and an unadopted personal default | Do not use generic proposal language or one-reply tone instruction |
| B02-29 | `skip` | Preference trial | Artifact-bounded trial of an accessibility presentation option | Must remain explicitly temporary/context-bound | Preference; review-trial extraction | Hard negative near durable accessibility preferences, but not color, headings, or a medical inference | One trial and one artifact class; no lasting need or broad default |
| B02-30 | `skip` | Expiring permission | Narrow authorization limited to one completed operation | Must remain explicitly temporary/context-bound | Boundary; permission-summary style | Existing data has approval requirements but little expired authorization; tests permission versus standing boundary | No external-contact or destructive-action paraphrase; one bounded authorization only |
| B02-31 | `skip` | Isolated environment state | Local setup choice caused by a short-lived resource limitation | Must remain explicitly temporary/context-bound | Project or episodic; technical state | Different from borrowed monitor and room arrangement; contrasts with stable environment constraints | Exclude actual machine-local details and temporary procedure overlap with B02-26 |
| B02-32 | `skip` | Isolated environment state | One run's reduced capacity without a continuing system limit | Must remain explicitly temporary/context-bound | Episodic; generated diagnostic summary | New boundary between observed run state and stable resource constraint | No performance metric conclusion, hardware identity, or recurring limitation |
| B02-33 | `skip` | Provisional measurement | Single observed metric before replication or validation | Must remain explicitly temporary/context-bound | Episodic or project; analytical extraction | Unlike casual reaction to a latency spike and unreviewed causal assessment; focuses on evidentiary status | One observation only; no adopted threshold or durable metric definition |
| B02-34 | `skip` | Provisional measurement | Preliminary comparison whose conditions differ across alternatives | Must remain explicitly temporary/context-bound | Project; analytical summary | New invalid-comparison boundary, not missing selection criterion or model evaluation | Avoid candidate model claims, numeric results, and training/evaluation activity descriptions |
| B02-35 | `skip` | Conditional hypothetical | Detailed system behavior contingent on a feature that was never adopted | Must remain explicitly temporary/context-bound; name system for standalone clarity | Project; hypothetical design extraction | Extends pilot speculative database idea but tests conditional specificity without proposal shortcut words | One unrealized antecedent; no generic feature proposal or adopted fallback |
| B02-36 | `skip` | Conditional hypothetical | Planned role or obligation contingent on a cancelled collaboration | Must remain explicitly temporary/context-bound | Profile or project; hypothetical relationship summary | New stable-role lookalike whose enabling relationship never became active | No real identities; distinguish from stale former role and unapproved workflow |
| B02-37 | `skip` | Stale role fact | Former recurring responsibility explicitly ended after a handoff | Must remain explicitly temporary/context-bound; name project if role was project-specific | Profile; outdated role summary | Existing outdated profile is a display label; this tests formerly durable operational responsibility | Do not preserve a still-relevant successor fact as the candidate's main purpose |
| B02-38 | `skip` | Stale constraint | Former system limitation removed by an adopted redesign | Must remain explicitly temporary/context-bound; name system for standalone clarity | Project; outdated technical fact | Different from superseded label design and submission plan; tests obsolete limitation, not replacement policy | One historical constraint; exclude durable correction wording that would justify `keep` |
| B02-39 | `skip` | Closed investigation | Completed defect investigation with no reusable cause or follow-up | Must remain explicitly temporary/context-bound | Episodic; technical summary | Different from resolved tool failure and helpful trace reaction; effort does not create future value | No retained workaround, recurring defect, or durable finding |
| B02-40 | `skip` | Closed investigation | Completed information search that produced no adopted decision | Must remain explicitly temporary/context-bound | Episodic; research-task summary | New closed inquiry boundary, distinct from preliminary assessment and unadopted extension | No reusable bibliography, evidence rule, or future commitment |
| B02-41 | `skip` | One-audience adaptation | Content-depth adjustment for a single known audience | Must remain explicitly temporary/context-bound | Boundary; artifact-generation instruction | Different from one-answer depth and document appendix choices; audience drives the bounded adaptation | No broad communication or accessibility preference; one audience/event only |
| B02-42 | `skip` | One-audience adaptation | Temporary terminology substitution for one external review | Must remain explicitly temporary/context-bound | Boundary or project; review instruction | Contrasts with stable glossary ownership and language preference; not general plain-language policy | One review artifact; no durable naming convention or translation default |
| B02-43 | `skip` | Local exception | One approved bypass that does not amend the standing rule | Must remain explicitly temporary/context-bound; name project/system if needed | Boundary; exception record | New hard negative: approval and formal tone without durable policy change | Do not duplicate fixed-space deliverable exception; vary domain and consequence |
| B02-44 | `skip` | Local exception | Temporary relaxation of a quality threshold for a disposable prototype | Must remain explicitly temporary/context-bound; name project for standalone clarity | Project; prototype decision | Distinct from advisory status and model-selection thresholds; bounded exception lacks future authority | No numeric metric, actual model work, or permanent quality rule |
| B02-45 | `skip` | Assistant inference | Unsupported inference about a user's stable workflow from one action | Must remain explicitly temporary/context-bound | Profile or preference; extraction-error style | Dataset has assistant reaction but not assistant-authored overgeneralization as the semantic flaw | One observed action and one unsupported generalization; no sensitive trait inference |
| B02-46 | `skip` | Assistant inference | Unsupported project ownership inference from one contribution | Must remain explicitly temporary/context-bound; name project for clarity | Profile; extraction-error style | New role-inference boundary, distinct from a completed handoff or former role | No real identity and no existing-memory dependency; evidence insufficiency must be textual |
| B02-47 | `skip` | Closed preparation task | Fully specified, one-time preparation task tied to a named completed milestone or bounded event; it creates no reusable rule, durable artifact, follow-up obligation, changed ownership, standing schedule, or maintained system condition | Later wording must identify the completed milestone or bounded event and remain complete and understandable on its own; closure must follow from the event structure, not mainly from obvious time-status terms | Boundary or episodic; self-contained preparation-task summary | A primary-eligible hard negative because the task is closed and non-reusable; distinct from closed investigations, mixed-value episodes, completed recovery, and one-audience adaptation | Group by the same preparation purpose and bounded milestone/event; exclude variants that produce a reusable artifact, continuing duty, ownership change, or maintained condition |
| B02-48 | `skip` | Ambiguous durability | A single observed project-scoped behavior with textual evidence, supplied by the event structure or decision context, that it occurred once and was not adopted as a standing practice, rule, or convention | Later wording must name the project for standalone clarity and establish both the one occurrence and lack of adoption without relying mainly on obvious temporary keywords | Project; self-contained observation summary with non-adoption evidence | Different from unapproved proposals and conditional hypotheticals because the behavior occurred; distinct from stale facts and unsupported assistant inferences because the text itself establishes one occurrence and non-adoption | Group only observations of the same bounded event and behavior; exclude proposal, hypothetical, obsolete-state, and inference variants, and any wording that leaves adoption genuinely unresolved |
| B02-49 | `skip` | Mixed-value episode | Important meeting outcome limited to scheduling, with no continuing decision | Must remain explicitly temporary/context-bound | Episodic; formal summary | Tests importance without durable substance, unlike routine worksheet or impressive demonstration | No adopted policy, role change, milestone, or recurring schedule |
| B02-50 | `skip` | Mixed-value episode | Completed recovery effort that restored prior state without changing future operation | Must remain explicitly temporary/context-bound; name system if needed | Episodic; incident summary | Distinct from closed investigation and isolated failure; recovery magnitude is not a milestone | No durable workaround, corrected architecture, or continuing maintenance consequence |

## 5. Coverage And Balance Audit

| Planned label | Semantic-family allocation | Count |
| --- | --- | ---: |
| `keep` | Stable work routine | 2 |
| `keep` | Communication accessibility and language preference | 3 |
| `keep` | Adopted conditional and reusable quality rules | 5 |
| `keep` | Environment, data-lifecycle, compatibility, and resource constraints | 5 |
| `keep` | Durable corrections and stable project conventions/objectives | 4 |
| `keep` | Operational ownership | 2 |
| `keep` | Ongoing commitments and consequential milestones | 4 |
| **`keep` total** |  | **25** |
| `skip` | Temporary workarounds, preference trials, and expiring permission | 5 |
| `skip` | Isolated environment states and provisional measurements | 4 |
| `skip` | Conditional hypotheticals and stale facts | 4 |
| `skip` | Closed investigations and one-audience adaptations | 4 |
| `skip` | Local exceptions and assistant inferences | 4 |
| `skip` | Closed preparation task, ambiguous durability, and mixed-value episodes | 4 |
| **`skip` total** |  | **25** |

The 25/25 allocation spreads both labels across preference, boundary, project,
profile, and episodic styles. Durable concepts need not use permanence words,
and several `skip` families intentionally use formal, approved, important, or
project-scoped framing. Conversely, some `keep` slots are conditional or
event-triggered rather than absolute. This reduces dependence on tense,
project naming, modal verbs, obvious temporary vocabulary, and sentence form.

The anti-overlap and grouping columns separate these slots from dense pilot
and Batch 1 areas and from one another. Later review must compare actual wording
against the then-current canonical rows; semantic novelty here does not
guarantee lexical or paraphrase novelty after drafting.

All 50 slots are intended to be primary eligible. None is policy-only,
depends on the state of stored memory, or represents a production memory-policy
decision. This is a balanced research authoring plan, not a claim about
production prevalence or authority to filter Gamma memories.

## 6. Required Owner Review Gates

1. No candidate wording may be drafted until the owner reviews and explicitly
   approves this inventory, including any weak or overlapping slots.
2. Later drafting must occur in a separate review-only CSV or equivalent
   review artifact. This specification must not be converted directly into
   canonical dataset rows.
3. Later candidates must be re-audited against the then-current canonical
   dataset for scope, privacy, exact duplicates, semantic overlap, lexical
   shortcuts, and grouping before migration is considered.
4. No Batch 2 row may be migrated into the canonical CSV without explicit
   owner approval of the reviewed candidate rows.
5. Model training and evaluation remain blocked until the final dataset target,
   cleaning, source/paraphrase grouping review, and untouched group-aware
   holdout steps are completed. Split assignment remains blocked during
   authoring and review.
6. Runtime integration remains blocked and requires a separate, explicitly
   approved task after the research and review prerequisites are satisfied.
