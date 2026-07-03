# CS 4700: Gamma Memory Triage

Status: Primary v0.1 working dataset migrated; expansion and cleaning next
Last verified: 2026-07-03

Working title: **Gamma Memory Triage: Using Machine Learning to Decide What
Gamma Should Remember**

## Objective

Classify one generated Gamma-style memory candidate as either:

- `keep`: likely to be useful as long-term memory in a separate future
  conversation;
- `skip`: temporary, vague, repetitive, outdated, reactive, or useful only in
  the current conversation.

The project studies one decision inside the existing memory pipeline. It does
not redesign Gamma's memory architecture or add a new assistant mode.

## Confirmed Gamma Architecture

### Candidate structure and creation

- **Confirmed:** `MemoryCandidate` is defined in
  `src/gamma/schemas/response.py`. Its fields are `type`, `text`, `importance`,
  `tags`, `subject_type`, `subject_name`, and `relationship_to_user`.
- **Confirmed:** Standard non-fast turns may call
  `ConversationService._extract_turn_metadata()` in
  `src/gamma/conversation/service.py`. The LLM metadata pass may return up to
  three candidates.
- **Confirmed:** When metadata extraction supplies no candidates,
  `ConversationService._build_memory_candidates()` applies deterministic
  phrase, length, and memory-personality rules.
- **Confirmed:** In fast mode,
  `ConversationService._background_memory_save()` creates rule-based
  candidates after the response and persists them in a daemon thread.
- **Confirmed:** Explicit phrases such as "remember this" and "save this" are
  inferred as `save_memory` tool calls. `SaveMemoryTool` in
  `src/gamma/tools/builtin.py` normalizes one candidate and calls the same
  persistence service.

### Shared persistence boundary

- **Confirmed:** `MemoryService.persist_candidates()` in
  `src/gamma/memory/service.py` is the shared write boundary for standard,
  background, and explicit-save candidates.
- **Confirmed:** The method currently returns the number of new stored rows as
  an integer.
- **Confirmed:** Supported profile-like candidate types call
  `_upsert_profile_fact()`; `episodic` candidates call
  `_upsert_episodic_memory()`.
- **Confirmed:** Candidates are not retained in a pending-candidate table. A
  supported candidate is immediately converted to a stored row when memory is
  enabled and write mode is not `off`.

### Stored records and continuity

- **Confirmed:** `ProfileFact` in `src/gamma/memory/models.py` stores a durable
  category, fact text, confidence, subject metadata, and creation time. Profile
  upsert logic canonicalizes facts and handles selected preference conflicts
  and identity/preference/project slots.
- **Confirmed:** `EpisodicMemory` stores a summary, importance, tags, subject
  metadata, optional session ID, and creation time. Episodic upsert logic
  merges exact or signature-equivalent events within a matching scope.
- **Confirmed:** `ConversationJournalEntry`, `RollingSessionSummary`,
  `WorkingStateCheckpoint`, and `DurableOutputState` are defined separately in
  `src/gamma/memory/continuity.py`. They provide session continuity and are not
  pending long-term memory candidates.
- **Confirmed:** `ConversationService.respond()` journals normal session turns
  through `ContinuityService.begin_exchange()` and `complete_exchange()`.
  Continuity does not automatically promote raw turns to profile or episodic
  memory.

### Review surfaces and routes

- **Confirmed:** Shana exposes `GET /v1/memory`, `POST /v1/memory/items`,
  `PATCH /v1/memory/items/{kind}/{item_id}`, `POST /v1/memory/clear`, and known
  person routes in `src/gamma/api/routes.py`.
- **Confirmed:** Dashboard memory proxies are defined in
  `src/gamma/dashboard/main.py` and `DashboardService` in
  `src/gamma/dashboard/service.py`.
- **Confirmed:** `src/gamma/dashboard/static/memory.js` supports reviewing,
  creating, editing, and deleting already-stored memories and known people.
  It does not display a pre-write candidate queue or keep/skip labels.
- **Confirmed:** Existing tests cover candidate heuristics, profile conflict
  handling, episodic merging, manual memory editing, linked identities, and
  dashboard memory routes. No current test covers a learned triage model.

## Locked Working Decisions

1. **Working decision:** The unit of classification is one candidate and the
   labels are exactly `keep` and `skip`.
2. **Working decision:** Model selection prioritizes high precision for
   `keep`. False keeps pollute long-term memory; false skips remain important
   and must be reported.
3. **Working decision:** The dataset is manually authored and privacy-safe.
   Raw conversations, stored memory rows, runtime logs, stream traces, secrets,
   usernames, file paths, account identifiers, and copied private content are
   prohibited.
4. **Working decision:** Examples may reflect generic Gamma use cases only
   after being rewritten as standalone anonymized candidates.
