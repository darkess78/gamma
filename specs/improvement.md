# Gamma Improvement System

Status: Current evaluation and isolated-experiment foundation
Last verified: 2026-08-27

## Purpose

Gamma may continuously observe its own reliability, latency, quality, safety,
and resource behavior. It may create isolated improvement candidates only
after the evaluation foundation can establish a representative baseline and
reject regressions. Continuous improvement means continuous observation and
controlled experimentation, not unattended mutation of the running checkout.

## Boundary

The observer is read-only with respect to Gamma source and runtime
configuration. The fixture runner calls Shana's explicit evaluation mode and
writes sanitized artifacts to a new operator-selected runtime directory.
Evaluation mode preserves the owner/persona prompt context while disabling
continuity writes, memory writes, tool execution, assistant-emotion updates,
Presence activity updates, and production timing-log writes.

The optional proposal analyst sends only aggregate metrics, cohorts,
opportunities, and warnings to an LLM. It has no tools. Proposal analysis is
restricted to a loopback or private-address local provider by default; sending
the aggregate to a hosted provider requires explicit authorization. The model
chooses metric IDs, but deterministic code binds the stored statistic, value,
sample count, and observation digest. Raw model responses and conversation
previews are not written to proposal artifacts.

The deterministic reviewer classifies measurement proposals, unsupported
causal claims, and direct-change ideas. A direct-change idea based only on
aggregate evidence is routed to source-code grounding. Independent models can
form consensus about what to inspect, but consensus is not authorization to
edit. The current implementation does not:

- grant proposal, grounding, or candidate models tools or command execution
- edit the live checkout or run services or deployments on a model's authority
- promote, revert, commit, deploy, or publish a candidate
- read or emit conversation preview text

Reports are written only when the operator supplies an explicit output path.
Detached experiment worktree preparation exists but is disabled by the
contract by default. When that policy is explicitly enabled, a candidate model
can return bounded exact-text replacements only for source included in a
grounded plan. Deterministic code binds every replacement to the experiment,
baseline commit, plan hash, source hash, cited line span, allowed path set, file
limit, edit limit, and wall-clock limit before applying it to the detached
worktree. Ambiguous, overlapping, stale, out-of-scope, and syntax-invalid edits
are rejected. Raw model replies are not persisted.

Candidate tests also remain behind the disabled experiment policy. Fixed
safety/privacy and full Pytest profiles run without a shell inside Bubblewrap,
a transient user cgroup, and process resource limits. The candidate checkout is
read-only during execution; only an ephemeral in-memory `data/` tree and `/tmp`
are writable. The host home, local configuration, network, GPU devices, and
ambient environment are absent. Passing these profiles proves only the
`automated_tests` and `safety_privacy` gates; it does not imply diff approval,
holdout success, health/soak success, rollback readiness, or owner approval.

## Improvement Contract

`config/improvement.toml` is the versioned machine-agnostic contract. It owns:

- bounded source-record limits
- objective, guardrail, and diagnostic metrics
- the selected distribution statistic
- minimum representative sample counts
- minimum practical improvement thresholds
- maximum regression budgets
- evidence required for each change class

Metric thresholds are policy, not discovered facts. Adjusting the contract is
a reviewed behavior change and must not be used to make a failing candidate
pass. Candidate authors must not receive hidden holdout fixtures or alter the
baseline after seeing candidate results.

## Change Classes

1. `runtime_adaptation`: bounded and reversible runtime selection or tuning.
2. `tracked_configuration`: portable configuration changes with a soak gate.
3. `behavior_or_code`: source, skill, prompt, memory-ranking, or policy changes.
4. `restricted_operation`: dependencies, databases, authentication, network,
   services, security, protected files, destructive work, or deployment.

Restricted operations are always manual even when all supplied evidence
passes. Behavior or code changes require recorded owner approval. Passing the
evaluator means a candidate is eligible for promotion review; it is not itself
authority to commit, merge, restart, deploy, or publish anything.

The scoring contract, fixture catalog, improvement implementation and tests,
persona/safety/user policy, machine-local configuration, and protected network
specification are outside the automated experiment mutation surface.

## Current Metrics

The first contract consumes existing `conversation.timings.jsonl` and
`llm.routes.jsonl` records plus sanitized `fixture.results.jsonl` artifacts.
Conversation p95 total latency is the initial objective. Prompt-context
assembly, draft-request assembly, full routed draft time, complete draft time,
metadata, memory, tools, finalization, TTS, and routed-call duration are
diagnostics. Live-voice total, STT, conversation, TTS, and time-to-first-audio
distributions are also diagnostic. Route failure percentage plus fixture
quality, safety, privacy, and reliability rates are guardrails. Reports contain
aggregates and record counts only. The observer calls out slow first audio,
dominant live-voice stages, route cohorts, fallback use, and draft substage
dominance without inferring causation from mixed traffic.

