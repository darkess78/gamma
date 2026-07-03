# CS 4700: Gamma Memory Triage

Status: Offline planning and provisional pilot v0.2 review
Last verified: 2026-06-29

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
5. **Working decision:** Begin with an approximately 60-example pilot. Expand
   to approximately 350-450 examples only after the labeling guide is reviewed.
6. **Working decision:** The full pilot may be roughly balanced for early
   review, but the primary-eligible subset can differ after policy exclusions.
   Neither balance estimates production prevalence.
7. **Working decision:** The owner is the primary labeler. Quality checks must
   include a delayed blinded self-relabel subset, label confidence, rationale
   code, guide version, privacy review, and explicit marking of hard cases.
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
14. **Working decision:** After the revised pilot is complete and reviewed the
    owner will blindly self-relabel 24 of the 60 pilot examples after a delay
    of at least 48 hours.
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

## Dataset Contract

The pilot schema is maintained at
`research/cs4700_memory_triage/data/memory_triage_pilot.csv`. Required fields:

```text
example_id,candidate_text,label,candidate_type,candidate_source,
source_group_id,paraphrase_group_id,rationale_code,labeler_id,
label_confidence,guide_version,privacy_checked,primary_eligible,notes,split
```

Only `candidate_text` is the planned first-version model input. Other columns
support provenance, quality review, analysis, and group-aware splitting.
`primary_eligible=false` rows are policy-only and must be removed before
constructing primary development or final-test data.

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

1. **Current:** Review the labeling guide and provisional v0.2 60-example
   pilot. The pilot has 30 `keep`, 30 `skip`, 57 primary-eligible rows, and
   three policy-only rows.
2. After the completed v0.2 review wait at least 48 hours and blindly
   self-relabel 24 selected examples.
3. Clean blanks, duplicates, paraphrases, label disagreements, and privacy
   failures.
4. Freeze grouped development and untouched test assignments.
5. Implement exploration and non-ML baselines offline.
6. Train and tune the planned classifiers offline.
7. Evaluate once on the final test set and write the model/dataset reports.
8. Review privacy, retention, and explicit-save policy before any Gamma hook.
9. Add disabled advisory integration only in a separately approved runtime
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

## Open Questions

- **Open question:** After the dataset expands, what group-aware holdout size
  provides enough per-class final-test examples without weakening development
  cross-validation?
- **Open question:** What tie-breaker should be used when eligible models have
  practically equivalent mean cross-validation `keep` precision?
