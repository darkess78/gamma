# Batch 03 Memory-Triage Authoring Specification

Status: **Historical planning specification — owner reviewed, approved, and migrated.**

Scope: **Historical semantic inventory and completed migration provenance.**

Migrated size: **exactly 50 primary-eligible examples.**

Migrated balance: **exactly 25 `keep` and 25 `skip`.**

This document began as a pre-drafting plan based on direct inspection of the
157-row canonical dataset. The owner later reviewed the inventory and all 50
draft rows, approved them, and authorized their migration. No training,
evaluation, split assignment, notebook execution, or runtime integration
occurred.

## 1. Current Evidence Inspected

Planning used the current repository state and these project artifacts:

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
- `research/cs4700_memory_triage/docs/batch_02_authoring_spec.md`
- `research/cs4700_memory_triage/docs/batch_02_candidate_drafts.csv`
- `research/cs4700_memory_triage/docs/batch_02_candidate_review.md`
- `research/cs4700_memory_triage/docs/batch_02_migration_record.md`
- `research/cs4700_memory_triage/notebooks/CS4700_Gamma_Memory_Triage.ipynb`

The notebook was inspected as JSON only; no cell was run. The canonical
`primary_v0_2` CSV contains 157 rows: 80 `keep` and 77 `skip`. Its composition
is 57 primary-eligible pilot rows (30/27), 50 Batch 01 rows (25/25), and 50
Batch 02 rows (25/25). The 107 pilot and Batch 01 rows retain
`dataset_release=primary_v0_1`; the 50 Batch 02 rows record
`dataset_release=primary_v0_2`. All 157 rows have `primary_eligible=true` and
`split=unassigned`.

Every retained canonical row currently has a unique `source_group_id` and a
unique `paraphrase_group_id`. In the 60-row frozen pilot source, `pg_0004` and
`pg_0007` are the only multi-row paraphrase groups; each has one canonical
primary representative and related policy-only rows excluded from the
canonical dataset. Batch 01 and Batch 02 assign one source group and one
paraphrase group per independently reviewed row. Batch 03 planning follows
that convention without assigning final identifiers. It is based on direct
inspection of the current 157-row canonical dataset.

## 2. Constraints Carried Forward

A `keep` concept must be understandable by itself and likely to improve help
in a separate future conversation. Suitable concepts include lasting
preferences, safe reusable workflow rules, stable project facts or
corrections, continuing commitments, stable relationships or roles, durable
boundaries, and consequential milestones. A `skip` concept is temporary,
casual, vague, current-task-only, unapproved, obsolete, isolated,
artifact-bound, speculative, or otherwise lacks durable future value.

All slots must be solvable from later candidate text alone. Policy-only cases
and cases whose label requires knowing whether equivalent information is
already stored are excluded. An explicit request to remember or save
information does not establish durability.

Project-specific architecture, tooling, reporting, workflow, compatibility,
or system rules must name the relevant project or system in later wording.
Non-project-specific `keep` wording is permitted only for a genuinely broad
cross-project preference or safe reusable rule. Naming a project is not label
evidence: both labels include meaningful named-system concepts.

Later drafting must not make `skip` predictable mainly from words such as
“temporary,” “one-time,” “currently,” “trial,” “expired,” “completed,”
“former,” or “cancelled.” It must not make `keep` predictable mainly from
“permanent,” “always,” “ongoing,” “must,” “required,” or a project name.
Durability and boundedness should follow from the full scenario. Semantic
plans also cannot become a shortcut for repetitive, template-driven text;
future drafts must vary structure and evidence while preserving each slot's
decision boundary.

The planned balance supports research and model development. It does not
represent production-memory prevalence. No model integration is authorized;
any future classifier remains advisory-only, disabled by default, and outside
this task.

## 3. Current Coverage And Gap Analysis

### High-density families to avoid repeating

Across the pilot and two migrated batches, coverage is already dense in:

- answer organization, explanation detail, comparison format, and visual
  accessibility preferences;
- clarification, approval, review order, safe-change, evidence collection,
  testing, and decision-recording workflows;
- dataset privacy, release, provenance, retention, and compatibility rules;
- named-project architecture, environment, fallback, resource, ownership,
  glossary, and identifier conventions;
