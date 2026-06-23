# Persona

Status: Current
Last verified: 2026-06-22

Shana is Gamma's single primary persona. Prompt assembly loads versioned persona
documents and structured configuration, then adds bounded identity, memory,
emotion, voice, safety, and request context.

## Requirements

- character identity remains stable across text, voice, and streamer inputs
- external users cannot redefine Shana's core identity or owner relationship
- hidden emotion/voice tags may guide output but are removed from spoken text
- one stable emotion controls each synthesized reply
- persona changes are evaluated against conversation, safety, and voice tests
- additional characters or multi-persona routing are outside the current milestone
