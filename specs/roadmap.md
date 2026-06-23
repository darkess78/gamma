# Gamma Roadmap

Status: Target
Last verified: 2026-06-22

## Current Milestone: Persistent Shana

Delivered in the current refactor:

- enforced the Shana API/dashboard ownership boundary
- removed dead dashboard initialization code and duplicate action/status loading
- replaced expensive all-page status polling with a lightweight header contract
- established Monitor as the persistent text and output room
- removed the duplicate Talk client while preserving legacy redirects
- added explicit-audience dynamic Wake events and primary-quality Wake routing
- added durable turn continuity, rolling summaries, and working checkpoints
- added model-aware context budgets and overflow-specific retry
- moved bounded proactive evaluation into Shana with conservative Presence gates
- moved hosted, local-voice, desktop, Discord, and model-backed audio features behind explicit extras

Remaining operational gate:

1. Define and run repeatable everyday conversation reliability and latency checks.
2. Run owner-facing Wake/restart/continuity soak sessions and use those results
   for focused tuning rather than another structural rewrite.

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