- recurring reviewer or coordinator roles, recurring release duties, adopted
  corrections, handoffs, and deprecation milestones;
- brief personal states, event plans, routine closed tasks, artifact reactions,
  one-answer or one-document instructions, missing referents, superseded facts,
  unapproved proposals, provisional measurements, short workarounds, local
  exceptions, and unsupported assistant generalizations.

Batch 3 must not create apparent novelty by renaming synthetic projects,
changing artifacts, or restating those same boundaries.

### Confirmed gaps

Direct row inspection found no primary examples centered on several useful
text-contained boundaries: stable error-correction and recommendation-specific
calibrated-uncertainty preferences; broad source-attribution and user-control
conventions; named
system observability, migration reversibility, time-zone interpretation, and
localization contracts; stable non-identity collaboration boundaries; durable
project assumptions whose operational consequence is explicit; recurring
maintenance triggers tied to lifecycle events; and milestones that establish
support, governance, or recovery obligations without repeating prior handoff
or deprecation cases.

The skip side is thinner for preference-like behavior caused by one artifact's
constraints; formal but non-authoritative drafts; technically successful
experiments that establish no adopted baseline; recurring-looking schedules
with a known terminal program; inherited metadata with no verified owner;
closed transaction choices, bounded exercise pairings, consumed queue
priorities, completed random assignments, and authorizations fully exhausted
by a named bounded use. These are hard negatives
because they may sound useful while remaining text-solvable skips.

### Tentative opportunities

Some thinner areas need special owner scrutiny rather than being treated as
confirmed gaps. Execution-context preferences can overlap existing workflow
rules; collaboration expectations can drift into generic advice; governance
milestones can duplicate approval gates; and bounded operational decisions can
become durable rules if later wording implies reuse beyond their stated scope.
Slots in these areas should be replaced, not forced, if drafting cannot make
the boundary distinct and objective. No opportunity justifies model work,
production integration, Gamma runtime changes, or synthetic-project renaming
as a substitute for semantic novelty.

## 4. Batch 3 Semantic Coverage Strategy

Batch 3 adds decisions not already exercised by the canonical rows. Each slot
defines one classification boundary, scope rule, source-style target, and
grouping boundary. Later drafting must compare proposed wording with the full
then-current canonical dataset and remove or replace a slot that collapses
into a pilot, Batch 01, or Batch 02 paraphrase.

Both labels mix preference, boundary, project, profile, and episodic styles.
The keep side combines user-centered defaults with scoped system facts,
constraints, roles, commitments, corrections, and consequential events. The
skip side combines bounded preferences, non-authoritative records, closed
episodes, exhausted permissions, misleading metadata, and bounded outcomes
that establish no reusable rule.
Neither class should be dominated by one semantic family.

Future wording must vary tense, syntax, subject placement, evidence form, and
project-name use. Approval, project naming, formal tone, technical detail,
event completion, and future-facing language must appear where semantically
appropriate on both sides rather than act as label markers. Meaningful
ambiguity is acceptable when the text still supplies enough evidence for a
guide-consistent answer. Ambiguity created by simply withholding decisive
information is not acceptable.

Each slot permits one concept only. Template clusters that differ only by
project name, artifact name, role name, date, or small wording changes are
prohibited. Slots must not be drafted as minimal paraphrase pairs unless a
later grouping review explicitly replaces or consolidates them.

## 5. Fifty-Slot Semantic Inventory

The entries below are planning metadata, not candidate wording. No candidate
text, final source-group identifier, or final paraphrase-group identifier is
assigned.