The contract will later add independently measured scorecards for:

- time to first text and cancellation latency
- persona consistency and ordinary conversation reliability
- memory relevance, contradiction, duplication, privacy, and token cost
- skill completion, tool-call efficiency, boundedness, and fixture compliance
- StreamBrain replay decisions, output invariants, and moderation safety
- CPU, RAM, GPU, disk, provider cost, and cold/warm behavior

Latency cannot compensate for a safety, privacy, correctness, persona, or
reliability regression.

## Evaluation Decisions

An isolated candidate is promotion-eligible only when:

- baseline and candidate both meet every required sample minimum
- at least one objective reaches its practical improvement threshold
- no objective or guardrail exceeds its regression budget
- every gate for its change class has externally produced evidence
- human approval is present when required
- the operation is not in the restricted class

Diagnostic metrics explain changes but do not independently block promotion.
Candidate results are `promote_candidate`, `reject_candidate`,
`needs_more_evidence`, or `manual_only`.

## CLI

Read the current aggregate baseline:

```bash
.venv/bin/python -m gamma.improvement.cli observe \
  --runtime-dir data/runtime
```

Run the tracked fictional fixture catalog against an already-running Shana:

```bash
.venv/bin/python -m gamma.improvement.cli run-fixtures \
  --output-runtime-dir data/improvement/runs/baseline-warm/runtime \
  --thermal-state warm \
  --repetitions 3
```

`cold`, `warm`, and `unknown` are operator assertions. The runner does not
silently label the first request cold because model residency must be
controlled outside the benchmark process.

Required and forbidden text invariants are compared after Unicode compatibility
normalization, case folding, whitespace folding, and common smart-quote/dash
normalization. This prevents typography-only model output differences from
creating false failures without weakening the configured semantic terms.

Compare two isolated snapshot directories:

```bash
.venv/bin/python -m gamma.improvement.cli compare \
  --baseline-runtime-dir data/improvement/baseline/runtime \
  --candidate-runtime-dir data/improvement/candidate/runtime \
  --change-class behavior_or_code \
  --evidence data/improvement/candidate/evidence.json \
  --fail-if-blocked
```

An evidence document contains `passed_gates`, `human_approved`, and an optional
`approval_reference`. A future experiment runner must generate gate evidence
from command results and immutable artifacts rather than trusting model prose.

Generate proposal-only hypotheses with one or more local models:

```bash
.venv/bin/python -m gamma.improvement.cli propose \
  --runtime-dir data/runtime \
  --output data/improvement/proposals/local.json \
  --model gpt-oss:20b \
  --model qwen3.8:27b
```

The parser accepts bounded JSON fragments and a small set of shape variations,
but metric IDs still have to exist, paths must be grounded in the repository,
and evidence values always come from the observer. Rejections retain only safe
field names, contract-known metric references, and validation codes.

Screen proposals and calculate independent-model consensus:

```bash
.venv/bin/python -m gamma.improvement.cli review-proposals \
  --runtime-dir data/runtime \
  --proposals data/improvement/proposals/local.json \
  --output data/improvement/proposals/local.review.json
```

Review results are `manifest_candidate`, `needs_revision`, or `rejected`.
Consensus next actions are limited to `manifest_planning` and `code_grounding`;
neither action edits a file.

Create a read-only source artifact for one code-grounding target:

```bash
.venv/bin/python -m gamma.improvement.cli ground-source \
  --path src/gamma/conversation/service.py \
  --target-metric conversation.draft_reply_ms \
  --target-metric conversation.total_ms \
  --output data/improvement/grounding/conversation.json
```

The artifact contains the exact file hash, Python symbols, bounded call names,
timing keys, and metric-reference lines. It contains no runtime prompts,
replies, secrets, or local configuration. Grounding is limited to readable
Python source inside the proposal path policy, with a bounded file count and
size. Protected improvement-control paths cannot be grounded for automated
mutation.

Ask up to three local models for source-cited plans:

```bash
.venv/bin/python -m gamma.improvement.cli ground-plan \
  --runtime-dir data/runtime \
  --proposals data/improvement/proposals/local.json \
  --proposal-hash <sha256> \
  --grounding data/improvement/grounding/conversation.json \
  --output data/improvement/grounding/conversation.plans.json \
  --model gpt-oss:20b \
  --model qwen3.8:27b
```

