# Neuro-Inspired Streamer Roadmap

Status: Target with maintained implemented foundation
Last verified: 2026-06-22

This roadmap applies lessons from the tracked Neuro-sama research without
claiming knowledge of Neuro-sama's private implementation. Gamma is not trying
to reproduce a hidden model stack. It is targeting observable qualities:
stable character, low latency, layered safety, constrained actions, reliable
presentation, replayability, and human control.

## Implemented Foundation

- normalized stream event and actor schemas
- StreamBrain attention/turn decisions
- Twitch IRC and EventSub ingestion
- public input sanitization and viewer trust
- speech pacing, budgets, queueing, and safety review
- generic performer/output events and target policies
- dashboard monitor, performer page, and subtitle overlay
- temporary stream context, traces, replay, evaluation, and self-goal approval
- global public-output stop and Presence gating

## Current Freeze

During the Persistent Shana milestone, maintain the implemented foundation but
do not add:

- new streaming platforms
- autonomous game control
- OBS scene control
- Discord voice/output
- broad tool/plugin ecosystems
- additional on-air personas

Boundary, safety, reliability, and regression fixes remain allowed.

## Required Before Public Expansion

1. Repeatable offline persona, latency, and safety evaluations.
2. Layered chat/input/output/tool moderation with operator veto.
3. Durable replay evidence for every public decision and action.
4. Reliable live voice with soak tests, cancellation, and safe fallback.
5. Clear identity, voice-consent, copyright, and synthetic-content policies.
6. Role/approval controls for scene, message, tool, and external actions.

## Later Build Order

1. Moderator review and Twitch-native moderation hooks.
2. OBS/avatar adapters consuming generic performer events.
3. One constrained, turn-based game adapter.
4. Closed adversarial testing with trusted users.
5. Human-moderated public beta.

Game integration follows the public Neuro SDK pattern: the adapter owns
authoritative state and legal action windows; the dialogue model chooses only
from bounded structured actions. High-APM control requires a separate
low-level controller and is outside this roadmap.

## Research Sources

- `../Neuro-sama Deep Research Report.pdf`
- `Neuro-sama Reverse-Engineering Report.pdf`
- `neuro_architecture_review.md`
- public Neuro SDK concepts summarized in those documents
