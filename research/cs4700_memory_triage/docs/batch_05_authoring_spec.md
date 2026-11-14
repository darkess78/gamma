# Batch 05 Memory-Triage Authoring Specification

Status: **Owner reviewed; candidate drafting and owner-approved canonical migration complete.**

Scope: **Owner-approved semantic inventory, reviewed candidate drafting, and completed migration provenance.**

Planned size: **exactly 50 future primary-eligible examples.**

Planned balance: **exactly 25 `keep` and 25 `skip`.**

The owner approved this semantic inventory for review-only candidate drafting,
reviewed the resulting 50 candidate rows, and explicitly approved all 50 for
canonical migration. The Batch 5 migration is complete.
Training, evaluation, split assignment, notebook execution, runtime
integration, staging, committing, and pushing remain blocked. This plan is
based on direct inspection of the actual current canonical dataset.

## 1. Current Evidence Inspected

The review covered these project files:

- repository `README.md`, `specs/README.md`, and
  `specs/current_implementations.md`;
- `specs/class-projects/cs4700-memory-triage.md`;
- `research/cs4700_memory_triage/README.md` and `data/README.md`;
- `data/memory_triage_pilot.csv` and
  `data/memory_triage_primary_dataset.csv`;
- `docs/labeling_guide.md`, `docs/dataset_card.md`, `docs/pilot_review.md`,
  `docs/blinded_self_relabel_protocol.md`, and
  `docs/final_dataset_expansion_plan.md`;
- Batch 01 through Batch 04 authoring specifications, candidate-draft CSVs,
  candidate reviews, and migration records; and
- the structure and source of `notebooks/CS4700_Gamma_Memory_Triage.ipynb`,
  without executing a cell.

Direct CSV inspection confirms 257 canonical rows: 130 `keep` and 127
`skip`. Provenance is 57 primary-eligible pilot v0.3 rows and 50 rows from
each of Batches 01, 02, 03, and 04. All 257 rows have
`primary_eligible=true`, `privacy_checked=true`, and `split=unassigned`.

Row-level dataset-release provenance is preserved: the 57 pilot rows and 50
Batch 01 rows carry `primary_v0_1`; Batch 02, Batch 03, and Batch 04 each have
50 rows carrying `primary_v0_2`, `primary_v0_3`, and `primary_v0_4`,
respectively. Canonical example IDs and source-record IDs are unique and
nonblank. Every canonical row currently has a unique, nonblank opaque
`source_group_id` and `paraphrase_group_id`. The frozen pilot source retains
shared groups for three policy-only paraphrases, but those rows are excluded
from the canonical primary dataset.

Batch 5 planning is therefore based on direct inspection of the current
257-row canonical dataset, not on the reported checkpoint alone. The project
specification still describes the older `primary_v0_1` state; the current CSV,
workspace documents, dataset card, and migration records establish the actual
`primary_v0_4` state used here.

## 2. Constraints Carried Forward

A `keep` concept must describe standalone information likely to improve a
separate future conversation: a lasting preference, reusable workflow rule,
stable scoped project fact, durable correction, continuing commitment, safe
stable relationship, or consequential milestone. A `skip` concept must have a
text-contained reason that it is non-durable, such as artifact or episode
scope, unresolved contingency, lack of adoption, obsolete applicability,
missing specificity, or absence of continuing behavioral value.

Every later candidate must be understandable from its text alone. Policy-only
concepts and concepts whose decision depends on whether Gamma already stores
an equivalent fact are excluded. Explicit save or remember language cannot
substitute for durability. Project-specific architecture, tooling, reporting,
workflow, compatibility, or system rules must name the relevant project or
system. A non-project-specific `keep` is allowed only for a genuinely broad
cross-project preference or safe reusable rule.

Later wording must not make either label predictable from project-name
presence, tense, approval language, completion language, event language,
source status, or words that resemble a label explanation. Named projects and
systems must appear meaningfully in both labels. `Keep` wording cannot rely on
permanence words, obligation phrasing, or polished system language; `skip`
wording cannot rely on words such as temporary, one-time, currently, trial,
expired, completed, former, cancelled, ended, closed, unapproved, or
authoritative.

