# Gamma Specifications

Status: Current
Last verified: 2026-06-22

Specifications describe product intent and supported behavior. Code and specs
must be updated together when a public contract changes.

## Authority Order

1. `LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` for deployment, ports, public URLs, and proxy routing.
2. `product.md` for product tiers and priorities.
3. `architecture.md` for runtime ownership and dependency direction.
4. `current_implementations.md` for the concise implemented inventory.
5. `roadmap.md` for ordered near-term work.
6. Domain specs for detailed behavior.

## Domain Specs

- `persona.md`
- `memory.md`
- `voice.md`
- `live_voice.md`
- `audio_understanding.md`
- `llm_router.md`
- `resource_routing.md`
- `modular_worker_runtime_admission_slice.md`
- `modular_worker_runtime_silent_slice.md`
- `improvement.md`
- `integrations.md`
- `minecraft_companion.md`
- `shana_presence.md`
- `shana_output_bus.md`
- `voice_dataset.md`
- `linux_portability.md`

## Streamer Research And Target

- `modular_worker_runtime_proposal.md`, the proposed worker ownership, lifecycle,
  dashboard, and phased migration design based on the 2026-09-19 source audit
- `modular_worker_runtime_audit.md`, source evidence and current coupling points
- `modular_worker_runtime_brain_process.md`, implemented independent cognition, authority protocol, lifecycle and evidence
- `modular_state_writer_inventory.md`, completed state writer ownership, evidence and rollback contract

- `streamer_plan/roadmap.md`
- `streamer_plan/neuro_architecture_review.md`
- `streamer_plan/twitch_stream_module.md`
- `streamer_plan/twitch_operator_setup.md`
- `Neuro-sama Deep Research Report.pdf`
- `streamer_plan/Neuro-sama Reverse-Engineering Report.pdf`

Research documents are references, not claims about Neuro-sama's private
implementation. Target documents must label future behavior explicitly.

## Class Projects

- `class-projects/README.md`
- `class-projects/cs4700-memory-triage.md`
- `class-projects/cs4710-osrs-screen-recognition.md`

Class-project specifications record confirmed Gamma integration points,
working project decisions, data/privacy rules, and phased offline work. They do
not claim that experimental models or runtime integrations are implemented.
