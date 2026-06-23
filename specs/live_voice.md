# Live Voice

Status: Current
Last verified: 2026-06-22

## Data Flow

```text
browser mic -> dashboard WebSocket session -> Shana live-job API
            -> STT -> stream/conversation -> TTS chunks
            -> dashboard playback and performer outputs
```

The dashboard owns browser capture, VAD, playback, and interaction state.
Shana owns transcription, generation, synthesis, job state, and cancellation.

## Current Protocol

- WebSocket endpoint: `/api/voice/live`
- Shana job endpoints: `/v1/voice/live/*` and `/v1/voice/transcribe`
- messages cover ready, ping/pong, start/end/cancel/interrupt, partial transcripts, reply chunks, job results, idle decisions, and errors
- jobs use queued, running, speaking, completed, cancelled, and failed states
- finalized turns run in cancellable subprocess workers
- chunk metadata includes ordering, finality, interruptibility, and protection time

## Turn And Interruption Policy

- browser VAD uses an adaptive noise floor, open/release thresholds, minimum and maximum turn duration, and trailing silence
- partial STT is best-effort snapshot transcription
- transcript-confirmed barge-in rejects likely playback echo before hard cancellation
- cancellation clears stale playback/output and records reason and latency
- simple chunked mode is the supported default
- incremental sentence generation remains experimental

## Known Limits

- browser capture uses `ScriptProcessorNode`; AudioWorklet is future work
- the protocol is phrase/chunk based, not continuous token-level speech streaming
- client history is bounded operational state, not the durable memory system

## Acceptance

Live voice must fail without orphaning workers, replaying stale audio, or
blocking the dashboard event loop. Network errors must be visible and safe to
retry. The everyday Talk client hides advanced tuning while using the same
protocol and defaults.