This semantic inventory is not a drafting template. Future Batch 5 drafting
cannot use the plan as a shortcut to produce repetitive, template-driven text
or substitute project, artifact, role, and date names into a shared sentence
shape.

## 3. Current Coverage And Gap Analysis

### Confirmed high-density families

The 257 rows already give dense coverage to communication and presentation
preferences; accessibility presentation defaults; cautious implementation,
testing, approval, privacy, review, evidence, and handoff workflows; ordinary
named-system architecture, compatibility, fallback, migration, identity,
provenance, and status conventions; reviewer, ownership, liaison, and
maintenance roles; and commitments or milestones that establish baselines,
support duties, governance, evidence standards, or source precedence.

The `skip` side is especially dense in transient states, casual reactions,
single-artifact instructions, generic closed work, finite events, narrow
permissions, temporary workarounds, rejected or unadopted proposals, obsolete
facts, unsupported inferences, role offers, source-authority defects,
provenance defects, local technical conditions, and isolated observations
without continuing consequences. Batch 4 further covers group-selection
attribution, generated defaults, fictional subjects, forced choices,
source-fidelity text, tutorial and vendor authority, simulations,
placeholders, causal overreach, contaminated evidence, wrong-question
measurement, surrogate compatibility, same-name collisions, checkpoint
state, advisory alignment, example-data relationships, outside promises,
audience summaries, unmapped sample labels, ceremonial recognition,
fictional incidents, unauthoritative forecasts, subset overreach, and local
environment exceptions. Renaming any of these is not novelty.

### Confirmed thinner or missing boundaries

Counts and row review confirm thinner coverage for durable corrections (7),
meaningful milestones (9), continuing commitments (10), and stable safe
relationships (14) than for ordinary project facts and reusable rules. There
is room for more keep-side consequences involving interpretation, request
routing, decision reopening, dependency exit readiness, and data portability,
provided they do not become role, handoff, or governance paraphrases.

Several text-solvable cross-label boundaries are also relatively thin:

- a useful preference-like choice versus a choice caused by a bounded
  deliverable objective or resource tradeoff;
- an active project direction versus a contingency that has not yet selected
  a course of action;
- a recurring safe specialist responsibility versus records whose role-like
  vocabulary actually describes ordering, possession, or response handling;
- an episode whose outcome changes later interpretation or duties versus one
  that merely supplies background history;
- a scoped correction that changes future behavior versus a correction whose
  only effect is consumed inside one artifact or adjudication; and
- a durable routing or interpretation boundary versus a pooled result that
  cannot establish a member-level fact, a layout relationship, an equivalent
  representation, or a transaction's consumed amount.

### Tentative opportunities requiring owner scrutiny

Preference boundaries around constrained choices and project boundaries around
deferred decisions are promising but can easily overlap prior artifact-specific
preferences, unapproved proposals, source-authority failures, or vague
statements. Aggregate interpretation, layout relationships, equivalent
representations, and transaction accounting also require careful drafting so
that their text-contained limits do not become generic missing-context or
one-artifact examples. The owner should reject any slot that cannot preserve a
distinct future-consequence test when later expressed naturally.

This plan does not recommend model work, production integration, specification
changes, or Gamma runtime changes. It avoids artificial novelty from synthetic
project renaming. Unless a later owner review identifies a genuinely new,
well-separated boundary, Batch 5 must not add another cluster of generic
closed-event skips, narrow-permission skips, temporary workflow skips,
unsupported technical-condition skips, source-authority skips,
provenance/attribution skips, role-only skips, or ordinary project-fact keeps.

## 4. Batch 5 Semantic Coverage Strategy

Batch 5 should center on consequences: whether a fact changes future choices,
interpretation, routing, portability, or constraints. This permits new
decision boundaries without manufacturing new topics. Both labels should span
preferences, boundaries, named projects or systems, safe role facts, and
episodic consequences, while skip-side interpretation examples also vary
across analytical, presentation, measurement, and transaction records. Neither
label should depend on a single semantic or source style.

Future drafting must distribute lexical form, tense, project names, approval
status, completion status, event scope, authority status, and source
provenance across both labels. Useful ambiguity may test a real boundary, but
the candidate text must still contain the facts needed to decide it. Omitting
the decisive fact is not an acceptable way to make an example difficult.

