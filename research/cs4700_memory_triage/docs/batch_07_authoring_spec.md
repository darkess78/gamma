# Batch 07 Semantic Coverage Plan

## 1. Review-only status and scope boundary

Status: review-only semantic plan; not approved for candidate drafting or
migration.

This document plans exactly 43 future primary-eligible concepts under labeling
guide v0.3. It contains no candidate wording, prompts, paraphrases, draft rows,
canonical IDs, source-record IDs, migration mappings, or review records. It
does not authorize creation of any of those materials. Every future concept
must be judged from its own text and must not depend on an already-stored
memory or other unavailable context.

## 2. Verified corpus snapshot and remaining balance gap

The reviewed canonical CSV has 357 rows: 180 `keep` and 177 `skip`. It consists
of 57 eligible pilot rows and 50 rows from each of Batches 01 through 06. All
357 rows have `primary_eligible=true` and `split=unassigned`. Its columns match
the required 18-column schema. The frozen pilot SHA-256 is
`ef369b736ed030dbc0ec525ac10870e1bd870c17cdace08522866c28f43fed38`.

The exact balance gap to 400 rows and 200 examples per class is 20 `keep` and
23 `skip`, for 43 total future rows.

## 3. Coverage-audit method

The audit reviewed the pilot, canonical dataset, labeling and dataset guides,
expansion plan, and every Batch 01 through Batch 06 authoring specification,
candidate draft, candidate review, and migration record. Candidate text was
case-folded, punctuation-normalized, and whitespace-normalized for exact
comparison. This found zero normalized exact-duplicate groups. A normalized
character-sequence scan using the established 0.72 suggestion threshold found
zero flagged pairs among the 357 canonical rows.

Surface scans were followed by manual semantic-neighbor review. That review
treated the underlying retention decision as the family, rather than grouping
items merely because they shared a domain noun. It distinguished durable
preferences, adopted workflows, maintained interpretations, continuing roles,
and consequential milestones from bounded tasks, local representations,
unaccepted proposals, incidental observations, exhausted permissions, and
unsupported inferences.

## 4. Selected Batch 7 coverage slice and why it is new

Batch 7 covers **continuity decisions in learning, creative-production,
collection-care, and community-program workflows**. Earlier batches strongly
cover software projects, evidence and governance semantics, general assistance
preferences, and game/stream continuity. They contain scattered educational,
artifact, and workshop examples, but not a systematic set about reusable
learning accommodations, maintained interpretive conventions, recurring
creative responsibilities, collection-care rules, and continuing community
program obligations.

The slice is decision-based, not domain-based. Both labels use every major
surface: a maintained convention or obligation can be `keep`, while a
session-bound technique, borrowed condition, local display choice, or
unratified suggestion on the same surface is `skip`.

## 5. Existing semantic neighbors considered and avoided

The review avoided restating pilot communication/accessibility preferences;
Batch 01 review order, approval, roles, and one-deliverable instructions;
Batch 02 interruption, glossary, accessibility-demonstration, lifecycle, and
handoff concepts; Batch 03 source traceability, units, accessibility triggers,
finite practicum schedules, and local analogies; Batch 04 evidence provenance,
role boundaries, sample/simulation authority, and local representation; Batch
05 maintained terminology, historical definitions, analytical premises,
locators, and one-artifact corrections; and Batch 06 play, account, stream,
audience, and series continuity.

Those are semantic exclusion boundaries. A future draft is not new merely
because it substitutes a classroom, studio, archive, or community noun into an
existing decision pattern.

## 6. Exact 43-slot semantic inventory

The descriptions below are semantic metadata only, not sentence stems or
draftable examples.

