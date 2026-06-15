# Specs

This folder is the source of truth for product, architecture, and implementation intent.

If code and specs disagree, update one of them immediately.

Suggested reading order:
1. `product.md`
2. `architecture.md`
3. `phase1.md`
4. domain-specific specs like `voice.md`, `memory.md`, `persona.md`
5. `audio_understanding_plan.md` for speaker-affect and non-speech audio analysis
6. `audio_understanding_deployment_proposal.md` for persistent model placement
7. `audio_understanding_handoff.md` for current measurements and resumable next steps
8. `integrations_observability_handoff.md` for the Twitch, Discord, VTube Studio, logging, and execution backlog
9. `llm_router.md` for current LLM routing scope and future router upgrades
10. `resource_aware_model_routing_proposal.md` for shared GPU/resource telemetry and future workload placement
11. `streamer_plan/streamer_roadmap.md` for the staged AI-streamer architecture direction
12. `streamer_plan/streamer_roadmap_current.md` for the mature current-state target architecture
13. `streamer_plan/streamer_gap_backlog.md` for the bridge from the current repo to the target state
14. `shana_output_bus.md` for the planned three-PC output bus, performer, dashboard monitor, VTuber, and Discord communication architecture