Before each call, Gamma rechecks every source hash and sends only relevant
structural facts plus bounded, line-numbered excerpts read from that verified
source. Excerpts are not persisted in the grounding artifact. A grounded plan
must cite an existing pinned symbol and an in-range line span for every allowed
path. `needs_more_source` is a valid fail-closed result. Generated draft or
response caching is rejected because it can replay stale persona, memory,
tool, or nondeterministic output. Grounded plans still have `grounding_only`
authority and cannot create a worktree or edit a file.

## Disabled Candidate Workflow

Candidate authoring is available only after an operator changes the tracked
`isolated_experiments_enabled` policy and creates a pinned detached worktree.
The normal sequence is:

```bash
.venv/bin/python -m gamma.improvement.cli plan-experiment \
  --id conversation-latency-001 \
  --hypothesis "Reduce grounded conversation latency without safety regressions." \
  --domain conversation \
  --change-class behavior_or_code \
  --baseline-commit <full-commit> \
  --allow-path src/gamma/conversation/service.py \
  --state-root data/improvement/experiments

.venv/bin/python -m gamma.improvement.cli prepare-experiment \
  --id conversation-latency-001 \
  --state-root data/improvement/experiments

.venv/bin/python -m gamma.improvement.cli draft-candidate \
  --id conversation-latency-001 \
  --state-root data/improvement/experiments \
  --grounding data/improvement/grounding/conversation.json \
  --grounded-plans data/improvement/grounding/conversation.plans.json \
  --output data/improvement/experiments/conversation-latency-001/candidates.json \
  --model gpt-oss:20b --model qwen3.8:27b

.venv/bin/python -m gamma.improvement.cli apply-candidate \
  --id conversation-latency-001 \
  --state-root data/improvement/experiments \
  --grounding data/improvement/grounding/conversation.json \
  --grounded-plans data/improvement/grounding/conversation.plans.json \
  --candidates data/improvement/experiments/conversation-latency-001/candidates.json \
  --receipt data/improvement/experiments/conversation-latency-001/receipt.json

.venv/bin/python -m gamma.improvement.cli validate-candidate \
  --id conversation-latency-001 \
  --state-root data/improvement/experiments \
  --receipt data/improvement/experiments/conversation-latency-001/receipt.json \
  --output data/improvement/experiments/conversation-latency-001/validation.json
```

`draft-candidate` may ask up to three explicitly selected models for independent
drafts. `apply-candidate` applies one selected draft, produces a hash-only
receipt, and advances the manifest only to `candidate_ready`.

## Isolated Experiment Manifests

Experiment manifests are proposal-only. They normalize and constrain allowed
paths and reject `.env`, local machine configuration, the locked deployment
specification, evaluator/control-plane code, all test code, fixture/scoring
inputs, persona/safety/authentication/user policy, runtime data, Git internals,
virtual environments, generated bytecode, dependency trees, private keys, and
the stale top-level `gamma/` directory. Changed paths must remain within the
manifest and under its file limit.

The manifest store enforces forward-only state transitions and append-only
audit events. It cannot transition an experiment to `promoted`; promotion is
intentionally not implemented. A future promotion component must consume an
eligible candidate evaluation, immutable validation evidence, current baseline
identity, rollback evidence, and fresh owner approval.

## Future Stages

Delivered foundations include aggregate and cohort observation, baseline
comparison, an initial fictional conversation catalog, state-isolated
evaluation requests, sanitized run artifacts, fixture guardrails, local
multi-model proposal analysis, deterministic evidence binding and screening,
independent-model consensus, pinned source grounding, local source-cited plans,
proposal manifests, scope validation, disabled-by-default detached candidate
authoring, exact-edit application receipts, and sandboxed fixed regression
profiles. Next stages are:

1. Calibrate and expand representative everyday conversation and voice fixtures.
2. Extend instrumentation and scorecards to memory, skills, brain,
   safety, resources, and cold/warm latency.
3. Add deterministic holdout, soak, health, and rollback evidence capture.
4. Add a bounded coordinator that can create a fresh experiment for a revised
   attempt while preserving attempt limits and every prior artifact.
5. Add owner-facing review and explicit promotion/rollback controls.
6. Consider automatic promotion only for narrow reversible runtime adaptations
   after repeated shadow and canary evidence.

The stable deterministic model router remains the fallback. No improvement
agent may weaken authentication, safety, privacy, approval, or deployment
boundaries to improve a score.
