# Gamma — LLM Handoff Prompt (Full)

Use this document to orient yourself to the current Gamma repo before making changes. It is meant for handoff between coding agents and should reflect the repo as it exists now, not an old roadmap.

## What Gamma Is

Gamma is a local assistant stack centered on a Shana persona, with these main parts:

1. Assistant backend
   `gamma.main` is a FastAPI app that owns conversation, memory, STT, TTS, vision, and live voice worker orchestration.

2. Dashboard
   `gamma.dashboard.main` is a separate FastAPI app that provides the operator dashboard, browser voice UI, provider controls, logs, and memory visibility/maintenance.

3. Voice pipeline
   The repo supports:
   - browser voice roundtrip
   - browser live voice over websocket
   - CLI voice loops
   - local STT/TTS provider tests

4. Stream/Twitch operator stack
   Stream events can flow through a stream brain, safety review, queueing,
   temporary stream memory, self-goal proposal review, Twitch IRC/EventSub
   ingestion, and dashboard operator controls.

5. Performer output path
   The assistant backend now has an early performer output bus. Stream output
   events can be translated into generic performer events and served to browser
   clients over a Shana API websocket. A minimal `/performer` page exists for
   Stream PC / OBS browser-source testing.

6. TTS dataset prep tooling
   The Tkinter dataset-prep tool still exists, but it is not the primary focus of the current repo state.

Current practical focus: the dashboard/browser voice workflow, the Twitch/stream operator workflow, the new performer output bus, and Linux/Windows compatibility for the live assistant path.

## Current State

The repo currently has working support for:

- Dashboard process supervision
- Shana backend supervision
- Browser live voice through `WebSocket /api/voice/live`
- Browser voice roundtrip through `POST /api/voice/roundtrip`
- Local TTS provider control for GPT-SoVITS and Qwen3-TTS sidecars
- HTTPS dashboard use behind a reverse proxy
- Dashboard auth
- Memory inspection and recent-memory cleanup from the dashboard
- Subtitle-style browser transcript display and pop-out subtitle window
- Browser mute controls for mic and assistant playback
- Stream event handling through `POST /v1/stream/events`
- Stream output logging, queue visibility, temporary memory, and self-goal review
- Twitch IRC worker, Twitch EventSub worker, replay tooling, and viewer-trust controls
- Dashboard safety and stream controls for Twitch-facing operation

Recent important changes:

- Qwen3-TTS startup now falls back cleanly when requested CUDA devices are unavailable
- Local faster-whisper STT also falls back more safely across device availability differences
- Dashboard public URL handling now separates bind port from public/proxied URL
- Dashboard memory panel now includes "Latest Memories"
- Memory cleanup is no longer a blind full wipe in normal UI flow; there is now a recent-memory selection/delete flow
- Browser voice controls now include mute mic, mute assistant playback, and pop-out subtitles
- Dashboard stream panels now include traces, safety review visibility, output events, pending stream speech, temporary stream memory, and self-goals
- Twitch controls now cover IRC worker lifecycle, EventSub lifecycle, runtime settings, viewer trust, replay, and dry-run replay
- Stream safety now has configurable LLM review timeout behavior and fallback audio support
- Stream/voice stack launcher scripts were added for starting the practical Shana runtime bundles
- A new performer output bus was added under `gamma/performer/`
- Stream output dispatch now publishes both JSONL log events and generic performer events
- Shana API exposes `WebSocket /v1/performer/events`, `GET /v1/performer/events/recent`, `GET /performer`, and `GET /v1/audio/artifacts/{filename}`
- The `/performer` browser page can be opened on the Stream PC or in OBS as an early audio/subtitle/expression monitor
- Pytest discovery is scoped to `tests/` in `pyproject.toml` so vendored/sidecar tests under `data/` are not collected by normal Gamma test runs

## Architecture Map

### Backend

- [gamma/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/main.py)
  FastAPI entrypoint for the assistant backend.

- [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py)
  Main API routes for conversation, vision, voice, stream, performer websocket, performer page, and audio artifact APIs.

- [gamma/conversation/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/conversation/service.py)
  Core conversation pipeline. Builds persona/system prompt, runs LLM, memory extraction, and optional TTS.

