# Final Dataset Expansion Plan

Status: Initial owner-approved expansion target complete; 400 primary-eligible rows migrated

Guide version: `v0.3`

## Purpose And Target

The owner-approved plan expanded the frozen 60-row pilot into 400
primary-eligible, manually and assistant-assisted authored examples for the
single-candidate `keep`/`skip` research task. The completed corpus contains
exactly 200 examples per class. This balance supports future model
development and error analysis; it does not estimate Gamma's production
candidate prevalence or imply calibrated runtime probabilities.

The pilot validates the labeling guide but is not large enough for final model
training or final performance claims. Batch 07 supplied the final 43 rows—20
`keep` and 23 `skip`—bringing the canonical dataset to 200 `keep` and 200
`skip`. No numerical gap remains for the original target. No data split,
notebook execution, or model or runtime work has begun.

## Planned Primary Composition

The category counts below were planning targets, not quotas that justified
weak or repetitive examples. The completed initial corpus reached the overall
400-row and exact 200/200 class targets; these historical category targets do
not assert a final category distribution.

### Keep: approximately 200 examples

| Category | Target | Coverage |
| --- | ---: | --- |
| Preferences and communication constraints | 45 | Response structure, accessibility, units, tone, explanation depth, and durable dislikes. |
| Reusable workflow rules | 40 | Review order, approval steps, testing habits, reporting routines, and reversible-action preferences. |
| Security and project constraints | 40 | Broadly safe rules plus clearly named system/project architecture, tooling, privacy, and compatibility constraints. |
| Ongoing project facts | 30 | Scoped objectives, adopted designs, active multi-stage work, and durable corrections. |
| Stable relationships or roles | 20 | Safe role-based collaborators, reviewers, mentors, and recurring responsibilities without real identities. |
| Milestones and ongoing commitments | 25 | Meaningful completed baselines, durable decisions, recurring deliverables, and continuing obligations. |

Project-specific `keep` candidates must follow guide v0.3 by naming the
relevant system or project. Unscoped candidates are acceptable only for clearly
reusable user preferences or broadly safe cross-project rules.

### Skip: approximately 200 examples

| Category | Target | Coverage |
| --- | ---: | --- |
| Temporary plans | 35 | Same-session intentions, short-lived schedules, immediate needs, and expiring setup details. |
| One-time events and incidents | 35 | Routine completions, isolated failures, incidental choices, and events without future value. |
| Casual reactions and acknowledgements | 25 | Thanks, greetings, laughter, artifact reactions, and non-durable feedback. |
| One-draft or one-task instructions | 35 | Reply formatting, temporary tone, document styling, and task-local constraints. |
| Vague or missing-context statements | 25 | Missing referents, unnamed options, incomplete explicit saves, and fragments that cannot stand alone. |
| Superseded or speculative information | 30 | Obsolete facts, rejected designs, unapproved proposals, guesses, and conditional ideas. |
| Difficult borderline cases | 15 | Important-sounding but temporary facts, ambiguous duration, and durable-looking text that fails specificity or behavioral value. |

## Authoring Against Lexical Shortcuts

- Write durable examples that do not depend on words such as “always,”
  “ongoing,” “must,” or “remember.”
- Write `skip` examples that initially sound important, future-facing, or
  preference-like but are limited by scope, duration, approval state, or
  missing context.
- Cover similar concepts with different syntax, vocabulary, sentence length,
  and candidate style.
- Do not mass-produce template paraphrases or change only a noun, date, or
  adjective to create another row.
- Review category balance and simple keyword cues after each batch; use those
  checks to revise authoring, not to manufacture easy labels.

## Source And Paraphrase Grouping

- Assign every example an opaque `source_group_id` and
  `paraphrase_group_id` during authoring.
- Put related examples, common source concepts, and paraphrases in one group,
  including cross-label contrasts when they are intentionally related.
- No related source or paraphrase group may cross development, validation, or
  final-test boundaries.
- Keep highly similar paraphrases sparse. A group must not be expanded merely
  to increase row count.
- Keep `primary_eligible=false` policy examples separate from primary training,
  cross-validation, held-out evaluation, and headline metrics. They may remain
  in a clearly flagged policy artifact or policy-only portion of the dataset.

## Recommended Split Process

1. Draft all planned batches and complete label, privacy, source-group, and
   paraphrase-group review before assigning any split.
2. Remove or rewrite privacy failures, exact duplicates, accidental near
   duplicates, and guide-inconsistent rows.
3. Freeze an untouched, group-aware final test set before fitting TF-IDF,
   evaluating rules, selecting features, tuning models, or choosing a decision
   threshold. A practical starting recommendation is about 20% of the primary
   rows, subject to group and class feasibility.
4. Use the remaining approximately 80% as the development set for grouped
   cross-validation, model comparison, and tuning. Cross-validation folds act
   as train/validation partitions.
5. Preserve class balance as closely as practical in the development set,
   folds, and final test set without splitting or leaking any group.
6. Record and freeze the split manifest before notebook training work is
   enabled. Evaluate the final test set once after development decisions are
   complete.

## Author-Review Checkpoints

1. **First expanded batch review:** Author approximately 50-75 primary rows,
   then check category coverage, subject specificity, confidence, and whether
   guide v0.3 is producing repeatable decisions before scaling further.
2. **Duplicate and near-duplicate audit:** Run normalized exact checks,
   similarity suggestions, and manual group review after each batch and again
   after drafting is complete.
3. **Privacy review:** Confirm every row is newly authored and contains no raw
   conversation content, real private identities, secrets, local paths, logs,
   runtime data, or copied stored memory.
4. **Guide-consistency spot check:** Review a varied concealed-label sample,
   document assistant involvement, and adjudicate disagreements without
   presenting the result as independent reliability evidence.
5. **Final holdout freeze:** Approve class/group allocation, write the immutable
   split manifest, and prohibit test-guided feature or threshold changes.
6. **Training gate:** Only after the preceding checkpoints may the notebook's
   expansion and training gates be enabled in a separate task.

## Unresolved Owner Decisions

- Decide whether policy-only examples remain in the primary CSV with
  `primary_eligible=false` or move to a separate policy-demonstration file.
- Approve the recommended approximately 20% group-aware final holdout after
  actual group sizes are known.

These decisions do not alter the completed initial expansion milestone. They
must be settled before their corresponding storage or split-freeze step. Any
expansion beyond 400 rows requires separately scoped planning and owner
approval; this plan does not define a future batch.

This documentation update does not authorize model work, split assignment,
notebook execution, runtime work, staging, committing, or pushing. Each such
action requires separate authorization where applicable.
