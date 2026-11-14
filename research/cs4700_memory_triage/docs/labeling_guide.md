# Memory Triage Labeling Guide

Status: Guide `v0.3`; pilot validation milestone frozen
Decision owner: Owner
Pilot review method: Assistant-assisted

## Unit Of Labeling

Label one standalone memory-candidate text. Judge whether the information is
likely to help Gamma in a separate future conversation, not merely later in the
same exchange.

Use exactly one provisional label:

- `keep`
- `skip`

If the decision is difficult, still choose a provisional label but set
`label_confidence` to `low` and explain the uncertainty in `notes`. Do not make
an uncertain case look authoritative.

## Primary-Task Eligibility

Set `primary_eligible` independently from the `keep` or `skip` label:

- `true`: the candidate can be judged from its text alone under this guide and
  is suitable for the planned primary text-only training and evaluation task;
- `false`: the correct policy decision requires the current stored-memory
  state or another signal that is unavailable to a single-candidate text
  classifier, or the example otherwise falls outside the defined task.

Rows marked `false` are policy and limitations examples. They must not enter
primary training, cross-validation, held-out testing, or headline metric
claims. A difficult but text-solvable example remains `true` and uses
`label_confidence` and `notes` to record uncertainty.

## Keep

Choose `keep` when the candidate is understandable on its own and expresses
information with durable future value.

Common inclusion rules:

- lasting preferences or dislikes;
- stable accessibility or communication preferences;
- an ongoing project objective, durable project fact, or reusable constraint;
- a recurring workflow or rule;
- a stable relationship fact that is safe and useful;
- an enduring boundary or permission rule;
- a meaningful milestone likely to matter in later conversations;
- a durable correction to something Gamma might otherwise get wrong.

Examples:

- “The user prefers concise status updates with the blocker listed first.”
- “Gamma must keep the Shana API and dashboard as separate processes.”
- “Ask before sending messages to external services.”
- “The user uses captions for all spoken output.”

## Skip

Choose `skip` when the candidate is transient, low-value, unclear, redundant,
or dependent on context that a future conversation will not have.

Common exclusion rules:

- current mood, hunger, fatigue, weather, or short-lived state;
- acknowledgements, laughter, greetings, or casual reactions;
- one-off scheduling details that have already passed;
- vague references such as “that one,” “the thing,” or “it worked”;
- current-turn instructions that are not standing rules;
- obsolete or explicitly superseded information;
- assistant reaction text rather than a fact about the user/project;
- speculative guesses presented without durable evidence.

An otherwise durable candidate that duplicates an already stored fact can be a
policy-level `skip`, but that case must use
`requires_existing_memory_context` and `primary_eligible=false`; it is not a
primary text-only exclusion rule.

Examples:

- “The user is tired tonight.”
- “That sounds great.”
- “Use the second option for this response.”
- “The old temporary server is active until this afternoon.”

## Tie-Breaking Rules

1. **Separate-conversation test:** Would this still make sense and be useful if
   shown without the surrounding transcript next month? If not, prefer `skip`.
2. **Duration test:** Information expected to expire quickly is usually `skip`.
3. **Specificity test:** A durable but incomprehensible fragment is `skip`.
4. **Behavioral-value test:** Prefer `keep` when the fact will reliably improve
   future assistance, not merely because it is interesting.
5. **Explicit-save test:** “Remember this” is evidence of intent, not automatic
   proof of durable value. Explicit owner saves are tracked separately because
   later policy may override the classifier.
6. **Safety test:** Unsafe/private content is not labeled `skip`; remove it from
   the dataset and record a privacy failure outside the training file.
7. **Duplicate test:** A text-only model cannot know the current memory store.
   Use `requires_existing_memory_context` when the label depends on an
   equivalent stored fact and set `primary_eligible=false`. Exclude those
   examples from primary training and all primary score or final-test claims;
   they may support a separate policy/limitations demonstration.
8. **Outdated test:** Explicitly outdated or superseded facts are `skip` unless
   the history itself is durably relevant and clearly stated.

## Borderline Cases

### Ongoing but not permanent