- [gamma/persona/loader.py](/home/neety/.openclaw/workspace/gamma-main/gamma/persona/loader.py)
  Builds the effective system prompt from persona files and memory.

- [gamma/memory/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/service.py)
  SQLite-backed memory layer. Stores profile facts and episodic memory. Recent-memory deletion and latest-memory listing now live here.

- [gamma/voice/stt.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/stt.py)
  STT provider selection. Current local STT path is faster-whisper.

- [gamma/voice/tts.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/tts.py)
  TTS provider selection and synthesis. Supports OpenAI, Piper, GPT-SoVITS, Qwen3-TTS, stub.

- [gamma/voice/live.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/live.py)
  Browser live voice websocket session manager.

- [gamma/voice/live_jobs.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/live_jobs.py)
  Background worker job manager for live voice turns.

- [gamma/run_live_voice_worker.py](/home/neety/.openclaw/workspace/gamma-main/gamma/run_live_voice_worker.py)
  Worker process for live turns.

### Stream Brain And Output

- [gamma/stream/brain.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/brain.py)
  Stream event decision engine. Classifies chat/events, applies safety and policy, calls conversation, queues speech, and emits output events.

- [gamma/stream/actions.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/actions.py)
  Converts stream decisions and assistant replies into action plans.

- [gamma/stream/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/models.py)
  Shared stream input, decision, action, and result models.

- [gamma/stream/output.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/output.py)
  Stream output dispatchers. The default dispatcher persists JSONL output records and publishes generic performer events to the performer bus.

- [gamma/stream/trace.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/trace.py)
  Stream trace persistence and recent trace reading.

- [gamma/stream/replay.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/replay.py)
  Recent stream turn replay/evaluation helpers for dashboard inspection.

- [gamma/stream/temp_memory.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/temp_memory.py)
  Ephemeral stream memory store for short-lived stream context.

- [gamma/stream/self_goals.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/self_goals.py)
  Proposed self-goal storage and approve/reject/clear workflow.

### Performer Output Bus

- [gamma/performer/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/models.py)
  Generic performer output event models and mapping from stream output events. This is where subtitle, speech, expression, motion, and clear events become runtime-agnostic performer events.

- [gamma/performer/bus.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/bus.py)
  In-process performer event bus with recent history and asyncio subscriber queues.

- [gamma/performer/static/performer.html](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/static/performer.html)
  Minimal Stream PC / OBS browser-source page. It connects to `/v1/performer/events`, shows subtitles/state, and plays audio from network-safe `audio_url` payloads.

Performer API routes currently live in [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py):
- `GET /performer`
- `GET /performer/assets/shana/default.png`
- `GET /v1/performer/events/recent`
- `WebSocket /v1/performer/events`
- `GET /v1/audio/artifacts/{filename}`

### Twitch Integration

- [gamma/integrations/twitch/worker.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/worker.py)
  Twitch IRC ingestion worker. Reads chat, applies Twitch controls, and posts normalized events to the stream API.

- [gamma/integrations/twitch/eventsub.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/eventsub.py)
  Twitch EventSub websocket worker for follows, raids, redeems, bits, subscriptions, and moderation-style events.

- [gamma/integrations/twitch/normalize.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/normalize.py)
  Maps Twitch IRC/EventSub/replay payloads into `StreamInputEvent` records.

- [gamma/integrations/twitch/sanitize.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/sanitize.py)
  Twitch chat safety/trust classification before events reach the stream brain.

- [gamma/integrations/twitch/trust.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/trust.py)
  Viewer trust store and pronunciation/notes metadata.

- [gamma/integrations/twitch/replay.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/replay.py)
  JSONL replay utility for testing Twitch-style events through the stream API.

- [gamma/integrations/twitch/client.py](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch/client.py)
  HTTP client used by Twitch workers/replay to post stream events to Gamma.

### Dashboard

