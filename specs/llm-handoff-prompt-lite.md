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
- optional dataset-prep tooling

Current practical focus is the live assistant/dashboard workflow and Twitch/stream operator workflow, not the old dataset-prep-first roadmap.

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
  Assistant API routes for conversation, vision, voice, stream events, stream logs, queue, temp memory, self-goals, and stream stop.

- [gamma/stream/brain.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/brain.py)
  Stream event decision engine, safety/policy handling, speech queue, output emission.

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
- Twitch IRC/EventSub workers, runtime controls, viewer trust, and replay tooling are implemented
- public stream speech should stay operator-supervised and safe-by-default
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

## Validation

Use:

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

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
