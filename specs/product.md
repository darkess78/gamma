# Gamma Product

Status: Current
Last verified: 2026-06-22

## Core Promise

Gamma is the runtime for Shana: a persistent assistant with a stable persona,
selective memory, text conversation, and interruptible voice conversation.

The primary success criterion is simple: the owner can start Shana, talk or
type to her repeatedly, and receive reliable in-character responses without
having to operate the streamer control plane.

## Product Tiers

### Core: Persistent Shana

- text and live voice conversation
- stable persona and emotional continuity
- selective memory and identity-aware context
- provider-independent LLM, STT, and TTS boundaries
- Presence modes for sleep, wake, break, and live operation
- privacy and speech-safety enforcement
- a persistent Monitor room for local text and output playback

Core work takes priority over new integrations.

### Maintained Extension: Streamer Foundation

- normalized stream inputs and turn policy
- Twitch IRC and EventSub ingestion
- performer/output events and target policies
- public-output safety gates and operator stop controls
- traces, replay, evaluation, and temporary stream context
- dashboard operations needed to run the existing foundation

This tier is intentionally preserved because it follows the Neuro-inspired
architecture research. New games, platforms, and presentation integrations are
frozen until the core assistant experience is reliable.

### Experimental And Tooling

- incremental sentence-generation experiments
- speaker-emotion and audio-event models
- resource-aware placement experiments
- Discord output/voice, OBS control, and game adapters
- VTube Studio runtime validation
- TTS dataset preparation and voice research

Experimental work must remain optional and may not complicate the core startup
or interaction path.

## Product Rules

- Shana owns assistant domain state and inference.
- The dashboard is an authenticated control plane and client, not a second
  Shana runtime.
- Streamer work uses constrained inputs, actions, outputs, and human override.
- Working optional features are classified and frozen rather than deleted
  without an explicit product decision.
- Specs distinguish implemented behavior from future targets.