| Slot | Planned label | Semantic family | Decision boundary being tested | Scope requirement | Candidate type or source-style target | Novelty / anti-overlap note | Planned grouping boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B03-01 | `keep` | Error-correction preference | Stable preference for corrections to preserve the valid portion of prior work while isolating the defect | May be broad cross-project | Preference; profile-like extraction | Distinct from B01-04 correction tone and B01-09 small-change workflow; concerns preservation during correction | One correction-preservation concept; exclude tone, rollback, and unrelated-worktree variants |
| B03-02 | `keep` | Recommendation-confidence preference | Reusable preference for recommendations or decision support to state calibrated confidence and the limits of the available evidence | May be broad cross-project | Preference; natural interaction summary | Distinct from pilot fact-versus-assumption report formatting, B02 exploratory-versus-approved artifact status, and speculative project claims; the retained behavior is confidence calibration at a decision point | One recommendation-confidence concept; exclude report layout, fact/assumption separation, artifact authority, and project-speculation variants |
| B03-03 | `keep` | Source-use preference | Stable expectation that externally sourced technical claims remain traceable to their origin | May be broad cross-project | Preference or boundary; research-assistance style | No current user-centered source-attribution boundary; not dataset provenance or failed-case evidence | One attribution behavior; exclude citation formatting and release-manifest concepts |
| B03-04 | `keep` | User-control preference | Reusable preference to receive a reversible preview before broad automated edits | May be broad cross-project | Preference; action-control summary | Distinct from approval before irreversible action and smallest-safe-change rules; preview, not permission, is central | Exclude destructive actions, publication approval, and explanation-before-change paraphrases |
| B03-05 | `keep` | Execution-context preference | Stable cross-project preference for execution instructions to expose their prerequisites, command context, and expected operational outcome before they are run | May be broad cross-project | Preference; technical interaction style | Concerns transparent execution context, not a preview of the resulting edited state; distinct from the repository virtual-environment rule, consequence-first planning, evidence-before-escalation, and smallest-safe-change workflow | One execution-context concept; exclude proposed-state previews, shell-specific syntax, environment mandates, escalation evidence, and change-sizing variants |
| B03-06 | `keep` | Reusable handoff rule | Standing cross-project rule that handoffs include unresolved risks and ownership of follow-up | May be broad cross-project | Boundary; workflow extraction | Existing handoff milestone changes ownership, but no broad handoff-content convention | No reviewer-signoff, decision-log, or incident-update variants |
| B03-07 | `keep` | Rollback-decision rule | Standing convention to define observable decision triggers for when a shared rollout must be rolled back | May be broad cross-project; later wording must identify a shared rollout and its decision criteria | Boundary; conditional rollout workflow | Distinct from B02-06 recovery-path validation: it governs the decision to reverse a rollout, not whether backup or recovery mechanisms work | One rollout-trigger set; exclude backup validation, recovery verification, temporary workarounds, and destructive-action approval |
| B03-08 | `keep` | Reusable accessibility rule | Cross-project convention to expose equivalent information when interaction depends on timing | May be broad cross-project | Boundary; accessibility-oriented rule | Adds timing access beyond captions, color, terminology, and voice-decision confirmation | One timing-equivalence concept; exclude public-demonstration checks and interface navigation |
| B03-09 | `keep` | Reusable review rule | Stable separation of factual defect review from optional style suggestions | May be broad cross-project | Boundary; review workflow | Unlike correction-feedback preference and confirmed-versus-assumed reports; governs review categorization | Exclude recommendation ordering, artifact status, and approval-stage concepts |
| B03-10 | `keep` | Data interpretation rule | Reusable convention to record units and reference frames with measurements | May be broad cross-project | Boundary; analytical workflow | Distinct from metric-unit preference and B02 metric-definition correction; concerns interpretability across domains | No numeric threshold, benchmark, or report-format variants |
| B03-11 | `keep` | Observability contract | Adopted named-system rule that operational alerts identify user impact as well as component state | Later wording must name the relevant system/project | Project; stable operational constraint | No canonical row covers alert semantics; unlike incident-update ownership and failure-evidence retention | One alert contract; exclude routing recipient and logging implementation |
| B03-12 | `keep` | Migration reversibility | Adopted named-system constraint preserving a verified return path during staged data migration | Later wording must name the relevant system/project | Project; architecture/workflow fact | New migration boundary; not archive-format compatibility or generic rollback planning | Keep separate from backup validation and short conversion workarounds |
| B03-13 | `keep` | Time interpretation contract | Stable named-system convention for storing or interpreting time across regions | Later wording must name the relevant system/project | Project; compatibility fact | Time-zone semantics are absent; avoid simple environment or recurring-schedule restatement | One time interpretation rule; exclude dates, event schedules, and locale identity |
| B03-14 | `keep` | Localization contract | Adopted named-system boundary separating canonical content from localized presentation | Later wording must name the relevant system/project | Project; architecture/content fact | Distinct from B02 language preference and terminology stewardship; system data ownership is central | No translation-style preference or glossary-maintenance variants |
| B03-15 | `keep` | Interface degradation rule | Stable named-system behavior that preserves core access when optional rich interaction is unavailable | Later wording must name the relevant system/project | Project; accessibility/compatibility constraint | Unlike offline operation and external-dependency fallback; tests optional-interface degradation | Exclude network loss, map fallback, and presentation-only accessibility checks |
| B03-16 | `keep` | Durable project assumption | Verified operating assumption that constrains a named project's design and includes its continuing consequence | Later wording must name the relevant system/project | Project; stable fact | Adds consequence-bearing assumptions without repeating device limits or platform support | One verified assumption; exclude unapproved hypotheses and resource-capacity facts |
| B03-17 | `keep` | Durable correction | Corrected interpretation of a named project's success criterion that changes later review decisions | Later wording must name the relevant system/project | Project; correction-style fact | Distinct from label-design and coverage-metric corrections; focuses on success-criterion meaning | Exclude preliminary measurements and model metrics; one corrected criterion only |
| B03-18 | `keep` | Stable governance fact | Named project's established tie-breaking authority for unresolved non-labeling decisions | Later wording must name the relevant project | Project or profile; governance extraction | Different from final scope authority, reviewer roles, and annotation adjudication | No real identity; exclude routine approval chains and label-review processes |
| B03-19 | `keep` | Stable collaboration boundary | Durable role separation between content ownership and technical implementation in a named project | Later wording must name the relevant project | Profile or project; role summary | New cross-role boundary; not maintenance ownership or reviewer responsibility | Group only the same role division; exclude one handoff and inferred ownership |
| B03-20 | `keep` | Stable support role | Recurring role responsible for translating verified user reports into maintainable project issues | Later wording must name the relevant project/system | Profile; safe role-based summary | Adds intake responsibility beyond incident communication, glossary, and review roles | No identities; exclude one report, escalation routing, and documentation ownership |
| B03-21 | `keep` | Recurring lifecycle commitment | Maintained compatibility review triggered by dependency lifecycle changes | Later wording must name the relevant project/system | Project; commitment extraction | Different from every-release guide reconciliation and schema compatibility; trigger is external lifecycle change | One trigger and one duty; exclude version-support facts and one upgrade task |
| B03-22 | `keep` | Recurring accessibility commitment | Maintained review duty triggered when a named system adds a new interaction mode | Later wording must name the relevant system/project | Project; commitment extraction | Extends accessibility beyond each-demonstration checks; lifecycle trigger is feature-class change | Exclude generic UI checks, captions-only review, and one release review |
| B03-23 | `keep` | Consequential recovery milestone | Completed recovery exercise that established a maintained recovery responsibility or verified operating boundary | Later wording must name the relevant system/project | Episodic; milestone summary | Contrasts with B02-50 closed restoration by requiring a continuing consequence; not baseline evaluation | Must encode the durable consequence; exclude incident magnitude and routine test completion |
| B03-24 | `keep` | Consequential governance milestone | Formal project transition that established a continuing review body or decision process | Later wording must name the relevant project | Episodic; milestone extraction | Unlike access approval and maintenance handoff; governance structure is the lasting result | Exclude one meeting, signoff-only approval, and role inference |
| B03-25 | `keep` | Consequential support milestone | Formal compatibility certification or equivalent milestone that establishes a concrete continuing support status or exchange consequence for a named system | Later wording must name the relevant system/project and the continuing support or exchange consequence | Episodic or project; milestone fact | Distinct from B02 compatibility obligations, deprecation milestones, evaluation baselines, and B03-46 isolated compliance evidence; one successful test, demonstration, or isolated check is insufficient | One authoritative certification and its continuing consequence; exclude isolated successes, routine checks, and marketing-style claims |
| B03-26 | `skip` | Artifact-induced preference | Preference-like choice caused solely by one artifact's audience or physical limits | Must remain explicitly bounded/context-specific | Preference; artifact review summary | Hard negative beyond color headings and brochure presentation; constraint, not user default, determines choice | One artifact constraint; exclude durable accessibility and one-answer formatting variants |
| B03-27 | `skip` | Borrowed workflow | Procedure followed for one collaboration because another group's process controlled the work | Must remain explicitly bounded/context-specific | Boundary or project; collaboration episode | New lookalike for reusable workflow rules; borrowed procedure has no adoption evidence | Exclude local bypasses, handoffs, and short repair workarounds |
| B03-28 | `skip` | Non-authoritative draft | Formal operating rule present only in a draft that was never granted authority | Must name the relevant project/system for standalone clarity and remain bounded | Project; document-status extraction | Related to unapproved proposals but tests polished rule text and document authority, not proposal possibility | One unratified rule; exclude superseded adopted rules and exploratory findings |
| B03-29 | `skip` | Demonstration-only configuration | Technically valid system configuration assembled solely to illustrate a capability | Must remain explicitly bounded/context-specific; name system if needed | Project or episodic; technical summary | Different from disposable visual assets and reduced-capacity setup; configuration sounds reusable but is not retained | Exclude benchmarks, temporary repair, and stable environment constraints |
| B03-30 | `skip` | Exhausted use authorization | Authorization was fully consumed by a completed, named, bounded information-use activity or event and leaves no future information-use decision to govern | Must identify the completed bounded use and establish exhaustion of the authorization without making privacy policy or retention nuance the classification issue | Boundary; authorization-summary style | Hard negative because narrow permission alone can be durable; the decisive fact is that the authorized use is closed and no standing data-use constraint remains | One exhausted authorization and its named use; exclude external-service approval, destructive-action approval, standing data-use constraints, and lifecycle-policy interpretation |
| B03-31 | `skip` | Terminal recurring schedule | Repeated activity belongs to a finite program whose endpoint is textually established | Must remain explicitly bounded/context-specific | Episodic or project; schedule summary | Hard negative against recurring commitments; repetition alone does not establish durable value | One finite program; exclude one event and genuinely maintained cadence |
| B03-32 | `skip` | Provisional taxonomy | Category scheme used to organize one review before terminology is adopted | Must remain explicitly bounded/context-specific | Project; analytical workflow summary | Contrasts with stable glossary stewardship and identifier conventions; classification utility is local | Exclude model labels, durable naming rules, and sponsor-vocabulary substitution |
| B03-33 | `skip` | Successful experiment without adoption | Trial produced a technically successful result but no baseline, supported path, or follow-up decision | Must remain explicitly bounded/context-specific | Episodic; experiment summary | Existing skips emphasize failed evidence or invalid comparisons; success is the hard-negative cue here | One result with explicit lack of durable consequence; no model training/evaluation details |
| B03-34 | `skip` | Rehearsal decision | Operational choice repeated during rehearsals but not selected for actual delivery | Must remain explicitly bounded/context-specific | Episodic or project; preparation summary | Adds repeated behavior without adoption; distinct from presentation preparation and one observed behavior | Group by the same rehearsal purpose; exclude stable presentation preference |
| B03-35 | `skip` | Unverified inherited metadata | Ownership or status claim copied from an inherited artifact with text-contained lack of verification | Must name the relevant project/system for standalone clarity | Project or profile; extraction-error style | Different from assistant inference and stale role fact; uncertainty comes from provenance, not one action | One metadata claim; exclude existing-memory context and real identities |
| B03-36 | `skip` | Conditional fact with absent prerequisite | Detailed implementation fact applies only under an option the text establishes is not part of the adopted system | Must name the relevant system/project and remain bounded | Project; conditional technical summary | Related to B02-35 but concerns a fact imported from an inapplicable variant, not a hypothetical design plan | One absent prerequisite; exclude rejected feature proposals and superseded history |
| B03-37 | `skip` | Closed transaction choice | A choice settled one named, fulfilled transaction but establishes neither a reusable user preference nor an adopted project rule | Must identify the bounded transaction and its fulfillment; the selected detail must have no continuing constraint | Episodic or boundary; transaction-outcome summary | Materially different from terminology, imports, normalization, translation, and interoperability wording; tests whether a concrete choice is overgeneralized beyond the transaction it resolved | One transaction and one consumed choice; exclude local exceptions, temporary workarounds, audience adaptations, role handoffs, proposals, and investigations |
| B03-38 | `skip` | Bounded exercise pairing | Role-neutral participants were paired for one completed structured exercise, with no continuing relationship, responsibility, or collaboration rule | Must identify the bounded exercise and use only safe generic roles; closure follows from completion of that exercise | Episodic or profile-like; participation summary | Adds a relationship-looking hard negative without retention, consent, privacy, lifecycle, or policy interpretation; the pairing never becomes a durable role fact | One exercise pairing; exclude imports, local exceptions, workarounds, artifact adaptations, investigations, rejected rationales, handoffs, and real identities |
| B03-39 | `skip` | Consumed queue priority | A priority decision governed the processing order of one bounded queue and was fully consumed when that queue was handled, without establishing a standing prioritization rule | Must identify the bounded queue and establish that the ordering decision has no effect on later work; do not rely mainly on temporal keywords | Boundary or episodic; operations-decision summary | Materially different from diagnostics, incidents, anomalies, defects, recovery, and technical observations; it tests a decision that was applied but has no durable scope | One queue and one exhausted priority decision; exclude temporary plans, drafts, measurements, audience adaptations, inferences, obsolete facts, and recurring scheduling rules |
| B03-40 | `skip` | One-release coordination rule | Coordination pattern mandated by one release's staffing arrangement | Must remain explicitly bounded/context-specific; name project for clarity | Boundary or project; release summary | Different from stable roles, handoff content, and one-audience adaptation; staffing scope limits it | One release and one coordination pattern; exclude lasting ownership change |
| B03-41 | `skip` | Local interpretation aid | Explanatory analogy adopted for one audience artifact but not as project terminology | Must remain explicitly bounded/context-specific | Boundary; communication artifact summary | Unlike plain-language definition preference and glossary ownership; analogy is intentionally local | Exclude audience depth adjustment and translation defaults |
| B03-42 | `skip` | Completed random assignment | A random assignment determined order or grouping for one completed bounded activity but establishes no preference, authority, schedule, or reusable allocation rule | Must identify the bounded activity and make the assignment's non-authoritative random basis textually clear without relying mainly on “temporary” or “cancelled” | Episodic; activity-allocation summary | Materially different from hypotheticals, absent prerequisites, contingencies, rejected variants, and inapplicable configurations; the hard-negative reason is non-generalizable random allocation | One assignment event; exclude drafts, maintenance offers, archived rationales, access arrangements, terminal schedules, and stable role or workflow implications |
| B03-43 | `skip` | Closed bounded access arrangement | Access method applied only within a completed venue-, participant-group-, or event-specific setting and ended with that setting | Must establish the closed setting without identities or sensitive attributes and must not imply a continuing accessibility need, recurring accommodation, or standing system access rule | Episodic or boundary; access episode | The bounded setting, not a user's lasting need, controls the label; privacy-safe roles or groups only | One closed setting and access method; exclude durable accessibility needs, captions or keyboard defaults, recurring accommodations, venue plans, and system access policy |
| B03-44 | `skip` | Unselected maintenance offer | A role volunteered to maintain an artifact but no authority or assignment followed | Must remain explicitly bounded/context-specific; name project if needed | Profile or project; proposed-role summary | Different from cancelled collaboration and inferred ownership; offer exists but role never forms | No identities; exclude active support roles and completed handoffs |
| B03-45 | `skip` | Superseded interpretation aid | Prior explanatory model was useful during exploration but is explicitly invalid for current decisions | Must name the relevant project/system for standalone clarity | Project; outdated analytical summary | Different from obsolete architecture facts and corrected criteria; candidate centers only the unusable old aid | Exclude wording that states the durable replacement or correction as the main information |
| B03-46 | `skip` | Isolated compliance evidence | One artifact passed a required check but the result creates no reusable certification or changed obligation | Must remain explicitly bounded/context-specific; name project if needed | Episodic; formal result summary | Hard negative against B03-25 certification and pilot baseline milestone; formal success lacks durable consequence | One check result; exclude recurring audit duties and maintained evidence |
| B03-47 | `skip` | Closed ownership coverage | Temporary responsibility covered a defined absence and ended when the standing owner returned | Must remain explicitly bounded/context-specific; name project/system if relevant | Profile; role episode | Distinct from stale reassignment after handoff and planned roles; original ownership never changed | No identities; exclude continuing backup-role commitments |
| B03-48 | `skip` | One-import normalization | Data naming or unit normalization applied only to one external delivery and not retained as a convention | Must remain explicitly bounded/context-specific | Project or episodic; import summary | Contrasts with measurement interpretation, canonical identifiers, and one conversion task by testing convention-like output | One delivery; exclude standing compatibility and data-lifecycle rules |
| B03-49 | `skip` | Archived option rationale | Reasoning for an option that was not selected and has no continuing constraint on the adopted design | Must name the relevant project/system for standalone clarity | Project; decision-history summary | Unlike unapproved proposal and superseded limitation; rationale may be sound but has no active consequence | One rejected option; exclude durable decision records that explain the adopted choice |
| B03-50 | `skip` | Closed anomaly reconciliation | One discrepancy was reconciled as an isolated artifact issue without changing definitions, procedures, or ownership | Must remain explicitly bounded/context-specific | Episodic; analytical incident summary | Distinct from corrected metric meaning, closed investigation, and recovery; reconciliation yields no reusable change | One anomaly and closure basis; exclude recurring quality issue or durable correction |