Each slot represents one concept. Template clusters that differ only by
project name, artifact name, role name, date, or small wording changes are
prohibited. Later wording must not make `keep` synonymous with named systems,
formal tone, adopted status, or future tense, and must not make `skip`
synonymous with completed events, weak authority, missing provenance, or
bounded-time vocabulary.

High-confidence `skip` examples must not collapse into generic descriptions
that “this information is limited.” Non-durability must follow from a precise,
text-contained reason distinct from prior batches. High-confidence `keep`
examples must have an actual ongoing decision, behavior, interpretation, or
constraint consequence, not merely a polished system statement.

## 5. Fifty-Slot Semantic Inventory

These are planning concepts only. They contain no final candidate text, full
candidate sentences, draft rows, source-record IDs, canonical IDs, CSV data,
or final group assignments.

| Slot | Planned label | Semantic family | Decision boundary being tested | Scope requirement | Candidate type or source-style target | Novelty / anti-overlap note | Planned grouping boundary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B05-01 | `keep` | Preference: decision reopening | Reusable preference for identifying what evidence would justify revisiting a settled recommendation | May be broad cross-project | Preference; decision-support extraction | Extends decision sensitivity beyond B04-01 without repeating confidence or counterevidence preferences | Keep separate from appeals, approval gates, and project-specific evidence thresholds |
| B05-02 | `keep` | Preference: unresolved-item visibility | Reusable preference for separating settled outcomes from choices still awaiting the user's input | May be broad cross-project | Preference; collaboration-summary style | Focuses on unresolved ownership rather than status-report formatting or handoff risk lists | No variants based only on checklist, report, or interface format |
| B05-03 | `keep` | Preference: terminology continuity | Reusable preference for preserving the user's established terms while explicitly flagging necessary terminology changes | May be broad cross-project | Preference; communication/correction style | Different from glossary governance, translation, source fidelity, and correction directness | Exclude named-project vocabularies and one-document editing choices |
| B05-04 | `keep` | Accessibility preference: change orientation | Reusable need for interface-change explanations to connect prior and new locations or controls | May be broad cross-project | Preference; accessibility extraction | Extends B04-05 spatial orientation from static directions to understanding change | Exclude one migration guide, landmark-only wording, and generic release notes |
| B05-05 | `keep` | Preference: fallback usefulness | Reusable preference for receiving a viable reduced-scope alternative when the requested outcome is blocked | May be broad cross-project | Preference; assistance-default extraction | Distinct from safest reversible option, failure diagnosis, and system fallback behavior | Do not create variants by changing blocked tool, service, or deliverable |
| B05-06 | `keep` | Reusable rule: exception retirement | Standing rule to remove or reassess an exception when the condition that justified it no longer applies | May be broad cross-project | Boundary; workflow-rule style | Adds lifecycle control for exceptions rather than another narrow approval or rollback rule | Exclude one named exception and obsolete-fact history |
| B05-07 | `keep` | Reusable rule: unresolved consequence ownership | Standing rule that deferred consequential choices retain a named decision owner and return condition | May be broad cross-project | Boundary; decision-workflow style | Different from B04-10 escalation and B03-06 handoff follow-ups; concerns deliberate deferral | One deferral-governance concept; exclude meeting scheduling and generic task ownership |
| B05-08 | `keep` | Reusable rule: correction reach | Standing rule to identify which downstream conclusions must be reconsidered when a material premise is corrected | May be broad cross-project | Boundary; evidence/correction style | Distinct from B04-24 artifact propagation; consequence is reasoning revision, not copying a fact | Exclude source lineage, causal claims, and project-specific propagation machinery |
| B05-09 | `keep` | Reusable rule: reversible exploration boundary | Standing separation between exploratory actions and changes that establish shared state or commitments | May be broad cross-project | Boundary; workflow-control style | Extends small-safe-change and preview rules by distinguishing state consequences | No sandbox, approval, or trial-language template variants |
| B05-10 | `keep` | Reusable rule: capability disclosure | Standing requirement to identify material limitations before relying on a tool for a consequential task | May be broad cross-project | Boundary; safe tool-use style | Not vendor authority, evidence provenance, or unsupported technical-condition reasoning | Exclude model integration, product policy, and one failed tool episode |
| B05-11 | `keep` | Named-system fact: semantic deletion | Adopted distinction between removal from active use and preservation needed to interpret historical records | Later wording must name the relevant project/system | Project; data-lifecycle fact | New interpretation boundary beyond decommission stewardship and archive compatibility | One system and one deletion/history distinction; exclude retention policy decisions |
| B05-12 | `keep` | Named-system constraint: replay meaning | Adopted rule that replayed events do not create a second real-world action or duplicate obligation | Later wording must name the relevant project/system | Project; event-semantics contract | Distinct from stable identity, deduplication policy, and write-success semantics | Exclude implementation mechanisms and existing-memory duplicate cases |
| B05-13 | `keep` | Named-system correction: scope of a metric | Corrected metric interpretation changes which future decision the metric may support | Later wording must name the relevant project/system | Project; durable correction | Extends B02-17 and B03-17 only if the metric's corrected decision use is materially different | One metric/use correction; exclude wrong-question measurement hard negatives |
| B05-14 | `keep` | Named-system correction: fallback ownership | Corrected responsibility for deciding when degraded operation may begin or end | Later wording must name the relevant project/system | Project; durable responsibility correction | Different from parser/policy ownership, maintenance reassignment, and incident routing | No component-name substitutions or generic escalation variants |
| B05-15 | `keep` | Named-system constraint: interpretation version | Adopted rule that historical outputs remain interpretable under the definitions in force when they were produced | Later wording must name the relevant project/system | Project; interpretation/lifecycle fact | New temporal interpretation consequence; not backward file-format support or source precedence | Exclude retention schedules, migrations, and current-schema compatibility |
| B05-16 | `keep` | Stable relationship: decision recorder | Recurring safe role captures decisions and unresolved dissent without owning the decisions | Later wording must name the relevant project or recurring context | Profile; role-boundary summary | Distinct from continuity coordinator, reviewer, and decision authority roles | One recorder role and explicit non-authority boundary; no real identities |
| B05-17 | `keep` | Stable relationship: novice-interpretation reviewer | Recurring safe specialist checks whether domain explanations remain usable by intended newcomers without deciding the underlying content | Later wording must name the relevant project or recurring context | Profile; specialist-review summary | Nearest to pilot accessibility review and B01-20 technical review, but responsibility is audience comprehension rather than interface access, correctness, or approval | One recurring comprehension responsibility; exclude bounded teaching events, content authority, and real identities |
| B05-18 | `keep` | Stable relationship: interpretation contact | Recurring safe role resolves how a maintained domain vocabulary applies to new cases without controlling project scope | Later wording must name the relevant project/system | Profile; specialist-role summary | Different from glossary maintenance and general category approval | One interpretive responsibility; exclude sensitive domains and real identities |
| B05-19 | `keep` | Stable relationship: dependency contact | Recurring role communicates externally driven compatibility changes that affect a named project's planning | Later wording must name the relevant project/system | Profile; coordination-role summary | Future value is a maintained communication responsibility, not third-party authority or promises | Exclude one vendor interaction, support intake, and maintenance ownership |
| B05-20 | `keep` | Consequential milestone: exception closure rule | Resolved exception establishes the conditions future similar cases must satisfy before normal handling resumes | Later wording must name the relevant project/system | Episodic; consequential milestone | Unlike a closed event, the outcome creates a reusable decision rule; not a narrow permission | One established condition set; exclude incident retrospectives and generic approval gates |
| B05-21 | `keep` | Consequential milestone: interpretation baseline | Completed reconciliation establishes a continuing shared definition for future comparisons | Later wording must name the relevant project/system | Episodic; meaningful milestone | Distinct from source precedence and performance baselines; consequence is shared meaning | Exclude terminology stewardship and one corrected artifact |
| B05-22 | `keep` | Consequential milestone: intent-based routing boundary | Resolution of a consequential cross-channel ambiguity establishes which request intent controls routing when channel and subject point to different destinations | Later wording must name the relevant project/system | Episodic; routing milestone | Nearest to B02-08 incident routing and B04-20 corrected intake scope, but establishes a durable intent-versus-channel interpretation boundary rather than role ownership or authority transfer | One routing conflict and its continuing interpretation; exclude source precedence, approval gates, and ordinary project facts |
| B05-23 | `keep` | Ongoing commitment: decision-constraint continuity | Continuing commitment to preserve the constraints and tradeoffs that an adopted consequential decision must satisfy as implementations change | Later wording must name the relevant project/system | Project; decision-continuity commitment | Nearest to B01-08 decision recording and B04-06 reversal boundaries, but future value is maintaining decision constraints through implementation change rather than reporting status or recording one rationale | One adopted decision and its constraint class; exclude cadence, handoff, exception, and generic governance concepts |
| B05-24 | `keep` | Ongoing commitment: user-data portability | Continuing commitment that changes to a named system's internal storage preserve a usable owner-controlled exit path for user-provided data | Later wording must name the relevant project/system | Project; portability commitment | Nearest to B01-25 schema compatibility and B02-14 legacy readability, but protects user exit from the system rather than consumer interoperability, duplicate views, or historical interpretation | One system and one portability obligation; exclude migration procedure, correction propagation, and retention policy |
| B05-25 | `keep` | Ongoing commitment: decision revisit trigger | Continuing project commitment to reopen a specific decision when named evidence conditions materially change | Later wording must name the relevant project/system | Project; ongoing commitment | New conditional commitment with real future consequence; not an unapproved contingency | One decision and trigger class; exclude schedules, forecasts, and generic monitoring |
| B05-26 | `skip` | Preference boundary: deliverable objective | A choice that optimizes one deliverable's stated objective but does not establish a general user preference | Must remain explicitly bounded/context-specific | Preference; artifact-decision summary | Different from forced calibration and source-fidelity wording; the choice is voluntary but objective-bound | One deliverable and one tradeoff; exclude color, layout, or explanation-length repeats |
| B05-27 | `skip` | Preference boundary: borrowed-user setting | A setting selected for another participant's use cannot establish the user's own preference | Must identify the bounded use and avoid private identities | Preference; collaboration-summary style | Not fictional-subject or group-attribution reuse; concerns accommodation of an actual generic collaborator | One safe collaborator context; exclude stable relationship facts and accessibility inference |
| B05-28 | `skip` | Preference boundary: diagnostic selection | A user choice made to expose a fault or compare behavior does not establish a preferred normal setting | Must remain explicitly bounded/context-specific | Preference; diagnostic-session summary | Distinct from forced choice and one-demo configuration; purpose is fault isolation | One diagnostic choice; exclude unsupported technical conclusions and lasting tool preferences |
| B05-29 | `skip` | Preference boundary: courtesy concession | A choice accommodates another participant's immediate constraint without supplying evidence of the user's own usual preference | Must identify the bounded interaction and use only safe generic participant context | Preference; interaction-choice summary | Nearest to B05-27 borrowed-user setting and B04-26 group attribution, but concerns a voluntary interpersonal concession rather than another person's setting or a collective choice | One interaction and one conceded choice; exclude artifacts, roles, accessibility inference, and recurring scheduling |
| B05-30 | `skip` | Preference boundary: unresolved comparison | A user compared viable styles but deliberately left the usual preference undecided | Must remain explicitly bounded/context-specific | Preference; exploratory-choice summary | Related to B02-28 but must test a different decision dimension and avoid practice-review framing | One open comparison; exclude unapproved project proposals and forced choices |
| B05-31 | `skip` | Boundary: case-consumed interpretation | A rule-like interpretation resolves only the named case because its decisive facts do not recur beyond that case | Must name the case context and remain explicitly bounded | Boundary; adjudication summary | Not a narrow permission or generic closed event; limitation follows from case-specific facts | One adjudication; exclude policy decisions and reusable precedents |
| B05-32 | `skip` | Boundary: counterfactual analysis assumption | A constraint is introduced only to compare consequences inside a bounded analysis and does not state a selected project condition or direction | Must name the analysis context and remain explicitly bounded | Boundary; analytical-comparison summary | Nearest to B02-35 conditional feature and B03-45 exploratory analogy, but tests the status of an assumed premise rather than an unadopted option, recommendation, or design metaphor | One analytical assumption; exclude proposals, promises, forecasts, and implementation branches |
| B05-33 | `skip` | Boundary: foreign-rule contrast | A rule is described to explain how a separately scoped system differs, while the text denies that the rule governs the project under review | Must name both safe generic scopes clearly | Boundary; comparative-summary style | Nearest to B04-39 same-name scope and B04-31 instructional authority, but the non-durability follows from explicit comparative scope rather than name collision, source weakness, or non-adoption | One contrasted rule and two unambiguous scopes; exclude workaround, negotiation, and source-precedence concepts |
| B05-34 | `skip` | Boundary: threshold chosen for inspection | A threshold is selected to inspect a bounded sample rather than define acceptance or future operation | Must name the relevant project/system and inspection scope | Boundary/project; analysis-summary style | New purpose-of-threshold distinction; not wrong-question measurement or model evaluation | One inspection threshold; exclude classifier metrics, training, and production policy |
| B05-35 | `skip` | Boundary: deferred choice without trigger | A future-facing intention to revisit a choice supplies neither a current decision nor a condition that would guide reopening | Must name the relevant project/system | Boundary; planning-summary style | Hard negative against B05-25; lack of an actionable trigger is text-contained | One deferred choice; exclude placeholders, forecasts, and generic unapproved proposals |
| B05-36 | `skip` | Project boundary: retrospective-only categorization | A categorization organizes one retrospective account but is not used by current project records, decisions, or procedures | Must name the relevant project and retrospective scope | Project; retrospective-summary style | Nearest to B03-32 provisional taxonomy and B04-45 sample label, but the categories structure a bounded retrospective narrative rather than intake records, repair mappings, or demonstrations | One retrospective and one categorization scheme; exclude exception history, data repair, and adopted terminology |
| B05-37 | `skip` | Aggregation boundary: pooled result | A result calculated across a defined collection cannot establish that any particular member has the pooled property when the record explicitly supplies no member-level result | Must identify the collection, aggregation level, and prohibited member-level interpretation without private or sensitive attributes | Analytical; aggregate-result summary | Nearest to B04-49 subset overreach and B04-26 group-choice attribution, but reverses the inference direction and concerns a mathematical aggregate rather than coverage of a larger population or attribution of a collective decision | One aggregate-to-member inference boundary; exclude sampling coverage, group preferences, causal claims, missing criteria, and variants made by changing the measured property |
| B05-38 | `skip` | Quantity boundary: ceiling mistaken for target | A stated quantity bounds what may be considered in one scoped decision but does not select an operating value, goal, or commitment | Must name the bounded decision context | Boundary/project; planning-constraint summary | Nearest to B04-37 wrong-question measurement and B01-43 missing criterion, but distinguishes a valid upper bound from an unstated target without invoking metrics, models, or environment conditions | One ceiling and one decision scope; exclude capacity observations, forecasts, and acceptance thresholds |
| B05-39 | `skip` | Presentation boundary: layout adjacency | Two items placed together solely to satisfy an explicitly stated page or display arrangement do not thereby acquire a semantic relationship, shared category, priority, or dependency | Must identify the bounded presentation and the nonsemantic layout basis | Presentation; layout-review summary | Nearest to B05-44 filing location and B05-41 neutral ordering, but tests spatial co-placement rather than navigational storage or sequence position; no choice, proposal, or available-option logic is involved | One layout relationship boundary; exclude category headings, folder placement, ordering keys, source-fidelity copying, and variants based only on changing the artifact or visual arrangement |
| B05-40 | `skip` | Commitment boundary: receipt without assent | Acknowledging receipt of a consequential request or condition establishes delivery only, not agreement, adoption, or a future duty | Must identify the bounded exchange without private identity | Boundary; communication-record summary | Nearest to pilot receipt acknowledgement and B04-43 outside promise, but the hard negative is delivery-versus-assent for consequential content rather than casual reaction or third-party authority | One receipt and one unaccepted content item; exclude role offers, approvals, and coordination workflows |
| B05-41 | `skip` | Ordering boundary: neutral sort mistaken for priority | Position in a bounded list follows an explicit non-preference ordering key and therefore establishes no priority, ranking, or user choice | Must identify the list purpose and ordering basis | Boundary; list-interpretation summary | Nearest to B03-42 random speaking order and B03-39 consumed queue priority, but uses deterministic neutral ordering and denies that list position encodes priority | One list and one neutral ordering key; exclude presentation preference and workflow sequencing |
| B05-42 | `skip` | Ownership-language boundary: copy designation | Ownership language identifies whose copy or workspace contains material, not who owns the project, content meaning, decisions, or maintenance | Must name the relevant artifact context and use no real identity | Project; artifact-context summary | Nearest to B02-46 inferred documentation ownership and B03-35 unsupported owner metadata, but resolves a lexical sense distinction rather than an inference from contribution or defective provenance | One copy designation; exclude authority transfer, role offers, and maintained ownership claims |
| B05-43 | `skip` | Representation boundary: equivalent quantity | A quantity restated in another unit or scale is explicitly the same measurement, so the second representation supplies neither a correction, a changed state, nor an independent observation with future behavioral value | Must identify the bounded measurement and establish equivalence within the text without relying on a bare pair of numbers | Measurement; comparison-note summary | Nearest to B03-48 one-import normalization and B03-10 durable measurement interpretation, but concerns duplicate representation of one quantity rather than a discarded conversion workflow or a standing units-and-reference-frame rule | One measurement and its equivalent representation; exclude import conventions, preferred units, corrections, tolerances, local environment conditions, and variants made by substituting units or scales |
| B05-44 | `skip` | Classification boundary: filing location | Placement under a heading or folder is used only to locate one bounded record and does not assert that the record has the heading's substantive property | Must name the bounded record collection | Project; record-organization summary | Nearest to B03-32 provisional taxonomy and B04-45 sample label, but separates navigational placement from semantic classification rather than proposing or demonstrating a category scheme | One record and one locating structure; exclude data-repair mappings and canonical taxonomy rules |
| B05-45 | `skip` | Date boundary: review horizon | A future date marks when one bounded statement must be checked again but does not itself establish a deadline, event, recurring schedule, or commitment | Must identify the statement and review purpose | Boundary/project; dated-record summary | Nearest to B01-29 finite review schedule and B04-34 calendar placeholder, but distinguishes metadata about validity review from an activity plan or attendance obligation | One statement and one review horizon; exclude obsolete facts, reminders, and recurring cadence |
| B05-46 | `skip` | Episodic boundary: correction consumed in artifact | A correction matters to one delivered artifact but changes no maintained definition, source, procedure, or later decision | Must remain explicitly bounded/context-specific | Episodic; correction summary | New contrast with durable corrections; not merely a closed task or isolated typo | One material correction and one consumed consequence; exclude anomaly reconciliation |
| B05-47 | `skip` | Attribution boundary: template repetition | Repeated wording across bounded records comes from an identified copied scaffold and does not establish repeated user emphasis, preference, or independent agreement | Must identify the bounded record set and scaffold origin without quoting candidate-like text | Episodic; record-comparison summary | Nearest to B04-27 generated default and B04-30 source-fidelity wording, but tests repetition attribution across records rather than a selected setting or one artifact's copied style | One scaffold and one bounded record set; exclude source-authority claims and general formatting preferences |
| B05-48 | `skip` | Transaction boundary: gross amount versus consumed value | A bounded transaction record includes a recoverable amount in its gross total, while explicitly separating the amount actually consumed; the gross figure therefore creates no lasting spending level, preference, obligation, or resource commitment | Must identify the safe generic transaction, the recoverable component, and the consumed-value distinction without account details or real identities | Episodic; transaction-summary style | Nearest to B03-37 consumed transaction choice and B04-37 wrong-question measurement, but tests accounting composition rather than a selected preference or a measure aimed at a different outcome; it contains no alternative, hold, or future-plan logic | One gross-versus-consumed accounting boundary; exclude budgets, refunds as ongoing tasks, purchase preferences, capacity reservations, accounting policies, and variants made by changing goods or amounts |
| B05-49 | `skip` | Reference boundary: relative anchor without date | A relative interval is recorded for one bounded event, but the event's anchor is absent from the standalone text, so no usable date or schedule can be recovered | Must make the missing anchor text-contained without a vague pronoun | Episodic; scheduling-record summary | Nearest to B01-42 missing artifact and pilot unnamed-option cases, but the missing information is a temporal anchor in an otherwise specific record rather than an unnamed object or workflow | One event and one unrecoverable relative interval; exclude coordination methods and recurring cadence |
| B05-50 | `skip` | Identifier boundary: code mistaken for meaning | An opaque code attached to one bounded record is explicitly only a lookup token and does not encode priority, date, category, preference, or project rule | Must name the safe record context without supplying an ID-like literal | Episodic/project; record-reference summary | Nearest to B04-45 unmapped sample label and B04-11 stable identity, but the token supports only retrieval of one record and explicitly carries no semantic classification or continuity contract | One lookup-token concept; exclude actual identifiers, mappings, permissions, exceptions, and canonical identity rules |