- [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
  Dashboard FastAPI app and dashboard API routes.

- [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)
  Dashboard-facing orchestration: status payloads, provider test actions, memory operations, audio serving, stream/Twitch status, Twitch runtime settings, viewer trust, and replay actions.

- [gamma/dashboard/auth.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/auth.py)
  Dashboard auth helpers.

- [gamma/dashboard/static/index.html](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/index.html)
  Dashboard UI shell.

- [gamma/dashboard/static/dashboard.js](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.js)
  Dashboard behavior. This file owns:
  - live browser voice control
  - browser recording/upload
  - provider actions
  - subtitles UI
  - memory delete modal
  - mute mic / mute assistant toggles
  - stream trace/output/queue/temp-memory/self-goal panels
  - Twitch worker/EventSub/settings/viewer-trust/replay controls

- [gamma/dashboard/static/dashboard.css](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.css)
  Dashboard styling.

### Process Supervision

- [gamma/supervisor/manager.py](/home/neety/.openclaw/workspace/gamma-main/gamma/supervisor/manager.py)
  Starts/stops `shana`, `dashboard`, `twitch-worker`, `twitch-eventsub`, plus managed local TTS sidecars.

- [gamma/supervisor/cli.py](/home/neety/.openclaw/workspace/gamma-main/gamma/supervisor/cli.py)
  CLI wrapper around the process manager.

### Config

- [gamma/config.py](/home/neety/.openclaw/workspace/gamma-main/gamma/config.py)
  Central settings loader from `.env` and `config/*.toml`, including dashboard, voice, stream safety, and Twitch settings.

- `.env`
  Local active environment config in this workspace.

- `config/app.example.toml`, `config/app.toml`, `config/app.local.toml`
  App config layering.

- `config/voices.example.toml`, `config/voices.toml`, `config/voices.local.toml`
  Voice profile layering.

### Scripts And Runtime Bundles

- `scripts/start_shana_voice_stack.py`, `scripts/start_shana_voice_stack_linux.sh`
  Starts the voice-focused runtime bundle.

- `scripts/start_shana_stream_stack.py`, `scripts/start_shana_stream_stack_linux.sh`
  Starts the stream-focused runtime bundle.

- `scripts/start_qwen_tts_server.py`, `scripts/stop_qwen_tts_server.py`, `scripts/qwen_tts_server.py`
  Qwen TTS sidecar helpers.

- `scripts/start_gpt_sovits_*`, `scripts/stop_gpt_sovits_*`
  GPT-SoVITS sidecar helpers.

## Current File Structure

Use this as the high-level map before editing:

- `gamma/api/` - assistant API routes for conversation, memory, system status, stream, vision, and voice
- `gamma/avatar_events/` - downstream avatar event models
- `gamma/conversation/` - main assistant response pipeline
- `gamma/dashboard/` - operator dashboard app, service layer, auth, and static UI assets
- `gamma/identity/` - speaker profile and owner/known-user resolution
- `gamma/integrations/twitch/` - Twitch IRC/EventSub/replay/client/safety/trust adapters
- `gamma/llm/` - mock, OpenAI, local/Ollama, and router LLM adapters
- `gamma/memory/` - SQLModel memory models and SQLite-backed memory service
- `gamma/persona/` - Shana prompt source files, emotional state, and persona loaders
- `gamma/performer/` - performer output event models, in-process output bus, and Stream PC browser performer page
- `gamma/safety/` - privacy guard, speech filter, hard rules, heuristic checks, LLM reviewer, and rewrite guard
- `gamma/schemas/` - API schema models
- `gamma/stream/` - stream brain, models, traces, output log, replay, temporary memory, and self-goals
- `gamma/supervisor/` - process manager and CLI
- `gamma/system/` - runtime status, CUDA/Torch helpers, Python resolution, lazy singletons
- `gamma/tools/` - assistant tool registry and built-in tools
- `gamma/tray/` - desktop tray app wrapper
- `gamma/vision/` - image analysis service
- `gamma/voice/` - STT/TTS, live voice, live jobs/runtime, roundtrip, controller, affect, reply planning/chunking, RVC, and voice profiles
- `config/` - layered app/model/memory/persona/voice config
- `scripts/` - platform launchers, service starters/stoppers, and TTS sidecar scripts
- `specs/` - product, architecture, implementation, voice, memory, model, stream, Twitch, and handoff docs
- `tests/` - unit/integration tests for API, dashboard, voice, stream, Twitch, memory, routing, and system helpers

## Current Important Environment / Deployment Facts

### Dashboard Behind HTTPS Proxy

Current intended deployment model:

- dashboard process binds locally on port `8001`
- reverse proxy terminates HTTPS and exposes `https://gamma.neety.me`
- dashboard public URL is configured separately from the bind port

Important settings:

- `SHANA_DASHBOARD_BIND_HOST`
- `SHANA_DASHBOARD_PORT`
- `SHANA_DASHBOARD_PUBLIC_HOST`
- `SHANA_DASHBOARD_PUBLIC_PORT`
- `SHANA_DASHBOARD_PUBLIC_SCHEME`
- `SHANA_DASHBOARD_COOKIE_SECURE`

Do not collapse public proxy port and internal bind port again. They are intentionally separated.

### Browser DNS / Local HTTPS

The dashboard may be used behind Nginx Proxy Manager or another reverse proxy on LAN. Browsers may need local DNS overrides or hosts-file overrides if secure DNS bypasses LAN DNS.

### Live Browser Voice

The browser live voice path currently uses `ScriptProcessorNode` for audio capture. Browsers warn that it is deprecated. Follow-up cleanup should migrate this to `AudioWorkletNode`.

This is a cleanup task, not a known blocker for the currently working flow.

### Stream / Twitch Operation

The current stream path is operator-supervised. Twitch workers can run in dry-run or speech-enabled modes, and stream-facing actions should remain safe by default.

Important settings include:

- `SHANA_TWITCH_*` for IRC/EventSub credentials, runtime controls, dry-run behavior, speech/subtitle enablement, safety review, and rate limits
- `SHANA_STREAM_*` for proactive idle behavior, stream safety review timeout, fallback behavior, output paths, and queue/temp-memory/self-goal behavior

Dashboard controls exist for:

- Twitch IRC worker start/stop/status
- Twitch EventSub worker start/stop/status
- Twitch runtime setting toggles
- viewer trust save/list
- replay and dry-run replay
- stream traces, safety findings, outputs, queue, temp memory, self-goals, and stream stop

Preserve operator review points for potentially public speech. Avoid making Twitch speech-on behavior the default unless the config already explicitly enables it.

### Performer / Stream PC Output

The new performer path is the first implementation slice of `specs/shana_output_bus.md`.

Current behavior:

- Stream output events are still persisted to JSONL.
- The same stream output events are also mapped into generic performer events.
- Performer clients can subscribe to `WebSocket /v1/performer/events`.
- Clients can request recent events through `GET /v1/performer/events/recent`.
- The `/performer` page is intended as a simple Stream PC / OBS browser source.
- Audio payloads should be network-safe. If a stream event has an `audio_path` inside `settings.audio_output_dir`, the performer payload removes the local path and exposes:
  - `audio_artifact`
  - `audio_url`
- The Shana API serves those artifacts through `GET /v1/audio/artifacts/{filename}`.

Important design intent:

- Keep Gamma/StreamBrain runtime-agnostic. Do not put VTube Studio-specific logic in `ConversationService`, `StreamBrain`, or core voice code.
- The current `/performer` page is a first browser client, not the final VTuber adapter.
- Future VTube Studio support should translate generic performer events into VTS API calls in an adapter/client layer.

## Current Provider Defaults In Practice

The exact local `.env` can vary, but the repo is currently designed around combinations like:

- LLM: local/Ollama
- STT: local faster-whisper
- TTS: Qwen3-TTS or GPT-SoVITS

The current local STT path is whisper-based:

- `SHANA_STT_PROVIDER=local`
- implemented through faster-whisper in [gamma/voice/stt.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/stt.py)

## Memory Model

There are two stored memory types:

1. `ProfileFact`
   Semi-stable user/person facts.

2. `EpisodicMemory`
   Event-ish memories with optional session scope.

Important current behavior:

- recent-memory listing exists in the dashboard
- targeted recent-memory deletion exists in the dashboard
- memory rows now carry `created_at`
- dashboard "clear memory" flow is intentionally no longer a one-click wipe

If you touch memory behavior, preserve:

- selective writing
- subject scoping
- recent-memory visibility
- safer deletion semantics

## Dashboard UX Behaviors That Matter

Current dashboard browser voice UX includes:

- `Mute Mic`
- `Mute Shana`
- pop-out subtitle window
- live subtitle text updated on transcript / reply chunk events
- memory-delete modal with per-item selection

If you change these, keep the operator-facing workflow simple. The dashboard is now an active control surface, not just a debug page.

## Platform Notes

Gamma is expected to stay runnable on both Linux and Windows.

Rules:

- shared runtime logic should not assume Windows-only paths or executables
- platform-specific wrappers are acceptable in `scripts/`
- do not break Windows compatibility just to improve Linux
- do not break Linux proxy/browser workflow just to preserve an old Windows-only assumption

Important recent portability work:

- safer CUDA device selection
- Qwen3-TTS Linux startup fixes
- dashboard public-vs-bind URL separation
- browser voice path validated through Linux-hosted dashboard + Windows client browser

## What To Check Before Editing

If you are changing browser voice or dashboard controls:

1. Check [gamma/dashboard/static/index.html](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/index.html)
2. Check [gamma/dashboard/static/dashboard.js](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.js)
3. Check [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
4. Check [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)

If you are changing memory:

1. Check [gamma/memory/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/models.py)
2. Check [gamma/memory/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/service.py)
3. Check dashboard memory routes/UI if the behavior is operator-visible

If you are changing provider behavior:

1. Check [gamma/voice/stt.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/stt.py)
2. Check [gamma/voice/tts.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/tts.py)
3. Check [gamma/supervisor/manager.py](/home/neety/.openclaw/workspace/gamma-main/gamma/supervisor/manager.py)
4. Check relevant scripts in `scripts/`

If you are changing stream/Twitch behavior:

1. Check [gamma/stream/brain.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/brain.py)
2. Check [gamma/stream/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/models.py)
3. Check [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py)
4. Check [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
5. Check [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)
6. Check [gamma/dashboard/static/dashboard.js](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.js)
7. Check relevant Twitch adapter files under [gamma/integrations/twitch/](/home/neety/.openclaw/workspace/gamma-main/gamma/integrations/twitch)

If you are changing performer/output-bus behavior:

1. Check [specs/shana_output_bus.md](/home/neety/.openclaw/workspace/gamma-main/specs/shana_output_bus.md)
2. Check [gamma/performer/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/models.py)
3. Check [gamma/performer/bus.py](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/bus.py)
4. Check [gamma/stream/output.py](/home/neety/.openclaw/workspace/gamma-main/gamma/stream/output.py)
5. Check [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py)
6. Check [gamma/performer/static/performer.html](/home/neety/.openclaw/workspace/gamma-main/gamma/performer/static/performer.html)

## Test / Validation Expectations

Current useful validation commands:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m gamma.supervisor.cli start dashboard
./.venv/bin/python -m gamma.supervisor.cli start shana
./.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

`pytest` is intentionally scoped to `tests/` in `pyproject.toml`; do not remove that unless vendored/sidecar test directories under `data/` are excluded another way.

Useful provider smoke tests:

```bash
./.venv/bin/python -m gamma.run_llm_test "Dashboard LLM smoke test."
./.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
./.venv/bin/python -m gamma.run_tts_test "Dashboard TTS smoke test."
./.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac
```

Useful focused tests:

```bash
./.venv/bin/python -m pytest tests/test_stream_brain.py tests/test_stream_output.py tests/test_twitch_integration.py -v
./.venv/bin/python -m pytest tests/test_dashboard_routes.py tests/test_api_routes.py -v
```

## Handoff Guidance

When taking over work in this repo:

- assume dashboard/browser voice and Twitch/stream operator paths matter more than old speculative roadmap text
- prefer small, concrete fixes over broad architecture churn
- preserve both Linux and Windows runnability
- if behavior is operator-facing, verify both backend route support and dashboard JS/UI behavior
- if behavior can produce public stream speech, verify safety, dry-run, queueing, and operator controls
- if browser behavior seems inconsistent, suspect stale static assets or proxy/browser caching before overcomplicating the logic

For a condensed version, see [specs/llm-handoff-prompt-lite.md](/home/neety/.openclaw/workspace/gamma-main/specs/llm-handoff-prompt-lite.md).