## 6. Coverage And Balance Audit

| Planned label | Semantic-family distribution | Count |
| --- | --- | ---: |
| `keep` | User preferences: correction, uncertainty, source use, control, and tool interaction | 5 |
| `keep` | Reusable workflow, accessibility, review, and interpretation rules | 5 |
| `keep` | Named-system operational, migration, time, localization, and degradation contracts | 5 |
| `keep` | Durable project assumptions, corrections, governance, and role boundaries | 5 |
| `keep` | Lifecycle commitments and consequential recovery, governance, and support milestones | 5 |
| **`keep` total** |  | **25** |
| `skip` | Artifact-bounded preferences, borrowed workflows, drafts, configurations, and exhausted authorizations | 5 |
| `skip` | Finite schedules, provisional schemes, non-adopted successes, rehearsals, and inherited metadata | 5 |
| `skip` | Inapplicable conditions, closed transaction choices, exercise pairings, queue priorities, and release coordination | 5 |
| `skip` | Local aids, random assignments, bounded access arrangements, unformed roles, and obsolete interpretations | 5 |
| `skip` | Isolated checks, coverage roles, imports, rejected rationale, and reconciled anomalies | 5 |
| **`skip` total** |  | **25** |

Batch 3 differs from prior batches by emphasizing interpretive contracts,
reversibility, traceability, lifecycle-triggered duties, governance
consequences, bounded authority, non-adopted technical success, and formal but
non-generalizable records. It avoids adding more generic answer-format rows,
approval templates, dataset-release rules, casual reactions, obvious short
events, generic proposals, or renamed versions of existing system facts.