| Slot | Label | Semantic family or decision context | Durability or bounded feature | Existing neighbors reviewed | Why substantively new | Future anti-shortcut note |
| --- | --- | --- | --- | --- | --- | --- |
| B07-01 | `keep` | Learning feedback: error-correction timing | A cross-session learning preference governs when correction occurs relative to completion of an explanation | Pilot explanation preferences; B01-04; B03-09 | Concerns correction timing for learning, not correction directness or defect-versus-style classification | Avoid permanence words and classroom-only label cues |
| B07-02 | `keep` | Learning support: recall versus recognition | A reusable study preference selects the kind of retrieval support used across topics | Pilot concrete-example preference; B02-03; B06-02 | Governs cognitive retrieval mode, not explanation vocabulary or escalating game hints | Do not make a study verb sufficient for `keep` |
| B07-03 | `keep` | Learning workflow: misconception ledger | A maintained record carries resolved misunderstandings into later study planning | B01-08; B05-08; B06-21 | Tracks corrected conceptual misunderstandings, not project decisions, premise propagation, or fictional obligations | Establish future behavioral use without “remember” wording |
| B07-04 | `keep` | Learning accommodation: processing pause | A stable interaction need reserves reflection time before an answer is treated as final | B02-02; B04-02; pilot accessibility rows | Addresses response-processing cadence rather than interruption batching or stopping delegated work | Avoid medical inference and absolute timing tokens |
| B07-05 | `keep` | Course-material convention: notation mapping | A named learning program maintains a cross-session mapping between equivalent notation systems | B02-21; B05-03; B06-24 | Preserves equivalence across instructional sources, not a glossary, user terminology, or shared game landmarks | Name the synthetic program; vary notation language across labels |
| B07-06 | `keep` | Creative review: intent-first critique | A recurring critique method checks an artifact against its stated intent before proposing alternatives | B01-07; B03-09; B05-02 | Uses creator intent as the evaluation anchor, not implementation consequences, defect severity, or settled-status reporting | Do not rely on “every review” as durability proof |
| B07-07 | `keep` | Creative production: provenance of reusable elements | A maintained studio convention records origin and reuse permission for components carried between works | B01-12/B01-17; B03-03; B04-14 | Couples reuse authority to creative components rather than general source traceability or canonical evidence | Avoid policy-only legality and secret/private content |
| B07-08 | `keep` | Creative production: destructive-versus-reversible medium changes | A recurring workflow separates exploratory alterations from changes that consume the original material | B01-13; B03-04; B05-09 | Concerns physical or source-material consumption, not generic destructive operations, previews, or shared state | Include durable practice without “never”; use similar media surfaces for skips |
| B07-09 | `keep` | Performance preparation: cue authority | A stable role owns changes to maintained performance cues across productions | B02-20/B02-21; B04-17/B04-20 | Establishes authority over a recurring cue system, not incident communication, glossary stewardship, or design continuity | Role title alone must not determine the label |
| B07-10 | `keep` | Creative series: continuity bible | A maintained interpretive reference controls recurring fictional-world facts across installments | B01-17; B04-21; B05-15 | Governs authored-world continuity rather than dataset manifests, conflicting registers, or historical metric definitions | Avoid real franchises and avoid “canonical” as an automatic cue |
| B07-11 | `keep` | Collection care: handling constraint | A named collection has an adopted material-handling limit that applies whenever relevant items are used | B02-11/B02-15; B03-21; B05-23 | Protects a collection-specific physical property, not execution hardware, dependency lifecycle, or software tradeoffs | Name a synthetic collection and omit blanket modal templates |
| B07-12 | `keep` | Collection care: condition vocabulary | A maintained distinction between damage, stable variation, and prior repair governs later assessments | B04-25; B05-15; B05-18 | Defines preservation-condition interpretation, not null states, historical report semantics, or generic vocabulary stewardship | Use the same collection vocabulary in bounded skips |
| B07-13 | `keep` | Collection access: surrogate-use rule | A continuing access practice directs routine consultation to a surrogate while preserving defined reasons to inspect the original | B01-12; B02-14; B04-23 | Balances recurring access with original-material preservation, not least disclosure, backward compatibility, or retired-decoder interpretation | Avoid making “original” or “preserve” label shortcuts |
| B07-14 | `keep` | Community program: recurring participation accommodation | A standing program arrangement makes the same activity reachable through more than one participation mode | Pilot accessibility rows; B03-08/B03-15; B02-22 | Concerns recurring program participation, not timed information, software fallback, or demonstration content | Name a synthetic program; include event-local accommodations among skips |
| B07-15 | `keep` | Community program: rotating facilitation obligation | An accepted rotation creates future preparation and handoff duties across meetings | B02-23; B06-16/B06-20; B03-24 | Covers facilitation continuity in a non-broadcast civic program rather than release support, co-stream briefs, hosting, or roadmap governance | Rotation language must not alone imply durability |
| B07-16 | `keep` | Community program: consent renewal trigger | A recurring practice revisits participation consent when the use or audience of contributed material changes | B01-12/B01-13; B05-06; B05-20 | Defines a change-triggered consent boundary, not data minimization, destructive approval, exception removal, or access recheck | Keep it text-solvable; no real participant data or policy adjudication |
| B07-17 | `keep` | Community knowledge: attribution preference | A stable contributor preference governs how their reusable non-sensitive contribution is credited | B03-03; B04-19; B05-24 | Concerns durable credit presentation, not source traceability, representation in planning, or data portability | Do not include identities; credit vocabulary also appears in skip contexts |
| B07-18 | `keep` | Recurring event: sensory-load default | A durable event-format preference reduces simultaneous competing inputs across sessions | B02-02; B02-22; B06-13 | Addresses concurrent sensory load, not interruption timing, readable equivalents, or visual descriptions | Avoid health claims and absolute prohibition wording |
| B07-19 | `keep` | Maintained teaching collection: retirement milestone | A completed milestone changes which instructional materials remain supported while retaining interpretive history | B02-25; B04-23; B05-11 | Governs teaching-material support and historical interpretation, not renderer deprecation, decoder retirement, or hidden records | The continuing consequence, not completion vocabulary, must justify `keep` |
| B07-20 | `keep` | Community workshop: tool-steward responsibility | A stable role maintains shared tools and communicates availability constraints across recurring sessions | B02-16/B02-20; B03-20; B05-19 | Combines physical resource stewardship with recurring participant planning, not software ownership, incident updates, issue translation, or dependency liaison work | Use role and workshop surfaces in skips so neither predicts the label |
| B07-21 | `skip` | Learning session: temporary mnemonic | A mnemonic is invented for one assessed list and is discarded after that bounded use | B03-41/B03-45; B06-39 | It is a purpose-built recall aid, not a local analogy, abandoned design metaphor, or chat joke | Avoid obvious expiry adverbs; do not imply a reusable study preference |
| B07-22 | `skip` | Learning exercise: assigned perspective | A learner adopts a viewpoint solely to complete one exercise, with no resulting belief or standing role | B03-42; B04-33; B04-42 | Separates pedagogical perspective-taking from random order, simulated authority, or synthetic relationship data | Make the exercise complete and self-contained without role-title shortcuts |
| B07-23 | `skip` | Learning assessment: rubric-induced response style | A response form reflects a scoring constraint rather than the learner's usual communication preference | B04-29; B05-26; B01-40 | The assessment rubric causes the style, not a forced preference card, deliverable objective, or review-format instruction | Use preference-like surface language without inferring durability |
| B07-24 | `skip` | Learning schedule: finite remediation block | Repeated meetings end with a defined remediation unit and create no continuing cadence | B03-31; B01-29; B06-42 | Concerns a bounded instructional intervention, not a practicum curriculum, selection round, or fictional clock | Recurrence alone must not imply `keep`; avoid dates as the main cue |
| B07-25 | `skip` | Creative draft: placeholder motif | A motif organizes one exploratory draft but has neither acceptance nor reuse authority | B01-46/B01-49; B03-32/B03-45 | Covers an unadopted creative organizing device, not a proposed project extension, draft requirement, provisional taxonomy, or design analogy | Avoid “draft” as a mechanical skip token |
| B07-26 | `skip` | Creative production: borrowed-tool setting | A setting is caused by a loaned tool used for one production and does not express the creator's preference | Pilot borrowed monitor; B02-31; B05-27 | Distinguishes equipment-imposed creative settings from temporary displays or collaborator accommodations | State the causal boundary; tool vocabulary must occur in both labels |
| B07-27 | `skip` | Performance: venue-issued cue | A cue convention belongs to one venue engagement and supplies no convention for later productions | B02-27; B03-43; B06-42 | Concerns an externally imposed performance cue, not package conversion, access-link lifetime, or in-world scheduling | Avoid relying on venue or “one” alone |
| B07-28 | `skip` | Performance rehearsal: substitute duty | A substitute covers a defined rehearsal need and relinquishes the duty before production continuity begins | B03-47; B02-36/B02-37 | Bounded rehearsal coverage differs from leave coverage, a role that never activated, and a former operational owner | Role language should resemble durable-role slots; make endpoint semantic |
| B07-29 | `skip` | Creative artifact: display mount classification | A mount or frame choice supports one display but does not classify, own, or define the displayed work | B05-39/B05-44; B04-45 | Tests presentation support versus artifact meaning, not adjacency, drawer location, or sample supplier labels | Avoid locator-only wording and use display terms on both labels |
| B07-30 | `skip` | Collection intake: donor-supplied description | An unverified supplied description accompanies an item but lacks authority to establish collection meaning | B03-35; B04-32/B04-43; B05-40 | Applies source-authority reasoning to intake description, not blank-provenance ownership, vendor guidance, outside promises, or receipt acknowledgement | Do not make external-source nouns sufficient for `skip` |
| B07-31 | `skip` | Collection care: transport-only packing choice | A protective arrangement is selected for one shipment and establishes no storage or handling convention | B03-37/B03-48; B02-27; B06-44 | Concerns preservation during transport, not order binding, delivery conversion, package format, or a consumable game piece | Make the bounded purpose explicit without temporary-keyword templates |
| B07-32 | `skip` | Collection exhibit: lighting observation | An apparent material color is caused by one exhibit's illumination and cannot establish the item's stable appearance | B06-45; B04-36; B05-43 | Extends source-versus-observation reasoning to object appearance, not stream scene light, contaminated measurement, or unit restatement | Use color in both labels and identify the observation limit |
| B07-33 | `skip` | Collection catalog: shelf-order inference | Neighboring placement supports retrieval but does not imply subject relationship or priority | B05-39/B05-41/B05-44; B06-38 | Concerns collection adjacency inference, not page layout, alphabetic order, drawer headings, or directory categories | Avoid paraphrasing locator rows; center the false semantic inference |
| B07-34 | `skip` | Community meeting: attendance-derived authority | Repeated attendance is observed, but no mandate or responsibility follows from it | B04-41/B04-46; B06-41 | Tests frequency without authority, unlike advisory alignment, ceremonial participation, or cohort badges | Repetition must not become a durability shortcut |
| B07-35 | `skip` | Community workshop: facilitator suggestion | A facilitator's recommendation remains unadopted by the program and creates no operating rule | B01-49; B04-32/B04-41; B03-44 | Isolates advice from a legitimate local role that still lacks decision adoption, not a draft rule, vendor guidance, advisory consensus, or volunteered duty | Name a synthetic program; facilitator status must not imply authority |
| B07-36 | `skip` | Community program: grant-period reporting cadence | A repeated reporting schedule exists only within a completed funding period and leaves no continuing obligation | B01-29; B02-23; B03-31 | A finite external reporting cadence differs from a selection round, recurring release duty, or finite practicum | Future-sounding cadence cannot decide the label by itself |
| B07-37 | `skip` | Community event: accessibility workaround | An alternate participation method responds to one venue failure and is not adopted as the program's recurring accommodation | B02-31/B02-48; B03-15; B07-14 neighbor | Direct cross-label challenge: incident workaround versus maintained multi-mode participation, not preview or read-aloud setup alone | Avoid framing accessibility domains as automatically durable |
| B07-38 | `skip` | Shared workshop: consumable inventory count | A precise count records one session's remaining supplies and carries no standing capacity or replenishment rule | B02-32; B05-37/B05-38; B06-27 | Measures per-session consumables, not reduced compute capacity, aggregate annotation counts, option ceilings, or active boss state | Precision and quantity must not imply usefulness or permanence |
| B07-39 | `skip` | Recurring event: guest-selected format | A format is chosen for one guest cohort and does not establish the host program's normal format | B02-41/B02-42; B05-27; B06-43 | Concerns bounded participant needs in a recurring program, not classroom explanation depth, sponsor vocabulary, collaborator settings, or immediate chat reaction | Event recurrence must coexist with a bounded format choice |
| B07-40 | `skip` | Teaching collection: copied annotation frequency | Repeated wording comes from a shared template rather than independent agreement or instructional importance | B05-47; B04-30; B04-49 | Applies common-origin reasoning to annotations, not forms, source-card mirroring, or sample coverage | Do not let repetition alone decide either label |
| B07-41 | `skip` | Creative review: unresolved preference comparison | A structured comparison ends without selecting a general critique or presentation preference | B02-28/B02-29; B05-30 | Covers a creative-review preference left open, not a practice trial, one brochure decision, or narrative-versus-table comparison | Use deliberate comparison language without treating exploration as adoption |
| B07-42 | `skip` | Collection project: impressive but nonrepresentative pilot | A successful pilot uses specially prepared items and cannot establish the collection-wide workflow | B03-29/B03-33; B04-38/B04-49 | Tests curated-item representativeness, not demonstration hardware, experimental routing, protocol stubs, or sample source coverage | Success language must appear without implying an adopted milestone |
| B07-43 | `skip` | Community publication: issue-specific terminology | A terminology substitution serves one audience issue and does not amend the maintained program vocabulary | B02-42; B03-41; B05-03/B05-18 | Concerns audience-bounded publication language, not sponsor vocabulary, a leaflet analogy, user terminology, or maintained interpretation roles | Pair terminology across labels; avoid “single issue” as the sole cue |

