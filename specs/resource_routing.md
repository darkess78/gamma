# Resource Routing

Status: Current with experimental targets
Last verified: 2026-06-22

## Implemented

- shared CPU, RAM, disk, NVIDIA GPU, and GPU-process snapshots
- configured resource targets, endpoints, modalities, models, and capacity estimates
- deterministic placement ranking with health, freshness, headroom, model, and warm-state checks
- optional startup admission for auto-device Qwen and audio-understanding sidecars
- observed sidecar allocation logs and estimate reconciliation
- idle unloading of the GPU voice sidecars (2026-11-15, `supervisor/voice_idle.py`):
  the Shana API stops Qwen TTS and audio understanding after
  `voice_sleep_unload_minutes` (5) without voice activity while presence is
  `sleep`, or `voice_idle_unload_minutes` (20) while awake, and preloads them
  when presence leaves `sleep`. Any process about to use one starts it on
  demand (TTS loads in about 7 s) through the same supervisor start path, so
  admission still picks the device. Only processes run under
  `GAMMA_LIFECYCLE_MANAGER` start or stop sidecars. `voice_idle_unload_enabled`
  turns it off. Ollama models already unload on their own after
  `local_llm_keep_alive` (30m).

All placement behavior is disabled or advisory by default unless explicitly
enabled in shared/local configuration.

LLM requests no longer use placement. The shadow decisions attached to route
logs, the advisory reservations around each call and the optional active
endpoint selection for local LLM calls were removed on 2026-11-13; they were
off in production (`active_llm_routing = false`).

## Not Implemented

- automatic eviction under memory pressure (only idle unloading of the voice sidecars exists)
- dynamic migration of running models
- managed Ollama lifecycle
- cluster scheduling or remote machine control

Resource routing is experimental. It may not prevent the core assistant from
starting with explicit provider/device configuration.