All drafted slots became primary eligible after owner review confirmed that
their candidate text stands alone and supports the planned label.

## 6. Coverage And Balance Audit

The inventory contains exactly 25 planned `keep` slots and 25 planned `skip`
slots.

| Planned semantic family | `keep` | `skip` |
| --- | ---: | ---: |
| Preference and accessibility | 5 | 5 |
| Reusable or bounded decision rules | 5 | 5 |
| Named project/system facts and constraints | 5 | 5 |
| Safe relationships, measurement, and interpretation boundaries | 4 | 5 |
| Milestones, commitments, transactions, and episodic consequences | 6 | 5 |
| **Total** | **25** | **25** |

Batch 5 differs from prior batches by emphasizing whether information changes
future interpretation, decision reopening, routing, portability, or downstream
consequence. It deliberately reduces reliance on ordinary
project facts and on skips explained only by closure, authority, provenance,
or short duration. The inventory includes paired hard boundaries, but each
slot has a separate semantic purpose and grouping limit rather than a planned
sentence template.

Shortcut learning is limited by placing formal named-system concepts,
future-facing decisions, measurement language, and consequential episodes
in both labels. Labels cannot be inferred from polish, tense, named-project presence,
approval or completion status, authority, or source language. Later drafting
must vary syntax and vocabulary and re-audit these risks against the actual
texts.

