# Gamma Roadmap

Status: Target
Last verified: 2026-06-22

## Current Milestone: Persistent Shana

1. Enforce the Shana API/dashboard ownership boundary.
2. Remove dashboard duplication and expensive all-page polling.
3. Add a dedicated text and live-voice Talk client.
4. Keep setup and optional dependency paths understandable.
5. Measure everyday conversation reliability and latency.

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
