# LLM Router

## Status

Gamma currently has a narrow deterministic router:
- central adapter-level routing
- explicit call-purpose routing for conversation draft, metadata extraction, tool finalization, vision analysis, and live voice helper passes
- optional hosted escalation for heavier normal conversation turns

This document describes the deeper router direction that may be added later. It is a future-work spec, not a statement that all items below are implemented today.

## Goals

- Reduce latency for live voice and lightweight assistant work.
- Keep strong models available for heavier reasoning, planning, coding, and multimodal tasks.
- Control cost and external dependency usage instead of always sending every turn to the strongest provider.
- Keep routing policy centralized and observable.
- Preserve assistant personality/style consistency even when multiple models are involved.

## Non-Goals

- Do not make an LLM choose the LLM on every request by default.
- Do not bury routing rules separately inside each provider adapter.
- Do not optimize only for benchmark quality while ignoring latency variance.
- Do not allow helper models to replace the main assistant orchestration role.

## Current Baseline

The current narrow router is intended to stay simple:
- `metadata_extraction` prefers a local tagging model
- `voice_reply_planner` and `voice_sentence_generator` prefer local worker models
- `tool_finalizer` prefers a local light model
- short/fast turns prefer a local light model
- image requests route to a vision-capable provider
- heavier draft turns may optionally escalate to a hosted provider

This baseline should remain the stable fallback even if more advanced routing is added later.

## Target Architecture

### Core pieces

- `RouterLLMAdapter`: single entry point for per-call routing policy
- `RouteDecision`: provider, model, reason, and policy metadata
- explicit `LLMCallContext`: purpose, latency hints, modality, and other routing inputs
- provider adapters that stay focused on transport/API behavior only

### Recommended routing layers

1. Hard constraints
   - vision required
   - provider availability
   - safety/format requirements
   - owner-only or local-only policy restrictions
2. Task classification
   - draft reply
   - metadata extraction
   - tool finalization
   - memory helper work
   - safety review
   - live voice planning
   - live voice sentence generation
   - coding/debug/planning analysis
3. Latency and cost policy
   - fast path
   - balanced path
   - heavy reasoning path
4. Fallback policy
   - local strong model
   - hosted strong model
   - safe degraded path

## Future Route Inputs

The deeper router should consider more than prompt length:
- call purpose
- input word count
- image presence
- live voice mode
- recent tool usage
- memory lookup requirements
- explicit user request for depth
- user language
- recent provider failures/timeouts
- current local provider health
- queue depth or local GPU saturation
- budget mode or cost policy

## Future Route Outputs

A more complete route decision should include:
- provider
- model
- route family
- reason
- expected latency tier
- expected cost tier
- retry/fallback chain
- logging/debug fields

## Planned Upgrades

### 1. Better task typing

Expand route purposes so Gamma can treat these as distinct workloads:
- normal conversation
- heavy reasoning
- coding/debug assistance
- structured extraction
- memory summarization
- memory cleanup/tagging
- safety review
- document/image reading
- live voice micros

### 2. Borderline-case classifier

If deterministic rules are not enough, add a very small local classifier only for borderline turns.

Requirements:
- local-first
- fast enough to avoid defeating the value of routing
- optional, not mandatory for every turn
- easy to disable

This classifier should answer a narrow question like:
- lightweight vs heavy
- local-safe vs hosted-needed

It should not generate freeform routing policy.

### 3. Provider health-aware routing

The router should eventually react to runtime status:
- if local Ollama is unhealthy, skip local routes
- if hosted provider auth is missing, skip hosted escalation
- if vision model is unavailable, fail over cleanly
- if repeated timeouts occur, temporarily demote that route

### 4. Style consistency controls

Cross-model routing can cause inconsistent tone. Future work should include:
- shared response style constraints
- tighter cleanup/finalization rules
- optional style-normalization pass only when needed
- route restrictions for persona-critical turns

### 5. Cost and mode profiles

Add top-level policy profiles such as:
- `local_only`
- `balanced`
- `low_latency_voice`
- `high_quality`
- `offline_safe`

These profiles should change routing thresholds without changing app code.

### 6. Observability

Every routed call should eventually be inspectable in logs or dashboard state:
- purpose
- chosen provider/model
- reason
- latency
- fallback used or not
- route failures

Without this, router debugging will become painful quickly.

## Suggested Config Direction

Possible future config additions:
- `llm_router_mode`
- `llm_router_log_decisions`
- `llm_router_profile`
- `llm_router_local_only`
- `llm_router_classifier_enabled`
- `llm_router_classifier_model`
- `llm_router_provider_fail_open`
- `llm_router_persona_sensitive_model`
- `llm_router_voice_fast_model`
- `llm_router_reasoning_model`

These should remain machine-agnostic in shared defaults, with machine-specific overrides in local config files.

## Risks

- Too many routing branches will make behavior hard to reason about.
- A classifier call can cost more latency than it saves.
- Cross-provider routing can make persona/tone drift more visible.
- Hidden fallback behavior can create confusing debugging sessions.
- If routing policy and adapter behavior drift apart, bugs will be subtle.

## Constraints

- The router must stay cross-platform.
- The router must not assume Windows-only paths or launch behavior.
- Shared runtime routing logic must be pure Python and provider-agnostic.
- Provider-specific shell/process management belongs elsewhere.

## Recommended Next Steps

1. Keep the current deterministic router as the default path.
2. Add route-decision logging.
3. Add provider-health-aware routing.
4. Add policy profiles for local-only vs balanced vs low-latency voice.
5. Only then consider a small local classifier for borderline cases.

## Implementation Rule

The primary assistant model remains the orchestrator.
Helper or worker models may support it, but they should not become the center of the architecture.