5. **Working decision:** Begin with an approximately 60-example pilot. After
   guide validation and plan approval, expand to approximately 400
   primary-eligible examples.
6. **Working decision:** The full pilot may be roughly balanced for early
   review, but the primary-eligible subset can differ after policy exclusions.
   Neither balance estimates production prevalence.
7. **Working decision:** The owner approves material guide and adjudication
   decisions. Quality checks include label confidence, rationale code, guide
   version, privacy review, explicit hard-case marking, and accurate disclosure
   of assistant involvement in label review.
8. **Working decision:** The likely future advisory hook is immediately before
   existing upsert logic inside `MemoryService.persist_candidates()`.
9. **Working decision:** Initial integration is advisory-only, disabled by
   default, and preserves the integer return contract.
10. **Working decision:** Explicit owner save requests remain a policy override
    or separate evaluation group during initial advisory evaluation.
11. **Working decision:** Rank eligible models by mean cross-validation `keep`
    precision. A model is eligible only when its mean cross-validation `keep`
    recall is at least `0.50` during development.
12. **Working decision:** The precision/recall criterion is a research and
    model-selection rule. It is not a production-safety guarantee, deployment
    threshold, or authorization to filter memory automatically.
13. **Working decision:** The pilot's 30 `keep` / 30 `skip` balance is
    intentional for early experiments and does not estimate Gamma's production
    candidate prevalence.
14. **Working decision:** Concealed-label or delayed spot checks may be used to
    find ambiguity, but an assistant-assisted review is not independent
    self-relabel reliability evidence.
15. **Working decision:** A label that depends on whether equivalent memory is
    already stored requires existing-memory context and is not fully solvable
    by the text-only single-candidate task. Use
    `requires_existing_memory_context`, set `primary_eligible=false`, exclude
    those examples from primary training, cross-validation, held-out testing,
    and headline claims, and retain them only for a separate policy/limitations
    demonstration when useful.
16. **Working decision:** Production-like prevalence and probability
    calibration are deferred until a separate privacy-reviewed candidate sample
    exists.
17. **Working decision:** `primary_eligible=true` means a row is suitable for
    the single-candidate text-only task. `false` means the decision requires
    stored-memory context or another unavailable policy signal. Eligibility is
    separate from label confidence.
18. **Working decision:** Candidate wording must be understandable without the
    original conversation. A small varied set of vague `skip` examples remains
    eligible because missing context is itself a text-solvable triage failure.
19. **Working decision:** Known near duplicates share an opaque
    `paraphrase_group_id`, with at most one primary-eligible representative in
    a highly similar group.
20. **Working decision:** The 24-item pilot comparison is an assistant-assisted
    label review. Its agreement result can locate guide ambiguity but is not
    independent ground truth, model evaluation, reliability evidence, or proof
    of objective label correctness.
21. **Working decision:** An unscoped candidate may be `keep` only when it is a
    clearly reusable user preference or broadly safe cross-project rule.
    Project-specific architecture, tooling, reporting, and workflow rules must
    name the relevant system or project to be primary-eligible `keep` examples.

## Dataset Contract

The current canonical working primary-eligible dataset is
`research/cs4700_memory_triage/data/memory_triage_primary_dataset.csv`. Its
required fields are:

```text
example_id,source_record_id,candidate_text,label,candidate_type,candidate_source,
source_group_id,paraphrase_group_id,rationale_code,labeler_id,
label_confidence,guide_version,privacy_checked,primary_eligible,batch_id,
dataset_release,notes,split
```

Only `candidate_text` is the planned first-version model input. Other columns
support provenance, quality review, analysis, and group-aware splitting.
`primary_eligible=false` rows are policy-only and must be removed before
constructing primary development or final-test data.

Release `primary_v0_1` contains 107 primary-eligible rows: 55 `keep` and 52
`skip`. Every split is `unassigned`. The unchanged pilot v0.3 CSV remains the
guide-validation source, and Batch 01 drafts remain source-review provenance.
No training, tuning, split assignment, or evaluation has occurred. Because 50
rows are owner-reviewed assistant-assisted drafts, authoring-style bias remains
a known limitation.

## Pilot V0.3 Reconciliation And Freeze

The 24-item assistant-assisted pilot-label review was reconciled on 2026-07-03.
Exact agreement was 22/24 (91.7%). The two disagreements exposed missing guide
language about project/tool subject and scope. Because the review was
assistant-assisted, the result is useful for finding ambiguity but is not an
independent blinded self-relabel reliability study or proof of label quality.

The owner approved the subject-specificity rule in working decision 21. The
prior wording of `mtp_0009` and `mtp_0013` would be `skip` under that rule; the
two candidates were rewritten with explicit Gamma scope, retained as `keep`
and `primary_eligible=true`, and kept their historical IDs. The pilot remains
at 60 rows, 30 `keep` / 30 `skip`, 57 primary-eligible rows, and three
policy-only rows.

