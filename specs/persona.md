# Persona

Status: Current
Last verified: 2026-08-30

Shana is Gamma's single primary persona. Prompt assembly loads versioned persona
documents and structured configuration, then adds bounded identity, memory,
emotion, voice, safety, and request context.

## Requirements

- character identity remains stable across text, voice, and streamer inputs
- external users cannot redefine Shana's core identity or owner relationship
- hidden emotion/voice tags may guide output but are removed from spoken text
- one stable emotion controls each synthesized reply
- private deliberation is represented only by a compact structured decision;
  raw chain-of-thought, hidden analysis, and scratchpad prose are not requested,
  displayed, logged, or persisted
- only `final_text` selected by the structured decision may become display or
  speech text; a silent turn may still apply validated emotion and working-state
  updates
- persona changes are evaluated against conversation, safety, and voice tests
- additional characters or multi-persona routing are outside the current milestone
