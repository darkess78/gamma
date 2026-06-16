# Resource-Aware Model Routing Proposal

## Status

This is a future-work architecture proposal. It does not describe implemented
behavior and does not authorize automatic model eviction, service migration, or
machine-local configuration changes.

Gamma already has several pieces that can support resource-aware placement:

- deterministic LLM request routing in `RouterLLMAdapter`
- dashboard GPU metrics collected through `nvidia-smi`
- device selection helpers for Torch workloads
- explicit device configuration for STT, Qwen TTS, and audio understanding
- provider health checks and structured route logging

Those pieces currently make independent decisions. The goal is to add a shared
resource view and placement policy without turning the LLM router into a
general-purpose process supervisor.

## Decision

Keep the existing LLM router and add a separate shared resource placement
service.

The two components answer different questions:

1. The LLM router selects a provider, model, and configured runtime endpoint for
   a request.
2. The resource placement service determines which known runtime targets can
   safely accept a workload and records temporary resource reservations.

Other model-backed services, including STT, TTS, and audio understanding, should
use the placement service directly rather than routing through the LLM router.

## Important Runtime Constraint

A request sent to one Ollama endpoint cannot reliably select a physical GPU for
that request. GPU placement is owned by the Ollama process and its environment.

Reliable endpoint-aware placement therefore requires one of these arrangements:

- multiple preconfigured Ollama instances, each pinned to a GPU and exposed on
  a distinct internal endpoint
- an external model runtime that exposes supported placement controls
- a later Gamma-owned runtime manager that starts pinned instances outside
  request handlers

The first implementation should only choose among already configured and
healthy runtime endpoints. It should not start, move, unload, or kill models.

## Goals

- Give Gamma one cached view of GPU VRAM, utilization, CPU, RAM, and known model
  runtime health.
- Route work using capacity, model availability, warm state, latency, and
  stability instead of VRAM alone.
- Preserve deterministic static routing as the fallback.
- Prevent simultaneous placement decisions from overcommitting the same VRAM.
- Make every placement and fallback decision observable and correlated with the
  request that caused it.
- Support LLM, STT, TTS, audio understanding, and future model-backed services
  through one provider-neutral interface.

## Non-Goals

- Do not migrate a running model or in-flight request between GPUs.
- Do not poll `nvidia-smi` synchronously in request handlers.
- Do not automatically rewrite local configuration.
- Do not evict models or terminate provider processes in the initial phases.
- Do not assume that the GPU with the most free VRAM is always the best target.
- Do not replace provider adapters or the supervisor with the placement service.

## Proposed Components

Suggested package:

```text
src/gamma/resources/
    models.py
    probe.py
    policy.py
    coordinator.py
    runtime_registry.py
```

### Resource probe

Collect a cached machine snapshot on a background interval:

- GPU index, UUID, name, total/free/used VRAM, utilization, and temperature
- GPU compute processes and reported VRAM use where available
- CPU and system RAM pressure
- snapshot creation time and probe errors

Use structured `nvidia-smi` queries on NVIDIA systems and preserve a supported
no-GPU result on other platforms. The dashboard should consume this shared
snapshot instead of maintaining a separate GPU polling implementation.

### Runtime registry

Describe known model-serving targets and their capabilities:

- stable target ID
- provider/runtime kind
- internal endpoint
- configured device or GPU UUID
- supported models and modalities
- managed or externally managed status
- health, loaded-model state, and recent failures
- optional VRAM reservation and concurrency limits

Machine-specific endpoints and GPU assignments remain in ignored local
configuration. Shared config may define portable schemas and defaults.

### Placement policy

Apply hard constraints before scoring candidates.

Hard constraints include:

- provider and endpoint health
- required model, modality, and runtime capability
- local-only, privacy, or hosted-provider restrictions
- minimum projected VRAM headroom
- explicit device allowlists or denylists
- active cooldown after OOM or repeated startup failure

Soft scoring can consider:

- projected VRAM headroom after placement
- current utilization and queue depth
- warm-model bonus
- model load or transfer cost
- workload-to-device affinity
- fragmentation risk
- recent latency and failure rate
- stability hysteresis to avoid target flapping

Policy decisions must be deterministic for the same snapshot and inputs.

### Resource coordinator

Expose a small shared API:

```text
snapshot()
rank_candidates(workload)
reserve(candidate, projected_resources, ttl)
confirm(reservation, observed_allocation)
release(reservation)
```

Reservations are admission-control records, not proof that VRAM was allocated.
They prevent concurrent decisions from planning against the same free capacity.
They expire automatically and are reconciled with observed process state.

### LLM router integration

Extend `RouteDecision` later with a stable runtime target ID and placement
metadata. The router should:

1. apply its existing purpose, model, provider, privacy, and fallback rules
2. ask the coordinator to rank matching configured runtime targets
3. choose a healthy target or use the existing static fallback
4. log the route and placement decision under the same correlation ID

Provider adapters remain responsible for transport. Process lifecycle remains
outside the router.

## Workload Description

Each placeable workload should declare enough information for admission:

- workload ID and kind
- requested model
- required runtime and capabilities
- estimated base and incremental VRAM
- minimum post-placement headroom
- expected duration and startup latency
- preferred, allowed, or forbidden devices
- whether CPU or hosted fallback is allowed
- whether the workload is persistent, bursty, or per-request
- whether it can be stopped or moved between jobs

