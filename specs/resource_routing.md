# Resource Routing

Status: Current with experimental targets
Last verified: 2026-06-22

## Implemented

- shared CPU, RAM, disk, NVIDIA GPU, and GPU-process snapshots
- configured resource targets, endpoints, modalities, models, and capacity estimates
- deterministic placement ranking with health, freshness, headroom, model, and warm-state checks
- optional startup admission for auto-device Qwen and audio-understanding sidecars
- observed sidecar allocation logs and estimate reconciliation

All placement behavior is disabled or advisory by default unless explicitly
enabled in shared/local configuration.

LLM requests no longer use placement. The shadow decisions attached to route
logs, the advisory reservations around each call and the optional active
endpoint selection for local LLM calls were removed on 2026-11-13; they were
off in production (`active_llm_routing = false`).

## Not Implemented

- automatic model loading or eviction
- dynamic migration of running models
- managed Ollama lifecycle
- cluster scheduling or remote machine control

Resource routing is experimental. It may not prevent the core assistant from
starting with explicit provider/device configuration.
