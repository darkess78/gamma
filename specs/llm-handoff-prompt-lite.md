# Gamma — LLM Handoff Prompt (Lite)

Quick orientation for the current repo state. For full context, see [specs/llm-handoff-prompt.md](/home/neety/.openclaw/workspace/gamma-main/specs/llm-handoff-prompt.md).

## What Gamma Is

Gamma is a local Shana assistant stack with:

- FastAPI assistant backend
- separate dashboard app
- browser live voice and voice roundtrip
- local memory
- pluggable STT / TTS / LLM backends
- stream brain and Twitch operator controls
- early performer output bus and `/performer` browser page for Stream PC / OBS testing
- optional dataset-prep tooling

Current practical focus is the live assistant/dashboard workflow, Twitch/stream operator workflow, and the new performer output path, not the old dataset-prep-first roadmap.

## Important Files

- [gamma/dashboard/static/dashboard.js](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.js)
  Browser voice UI, subtitles, mute buttons, memory-delete modal, stream panels, Twitch controls.

- [gamma/dashboard/static/index.html](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/index.html)
  Dashboard structure and inline button hooks.

- [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
  Dashboard API routes, including stream and Twitch proxy/control routes.

- [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)
  Status payloads, provider actions, memory actions, stream/Twitch status and actions.

- [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py)
  Assistant API routes for conversation, vision, voice, stream events, stream logs, queue, temp memory, self-goals, stream stop, performer websocket/page, and audio artifact serving.

- [gamma/stream/brain.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/brain.py)
  Stream event decision engine, safety/policy handling, speech queue, output emission.

- [gamma/stream/output.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/output.py)
  Stream output dispatcher. Default dispatch persists JSONL logs and publishes generic performer events.

- [gamma/performer/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/models.py)
  Runtime-agnostic performer event models and stream-output mapping.

- [gamma/performer/bus.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/bus.py)
  In-process performer event bus with recent history and websocket subscribers.

- [gamma/performer/static/performer.html](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/static/performer.html)
  Minimal Stream PC / OBS browser-source page for subtitles, state, and Shana audio playback.

- [gamma/stream/temp_memory.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/temp_memory.py)
  Short-lived stream memory store.

- [gamma/stream/self_goals.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/self_goals.py)
  Proposed self-goal storage and approve/reject/clear workflow.

- [gamma/integrations/twitch/worker.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/worker.py)
  Twitch IRC ingestion worker.

- [gamma/integrations/twitch/eventsub.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/eventsub.py)
  Twitch EventSub websocket worker.

- [gamma/integrations/twitch/trust.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/trust.py)
  Viewer trust metadata store.

- [gamma/memory/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/models.py)
  Memory row models, including `created_at`.

- [gamma/memory/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/service.py)
  Memory persistence, recent listing, targeted deletion.

- [gamma/voice/stt.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/stt.py)
  Local STT path is faster-whisper.

- [gamma/voice/tts.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/tts.py)
  TTS provider selection and synthesis.

- [gamma/config.py](/home/neety/.openclaw/workspace/gamma-main/gamma/config.py)
  Settings, including dashboard public-vs-bind URL handling, stream safety, and Twitch runtime controls.

## File Structure

- `gamma/api/` - assistant API routes
- `gamma/dashboard/` - operator dashboard app/service/static UI
- `gamma/integrations/twitch/` - Twitch IRC/EventSub/replay/safety/trust adapters
- `gamma/stream/` - stream brain, output, traces, replay, queue helpers, temp memory, self-goals
- `gamma/performer/` - performer event bus, generic output models, and browser performer page
- `gamma/voice/` - STT/TTS, live voice, live jobs/runtime, roundtrip, controller, reply helpers
- `gamma/safety/` - privacy and speech/stream safety layers
- `gamma/llm/` - mock/OpenAI/local/router adapters
- `gamma/memory/` - SQLModel memory service
- `scripts/` - platform launchers and service sidecar scripts
- `specs/` - architecture, implementation, streamer/Twitch, and handoff docs
- `tests/` - API, dashboard, voice, stream, Twitch, memory, routing, and system tests

## Current Operational Facts

- Dashboard runs internally on port `8001`
- HTTPS is expected to be terminated by a reverse proxy
- public dashboard URL is separate from bind port
- browser live voice currently works
- browser capture still uses deprecated `ScriptProcessorNode`
- stream event API, stream output logs, queue, temp memory, and self-goals are implemented
- performer output bus is implemented for generic subtitle/speech/expression/motion events with target policies, monotonic sequences, replay resume, and replay gap reporting
- `/performer`, `/monitor`, and `/overlay/subtitles` serve browser output clients for Stream/Gaming PC use
- `WebSocket /v1/performer/events` streams performer events; `GET /v1/performer/events/recent` exposes recent bus history with `after_sequence`
- `GET /v1/performer/status` reports bus stats, per-target latest events, output targets, and adapter status
- VTube Studio adapter maps generic performer events to configured hotkey request payloads, includes an optional websocket/auth client, and has a runner that can subscribe to `stream_public` performer events
- Discord adapter normalizes Discord message/voice inputs into stream input events with identity resolver support; a dependency-light runtime tracks config/status and isolated `discord_call` outputs, while the real bot/voice transport remains future work
- performer bus now maintains a derived in-memory spoken-turn store and exposes recent turns through performer status
- `GET /v1/audio/artifacts/{filename}` serves network-safe TTS WAV artifacts from the Shana API
- Twitch IRC/EventSub workers, runtime controls, viewer trust, and replay tooling are implemented
- public stream speech should stay operator-supervised and safe-by-default
- performer clients should not depend on Shana PC local file paths; use `audio_url` / artifact endpoints
- dashboard now has:
  - mute mic
  - mute Shana
  - pop-out subtitles
  - latest memories
  - recent-memory selection delete
  - stream traces/safety/output/queue/temp-memory/self-goal panels
  - Twitch worker/EventSub/settings/viewer-trust/replay controls

## Memory Notes

Current safe UI behavior:

- no one-click full memory wipe in the normal dashboard path
- recent memory cleanup should be previewed and selective
- recent memory is time-based now that rows carry `created_at`

## Platform Notes

Preserve both Linux and Windows compatibility.

Do not:

- hardcode Windows-only paths in shared runtime code
- merge proxy-facing public port with internal dashboard bind port
- regress the Linux-hosted / Windows-browser flow that currently works
- bypass stream safety, dry-run, queueing, or operator review for Twitch-facing speech
- put VTube Studio-specific logic into `ConversationService`, `StreamBrain`, or core voice code; keep it in a future performer adapter/client

## Validation

Use:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

`pytest` is scoped to `tests/` in `pyproject.toml` so vendored sidecar tests under `data/` are not collected.

Useful smoke tests:

```bash
./.venv/bin/python -m gamma.run_llm_test "Dashboard LLM smoke test."
./.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
./.venv/bin/python -m gamma.run_tts_test "Dashboard TTS smoke test."
./.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac
```

Focused stream/Twitch checks:

```bash
./.venv/bin/python -m pytest tests/test_stream_brain.py tests/test_stream_output.py tests/test_twitch_integration.py -v
```