Initial estimates may be configured and conservative. Later measurements can
refine them, but observed peak allocation must not silently become a promise
that every request will fit.

## Decision Lifecycle

1. A request receives or preserves a correlation ID.
2. The caller builds a workload description.
3. The coordinator reads a recent cached snapshot.
4. Policy filters and ranks eligible targets.
5. The caller reserves projected capacity for the selected target.
6. The existing adapter or sidecar performs the operation.
7. Actual allocation and health are confirmed when observable.
8. The reservation is released or converted into a persistent allocation.
9. Failure updates target health and may trigger the existing fallback chain.

No placement decision should move a workload while it is serving a request.

## Failure Behavior

- Probe unavailable: use a recent non-expired snapshot, then static routing.
- Snapshot stale: reject automatic placement and use the configured fallback.
- Reservation conflict: try the next ranked candidate.
- Runtime unhealthy: use existing provider backoff and fallback behavior.
- Startup failure or OOM: release the reservation, put the target in a bounded
  cooldown, and try an allowed fallback.
- No target fits: use hosted or CPU fallback only when policy allows it;
  otherwise return a clear capacity error.
- Unknown model size: require conservative configured capacity rather than
  guessing from currently free VRAM.

Static configuration remains the fail-safe path. Resource awareness must not
make a previously valid fixed deployment unusable when telemetry fails.

## Observability

Placement events should use the shared structured logging foundation and include:

- event name and timestamp
- request and correlation IDs
- workload ID and kind
- resource snapshot ID and age
- selected target, provider, model, GPU index, and GPU UUID
- free, reserved, estimated, and projected-headroom VRAM
- warm-model state and endpoint health
- decision reason and rejected-candidate reasons
- reservation ID and outcome
- fallback target and failure classification

Do not log prompts, credentials, authorization headers, or provider tokens.
The dashboard may show advisory placement state, reservations, and blockers,
but logs remain the durable diagnostic record.

## Configuration Direction

A future portable schema may resemble:

```toml
[[resource_routing.targets]]
id = "ollama_gpu_0"
kind = "ollama"
endpoint_ref = "local_ollama_gpu_0"
device = "cuda:0"
managed = false
models = ["gemma4:e4b", "gpt-oss:20b"]
reserved_vram_mb = 2048

[resource_routing.policy]
enabled = false
shadow_mode = true
snapshot_max_age_seconds = 5
reservation_ttl_seconds = 30
minimum_headroom_mb = 2048
```

Real endpoints, device mappings, and machine-specific limits belong in local
configuration. The coordinator must also support the current single-endpoint
deployment without requiring duplicate Ollama instances.

## Rollout Plan

### Phase 1: Shared read-only snapshots

- Extract GPU polling from the dashboard into a reusable resource probe.
- Add CPU/RAM and process attribution where supported.
- Preserve the existing dashboard response contract.
- Add probe caching, timeouts, and structured failure logs.

Implementation started on June 15, 2026:

- `src/gamma/resources/probe.py` provides read-only resource snapshots with
  CPU, RAM, disk, GPU, and optional GPU compute-process attribution.
- The dashboard consumes the shared monitor while preserving its existing
  machine-status payload.
- No placement policy, reservation API, model lifecycle management, or runtime
  endpoint selection has been implemented yet.

### Phase 2: Shadow placement decisions

- Define workload and target models.
- Run policy decisions without changing any route.
- Attach snapshot and would-select metadata to LLM route logs.
- Compare decisions against observed load and operator expectations.

### Phase 3: Endpoint-aware LLM routing

- Register separately configured local model endpoints.
- Route only among already running targets.
- Retain the existing deterministic provider/model fallback.
- Start disabled and enable by explicit local configuration.

### Phase 4: Startup admission for model sidecars

- Let STT, TTS, and audio-understanding startup consult the coordinator.
- Select a device before process launch; never move an active sidecar.
- Preserve each service's explicit configured device as an override.

### Phase 5: Optional lifecycle management

- Consider managed load/unload and pinned provider startup only after endpoint
  routing is stable and measured.
- Require explicit operator enablement, bounded cooldowns, and audit events.
- Keep destructive process actions in the supervisor/runtime manager.

## Tests And Acceptance Criteria

- Policy unit tests use synthetic snapshots and deterministic candidate order.
- Cover stale telemetry, no GPU, insufficient headroom, reservations, warm
  models, endpoint failure, OOM cooldown, and allowed fallback behavior.
- Router tests prove that shadow mode never changes the selected route.
- Integration tests use fake runtime endpoints; tests must not depend on local
  GPU availability.
- Dashboard tests prove its existing metrics contract remains compatible.
- Runtime validation keeps Twitch dry-run and voice disabled while unrelated
  integration observability work is in progress.

The first active-routing phase is acceptable only when disabling resource-aware
routing immediately restores the current deterministic behavior.

## Open Decisions

- Whether Gamma should manage multiple Ollama processes or only discover
  operator-managed endpoints.
- How model VRAM estimates are sourced and updated across model revisions.
- Whether persistent sidecars reserve measured peak VRAM or a configured cap.
- How multi-GPU models and tensor parallel runtimes should be represented.
- Which workloads may use hosted fallback when local capacity is exhausted.

## Recommended Next Step

Implement Phase 1 only after the current integrations observability work package
is complete. It provides reusable telemetry and removes duplicate dashboard
probing without changing model placement behavior.
