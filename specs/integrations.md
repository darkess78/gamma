# Integrations

Status: Current
Last verified: 2026-06-22

Integrations normalize external inputs into stream events and consume generic
performer outputs. They do not call conversation internals directly.

## Twitch

- IRC chat ingestion with authentication, reconnect/backoff, bot ignores, and durable state
- EventSub WebSocket ingestion with token validation and subscription evidence
- chat sanitization, trust overrides, dry-run replay, and per-event runtime controls
- normalized follows, raids, subscriptions, bits, redeems, and moderation-style events
- stream output remains gated by Presence, safety, pacing, and target policy

## Discord

- optional allowlisted text worker for one guild/channel
- normalized identity and message metadata posted to the Shana stream API
- replies, voice input, and voice output remain disabled experimental work

## VTube Studio

- optional adapter translates generic expression/motion events into hotkey requests
- disabled by default and not part of the core assistant path
- endpoint, token, and hotkey mapping remain machine-local configuration

## Future Integration Rule

New integrations are frozen during the Persistent Shana milestone. A future
adapter must expose bounded inputs/actions, preserve auditability, and avoid
adding platform-specific logic to conversation, voice, or StreamBrain.