An active multi-week project objective can be `keep`; a task expected to end
today is usually `skip`. Mark low confidence if duration is not stated.

### Dates and events

Recurring anniversaries and meaningful completed milestones can be `keep`.
One-time appointments or expired deadlines are usually `skip`.

### Preferences that may be temporary

“Prefers dark themes” is likely `keep`. “Wants dark mode for this screenshot”
is `skip`. If wording omits the duration, use confidence and notes.

### Negative constraints

Stable boundaries such as “never publish drafts automatically” are `keep`.
One-turn constraints such as “do not summarize this message” are `skip`.

### Candidate formatting

Some Gamma episodic candidates may contain both user and assistant text. Label
the durable information, but mark candidates dominated by assistant wording as
`skip` or low confidence.

### Project or tool subject specificity

An unscoped candidate may be `keep` when it expresses a clearly reusable user
preference or a broadly safe cross-project rule. For example:

- `keep`: “Warn the user before starting a long-running training job.”
- `keep`: “Keep machine-specific credentials outside tracked configuration.”

Project-specific architecture, tooling, reporting, and workflow rules must
name the relevant system or project to be primary-eligible `keep` examples.
Durability alone does not repair an ambiguous subject. For example:

- `skip` as written: “The application must keep its services separate.”
- `skip` as written: “The reporting tool must preserve its response schema.”
- `keep` after scoping: “For Gamma's status-reporting API, preserve existing
  response schemas unless an approved migration is planned.”

This owner-approved clarification resolves the subject/scope ambiguity found
during the assisted pilot-label review and defines guide version `v0.3`.

## Rationale Codes

Recommended `keep` codes:

- `durable_preference`
- `stable_project_fact`
- `reusable_rule`
- `recurring_constraint`
- `stable_relationship`
- `accessibility_preference`
- `durable_correction`
- `meaningful_milestone`
- `ongoing_commitment`

Recommended `skip` codes:

- `temporary_state`
- `casual_reaction`
- `context_dependent`
- `current_turn_only`
- `one_off_detail`
- `outdated_or_superseded`
- `requires_existing_memory_context`
- `assistant_reaction`
- `speculative_or_unclear`

## Phrasing And Shortcut Checks

Duration words are valid evidence but must not determine the dataset by
themselves. During review:

- retain durable candidates that do not say `always`, `ongoing`, or similar
  permanence words;
- retain non-durable candidates that use preference or future-sounding
  language but are limited to one artifact, reply, trial, or unapproved idea;
- keep a small varied set of vague candidates because missing context is a real
  triage failure mode, but do not let them dominate `skip`;
- inspect simple keyword-baseline errors before interpreting learned-model
  performance.

Known paraphrases share a `paraphrase_group_id`. A highly similar group may
contain at most one `primary_eligible=true` row. Policy-only variants can stay
in that group to demonstrate information unavailable to the text-only task.

## Confidence

- `high`: The guide clearly determines the label.
- `medium`: Reasonable label, but wording or duration leaves some uncertainty.
- `low`: Genuine boundary case requiring owner adjudication or separate-policy
  treatment.

## Quality Procedure

1. Draft examples under the current guide and preserve an auditable author and
   labeler identifier.
2. Have the owner approve material guide decisions and adjudicate flagged
   examples before a dataset freeze.
3. Privacy-check every row independently of its class label.
4. Use delayed or concealed-label spot checks as ambiguity-finding tools, and
   describe any assistant involvement accurately.
5. Record agreement, review disagreements, and avoid presenting an assisted
   review as independent reliability evidence.
6. Increment `guide_version` when a rule changes materially.
7. Re-review earlier rows affected by a guide revision.

The 24-item assistant-assisted pilot-label review was reconciled on 2026-07-03
with 22 exact agreements (91.7%). Its two disagreements exposed the missing
project/tool subject-specificity rule. The result is useful evidence that the
guide needed clarification, but it is not an independent blinded self-relabel
reliability study or proof of label quality. The owner approved the rule above
and the two affected rewrites for `v0.3`. Pilot `v0.3` is frozen as a
labeling-guide validation milestone, not as a final training dataset.
