# Gamma Roadmap

Status: Target
Last verified: 2026-06-22

## Current Milestone: Persistent Shana

Delivered in the current refactor:

- enforced the Shana API/dashboard ownership boundary
- removed dead dashboard initialization code and duplicate action/status loading
- replaced expensive all-page status polling with a lightweight header contract
- established Monitor as the persistent text and output room
- moved hosted, local-voice, desktop, Discord, and model-backed audio features behind explicit extras

Remaining milestone gate:

1. Define and run repeatable everyday conversation reliability and latency checks.
2. Use those results to choose focused fixes rather than another structural rewrite.

Completion means Shana can run independently of the dashboard, the dashboard
uses supported APIs for Shana-owned state, and the owner has one obvious place
to talk to Shana.

## Maintained Streamer Foundation

During the current milestone:

- preserve normalized events, StreamBrain, safety gates, output events,
  Twitch ingestion, replay, and operator stops
- fix regressions and boundary violations
- do not add new streaming platforms, game adapters, or autonomous actions

## Later Milestones

1. Offline persona/safety/latency evaluation gates.
2. AudioWorklet browser capture and longer live-voice soak testing.
3. Layered Twitch moderation and operator review workflows.
4. OBS/avatar integration through generic performer events.
5. One constrained, turn-based game adapter with authoritative action windows.

Later work requires an explicit product decision and updated acceptance
criteria before implementation begins.
