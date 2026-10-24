# Resource Routing

Status: Current with experimental targets
Last verified: 2026-06-22

## Implemented

- shared CPU, RAM, disk, NVIDIA GPU, and GPU-process snapshots
- configured resource targets, endpoints, modalities, models, and capacity estimates
- deterministic placement ranking with health, freshness, headroom, model, and warm-state checks
- advisory shadow decisions attached to LLM route logs
- temporary advisory reservations during routed calls
- optional active endpoint selection for local LLM calls
- optional startup admission for auto-device Qwen and audio-understanding sidecars
- observed sidecar allocation logs and estimate reconciliation

All placement behavior is disabled or advisory by default unless explicitly
enabled in shared/local configuration.

## Not Implemented

- automatic model loading or eviction
- dynamic migration of running models
- managed Ollama lifecycle
- cluster scheduling or remote machine control

Resource routing is experimental. It may not prevent the core assistant from
starting with explicit provider/device configuration.

## Shared execution authority

Model calls now acquire endpoint and workload budgets through a cross-process
SQLite authority independent of the checkout data directory. The shared
`config/admission.toml` policy starts with one interactive-only execution unit.
Background capacity must be measured before it is enabled. This mandatory
execution boundary is separate from optional/advisory placement ranking above.
Uncertain provider work retains capacity; expiry and process death alone do not
prove remote completion. See `modular_worker_runtime_admission_slice.md` for the
caller audit, resource dimensions, reconciliation rules and Gamma measurements.
Model residency, eviction and unmanaged external callers remain separate.