All 50 slots are intended to be primary eligible. No slot is policy-only,
requires existing-memory context, or represents a production retention-policy
decision. The target remains approximately 200 `keep` and 200 `skip`. The
historical planning projection for all 50 slots was 155 `keep` and 152 `skip`,
leaving approximately 45 `keep` and 48 `skip` toward that target; the completed
migration now matches that projection.

## 7. Required Owner Review Gates

- No Batch 5 candidate wording may be drafted until the owner explicitly
  reviews and approves this inventory.
- Later drafting must occur in a separate review-only CSV or equivalent review
  artifact; this specification is not a candidate source file.
- Future drafts must be re-audited against the then-current canonical dataset
  for privacy, scope, exact duplicates, conceptual overlap, lexical shortcuts,
  and source/paraphrase grouping.
- No Batch 5 candidate row may enter the canonical dataset without explicit
  owner approval.
- Training remains blocked until the final target, cleaning, group review, and
  frozen untouched group-aware holdout steps are completed.
- Evaluation, split assignment, and notebook execution remain blocked until
  their separately approved gates are satisfied.
- Runtime integration remains blocked and requires a separate explicitly
  approved task. Any future classifier remains advisory-only and disabled by
  default unless a later approved runtime task changes that status.

Owner review should pay particular attention to B05-13, B05-20, B05-21,
B05-22, B05-26, B05-30, B05-31, B05-35, B05-37, B05-38, B05-39, B05-40,
B05-43, B05-44, B05-47, B05-48, and B05-50. Those slots are useful boundaries,
but weak drafting could overlap prior metric, milestone, routing,
bounded-preference, aggregate-attribution, record-organization, measurement,
transaction, missing-context, taxonomy, generated-text, or identity examples.
Approval should require a clear distinction before any candidate wording is
authorized.
