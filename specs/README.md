# Gamma Specifications

Status: Current
Last verified: 2026-06-22

Specifications describe product intent and supported behavior. Code and specs
must be updated together when a public contract changes.

## Fresh foundation discussion

- [Coordination core implementation handoff](shana_coordination_core_handoff.md)
  records the authorized first isolated refactor, GitHub checkpoints, ownership,
  worker recovery, and acceptance requirements for the new implementation chat.
- [Shana foundation direction](shana_foundation_direction.md) records the
  current user direction captured on 2026-10-23, including agreed experiences,
  current scope choices, and open decisions.
- [Foundation review and next steps](shana_foundation_review.md) records the
  production/candidate source comparison, current process observations,
  keep/change/replace recommendations, and remaining evidence gaps. The earlier
  documentary comparison is retained as background.

The direction and review retain discussion decisions and implementation evidence.
The handoff records the subsequently authorized first isolated refactor. These
documents do not establish live capability; existing runtime contracts remain
in effect until revisions are implemented, verified, and approved for activation.

## Authority Order

1. `LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` for deployment, ports, public URLs, and proxy routing.
2. `product.md` for current product direction and links to earlier plans.
3. `architecture.md` for runtime ownership and dependency direction.
4. `current_implementations.md` for the concise implemented inventory.
5. `roadmap.md` for the next proposed discussion and review work.
6. Domain specs for detailed behavior.

## Domain Specs

- `persona.md`
- `memory.md`
- `voice.md`
- `live_voice.md`
- `audio_understanding.md`
- `llm_router.md`
- `resource_routing.md`
- `improvement.md`
- `integrations.md`
- `minecraft_companion.md`
- `shana_presence.md`
- `shana_output_bus.md`
- `voice_dataset.md`
- `linux_portability.md`

## Streamer Research And Target

- [Expanded reference research, 2026-10-23](streamer_plan/shana_reference_research_2026-10-23.html),
  covering Neuro-sama, Riko, and J.A.I.son with ERMA; see the direction file for
  coverage and observation limits
- `streamer_plan/twitch_stream_module.md`
- `streamer_plan/twitch_operator_setup.md`
- `Neuro-sama Deep Research Report.pdf`
- `streamer_plan/Neuro-sama Reverse-Engineering Report.pdf`

Research documents are references, not claims about Neuro-sama's private
implementation. Target documents must label future behavior explicitly.

## Earlier plans and assessments

[The archive index](archive/README.md) separates older planning from active
references. The [2026-10-23 archive](archive/2026-10-23/README.md) preserves the
earlier product and streamer roadmaps, modular worker proposal and source audit,
Minecraft implementation plan, and Neuro architecture interpretation.

Archived source findings and staged evidence remain inputs to review. They do
not establish current production behavior or select the next implementation.

## Class Projects

- `class-projects/README.md`
- `class-projects/cs4700-memory-triage.md`
- `class-projects/cs4710-osrs-screen-recognition.md`

Class-project specifications record confirmed Gamma integration points,
working project decisions, data/privacy rules, and phased offline work. They do
not claim that experimental models or runtime integrations are implemented.