The slots reduce shortcut learning by distributing named projects, technical
detail, formal status, approval, repeated activity, successful outcomes, and
event completion across both labels. Labels depend on lasting behavioral
value, authority, scope, adoption, and continuing consequence rather than
single words, tense, source style, or project naming.

All 50 slots were drafted and reviewed as `primary_eligible=true` before
owner-approved migration. No slot is policy-only, requires existing-memory context,
or represents a production retention-policy decision.

The maintained target remains approximately 200 `keep` and 200 `skip` rows.
All 50 reviewed Batch 3 rows were approved and migrated, changing the
canonical balance from 80/77 to 105 `keep` and 102 `skip` and leaving
approximately 95 `keep` and 98 `skip` examples toward that target. These
figures record progress; they do not authorize Batch 4 or weak examples to
meet a quota.

## 7. Owner Review Gates And Outcome

1. The owner reviewed and approved this inventory before candidate drafting.
2. Drafting occurred in the separate review CSV; this specification was not
   used directly as a source of canonical rows.
3. The completed drafts were audited against the then-current canonical dataset
   for privacy, scope, exact duplicates, conceptual overlap, lexical
   shortcuts, and source/paraphrase grouping.
4. The owner explicitly reviewed and approved all 50 candidate rows before
   their migration to `primary_v0_3`.
5. Training remains blocked until the final target, cleaning, group review,
   and frozen untouched group-aware holdout steps are completed. Evaluation,
   metric calculation, split assignment, and notebook execution remain
   blocked.
6. Runtime integration remains blocked and requires a separate explicitly
   approved task. Any future classifier remains advisory-only and disabled by
   default unless a later approved runtime task changes that status.