Pilot v0.3 is frozen as the labeling-guide validation milestone. It is not
large enough for final model training or final performance claims. It may guide
final-dataset expansion after the owner approves the practical plan in
[`final_dataset_expansion_plan.md`](../../research/cs4700_memory_triage/docs/final_dataset_expansion_plan.md).

## Evaluation Contract

The planned comparison includes:

1. rule/keyword baseline;
2. majority-class `DummyClassifier`;
3. TF-IDF plus Logistic Regression;
4. TF-IDF plus Linear SVM;
5. TF-IDF plus Decision Tree;
6. TF-IDF plus Random Forest.

Evaluation must use a final untouched test set, cross-validation on development
data, and model-specific hyperparameter searches. Exact duplicates,
paraphrases, and shared source templates must not cross splits. Prefer grouped
stratification when the available tooling supports it.

During development, first discard model configurations whose mean
cross-validation `keep` recall is below `0.50`. Rank the remaining configurations
by mean cross-validation `keep` precision. Report fold-level variation and all
other required metrics; do not describe this selection rule as production
validation.

Required reporting:

- precision, recall, F1, and support for both labels;
- accuracy and confusion matrix;
- cross-validation mean and variation;
- preferably PR-AUC and a precision-recall curve;
- threshold selection performed only on development data;
- qualitative error analysis, especially false `keep` decisions.

## Phases

1. **Current:** Expand and clean the 107-row working dataset toward the
   approximately 400-row primary-eligible target; approximately 145 additional
   `keep` and 148 additional `skip` examples remain planned.
2. **Completed 2026-07-03:** Reconciled the 24-item assistant-assisted review,
   clarified the subject/scope rule, resolved both disagreements, and froze
   pilot v0.3 as the guide-validation milestone.
3. **Completed 2026-07-03:** Migrated the 57 primary-eligible pilot rows and 50
   owner-approved Batch 01 rows into canonical working release `primary_v0_1`.
4. Author and review the remaining expanded dataset in batches.
5. Clean blanks, duplicates, paraphrases, and privacy failures, then freeze
   grouped development and untouched test assignments.
6. Implement exploration and non-ML baselines offline.
7. Train and tune the planned classifiers offline.
8. Evaluate once on the final test set and write the model/dataset reports.
9. Review privacy, retention, and explicit-save policy before any Gamma hook.
10. Add disabled advisory integration only in a separately approved runtime
   task.

## Non-Goals

- **Future work / intentionally out of scope:** Runtime classifier code,
  automatic memory filtering, a review API, a new database table, dashboard
  candidate controls, dependency changes, or model artifacts are not part of
  the current phase.
- **Future work / intentionally out of scope:** The classifier will not replace
  existing deduplication, contradiction handling, subject permissions, or
  explicit owner intent.

## Decision Log

- 2026-06-29: Locked binary individual-candidate classification.
- 2026-06-29: Chose high `keep` precision as the primary safety objective.
- 2026-06-29: Restricted v1 data to manually authored privacy-safe examples.
- 2026-06-29: Set pilot target near 60 and final target near 350-450.
- 2026-06-29: Selected offline evaluation first and advisory-only future
  integration at the shared persistence boundary.
- 2026-06-29: Set model selection to mean cross-validation `keep` precision
  subject to mean `keep` recall of at least `0.50`; this is not a deployment
  threshold.
- 2026-06-29: Fixed the blinded self-relabel check at 24 pilot examples after
  at least 48 hours.
- 2026-06-29: Excluded existing-memory-context cases from primary text-only
  scoring and deferred production prevalence/calibration work.
- 2026-06-29: Added `primary_eligible` as the explicit text-only task boundary
  and revised the 60-row pilot to v0.2 while preserving its 30/30 overall
  balance.
- 2026-06-29: Required at most one primary-eligible row per highly similar
  paraphrase group and moved the blinded 24-row check after v0.2 review.
- 2026-07-03: Reconciled the 24-item assistant-assisted pilot-label review at
  22/24 agreement; treated the result as ambiguity-finding evidence rather
  than independent reliability evidence.
- 2026-07-03: Added guide v0.3's owner-approved subject-specificity rule,
  rewrote `mtp_0009` and `mtp_0013` with explicit Gamma scope, preserved their
  historical IDs and `keep` labels, and froze the pilot as the guide-validation
  milestone.
- 2026-07-03: Owner approved Batch 01 migration; created canonical working
  release `primary_v0_1` with 107 primary-eligible, unassigned rows while
  retaining the pilot and draft sources as provenance.

## Open Questions

- **Open question:** After the dataset expands, what group-aware holdout size
  provides enough per-class final-test examples without weakening development
  cross-validation?
- **Open question:** What tie-breaker should be used when eligible models have
  practically equivalent mean cross-validation `keep` precision?