## 7. Cross-label anti-shortcut design

Learning, studio, collection, workshop, accessibility, role, terminology, and
recurrence surfaces appear in both labels. The label depends on maintained
cross-session consequence versus a bounded cause, missing adoption, invalid
inference, or exhausted scope. “Remember this” is never durability evidence.
Neither role titles, recurring language, named programs, precision, success,
accessibility vocabulary, nor importance can decide a label mechanically.

Future drafting must vary syntax and length within each label, avoid label-
specific opening templates, and include durable concepts without permanence
markers as well as bounded concepts with future-facing or preference-like
surface language.

## 8. Future candidate-drafting constraints

If separately approved, each future candidate must be newly authored,
self-contained, privacy-safe, and labelable from its own text. It may use only
synthetic programs, collections, workshops, and projects. It must contain no
real games, platforms, channels, accounts, people, viewers, usernames, private
data, Gamma runtime behavior, or user information. No candidate may depend on
an existing memory, hidden conversation, policy-only decision, or external
state.

Drafting must preserve each slot's decision distinction rather than merely
substituting nouns into a prior row. It must not introduce candidate pairs,
paraphrases, prompts, canonical metadata, or mechanically predictive temporary
wording. Domain membership never determines the label.

## 9. Owner-review gate

The owner must review and explicitly approve this semantic plan before any
Batch 7 candidate drafting begins. Approval of the plan would authorize only a
separately scoped drafting task; it would not approve drafted candidates for
migration. Any future drafts would require their own privacy, label,
duplicate, similarity, semantic-neighbor, and owner review before a separate
migration decision.

## 10. Explicitly unauthorized next actions

This document does not authorize candidate drafting, draft CSV creation,
canonical migration, dataset editing, split assignment, notebook execution,
model work, evaluation, metric or chart generation, runtime integration,
staging, committing, or pushing. It creates no Batch 7 rows and changes no
existing research artifact.
