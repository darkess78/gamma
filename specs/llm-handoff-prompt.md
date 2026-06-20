# Gamma LLM Handoff Prompt

Use this document to orient yourself to the current Gamma repo before making
changes. It is meant for handoff between coding agents and should reflect the
repo as it exists now, not an old roadmap.

Last refreshed: 2026-06-20, against the current committed implementation plus
the existing working-tree Presence notes.

## What Gamma Is

Gamma is a local assistant stack centered on the Shana persona. It has four
main runtime surfaces:

1. Assistant backend
   `src/gamma/main.py` starts the Shana FastAPI app. It owns conversation,
   memory, vision, STT, TTS, live voice jobs, stream events, performer output,
   and Shana-owned `/v1/*` routes.

2. Dashboard
   `src/gamma/dashboard/main.py` starts a separate FastAPI app. It owns the
   operator dashboard, dashboard `/api/*` routes, browser live voice UI,
   status panels, settings, stream/Twitch controls, Presence controls, monitor
   views, and dashboard static assets.

3. Voice and audio
   Gamma supports browser voice roundtrip, browser live voice over websocket,
   CLI voice loops, local/hosted STT, local/hosted TTS, optional RVC
   post-processing, and a persistent audio-understanding sidecar for speaker
   emotion/audio events.

4. Stream and output
   Stream events flow through stream decision logic, safety review, queueing,
   temporary stream memory, self-goal review, Twitch/Discord ingestion, and the
   performer output bus. Performer clients can consume generic subtitle,
   speech, expression, motion, and clear events.

The current practical focus is the dashboard/browser voice workflow, Twitch and
stream operator workflow, Presence-controlled public output, resource-aware
local model placement, and the performer output path.

## Current State

Working support includes:

- Shana backend and dashboard process supervision.
- Separate Shana API on port `8000` and dashboard app on port `8001`.
- Browser live voice through dashboard `WebSocket /api/voice/live`.
- Browser voice roundtrip through dashboard `POST /api/voice/roundtrip`.
- Shana voice APIs under `/v1/voice/*`.
- Local TTS through Piper or Qwen TTS, hosted TTS through OpenAI, and test-only
  stub TTS.
- Local STT through faster-whisper, hosted STT through OpenAI, and test stub
  transcription.
- Optional audio-understanding sidecar on loopback port `9883`.
- Dashboard auth, status, settings, memory maintenance, live voice, monitor,
  Presence, stream, Twitch, Discord text-worker, and provider controls.
- HTTPS dashboard use behind a reverse proxy with separate bind URL and public
  URL settings.
- Stream event handling through `POST /v1/stream/events`.
- Twitch IRC ingestion, Twitch EventSub ingestion, replay tooling, viewer trust,
  and dashboard runtime controls.
- Discord text ingestion worker for one allowlisted guild/channel; Discord
  replies and Discord voice transport remain deferred.
- Stream output logging, performer output events, pending speech queue,
  temporary stream memory, self-goal review, and stream replay/eval inspection.
- VTube Studio adapter/client/runner for translating performer events into VTS
  hotkey requests. It is disabled by default and still needs live machine-local
  validation/configuration.
- Resource telemetry, shadow placement, endpoint-aware local LLM routing behind
  a feature flag, startup-only device admission for sidecars behind a feature
  flag, and observed sidecar allocation logs.

## Recent Important Changes

- The package uses a `src/` layout. Authoritative source is under
  `src/gamma`, not the stale top-level `gamma/` runtime bytecode/log folder.
- Dashboard navigation is page-oriented:
  `/dashboard`, `/dashboard/live`, `/dashboard/monitor`, `/dashboard/status`,
  `/dashboard/presence`, `/dashboard/stream`, `/dashboard/memory`, and
  `/dashboard/settings`.
- Dashboard-owned public/browser routes include `/dashboard*`, `/api/*`,
  `/static/*`, `/login`, `/logout`, `/monitor*`, and `/overlay/*`.
- Shana-owned routes include `/health`, `/v1/*`, `/performer*`, and
  `/stream/*`.
- `/dashboard/twitch` redirects to `/dashboard/stream`; `/monitor` redirects to
  `/dashboard/monitor`.
- The Shana API redirects valid dashboard page requests to the configured
  dashboard public URL so misrouted browser links do not become JSON 404s.
- Dashboard links use `SHANA_DASHBOARD_PUBLIC_*` / `settings.dashboard_base_url`
  rather than assuming root-relative links are always public-safe.
- The dashboard navbar includes page links, Performer/Subtitles output links,
  compact status chips, a status dropdown, mobile menu behavior, and a permanent
  `Stop Output` control.
- `Stop Output` stops current speech/output and clears `dashboard_monitor`,
  `stream_public`, and `discord_call` performer targets without killing Shana,
  dashboard, Twitch, EventSub, or ingestion workers.
- Presence state exists for Shana lifecycle modes `sleep`, `wake`, `go_live`,
  and `break`. It gates stream-facing autonomy/output separately from backend
  process controls. Stale persisted `go_live` state is downgraded after Shana
  restart until confirmed again.
- `/dashboard/monitor` is the minimalist monitor view; `/overlay/subtitles` is
  the subtitle overlay; `/performer` is served by Shana for Stream PC / OBS
  browser-source testing.
- The performer bus has target policies, recent history, replay resume, replay
  gap reporting, per-target control events, VTube Studio adapter status, Discord
  output isolation, and a derived in-memory spoken-turn store.
- Audio artifacts are exposed with network-safe URLs through
  `GET /v1/audio/artifacts/{filename}`; performer clients should not depend on
  local file paths from the Shana PC.
- Resource routing can define named runtime endpoints and resource targets.
  Shadow ranking and reservation metadata are advisory by default. Active local
  LLM endpoint routing is behind `resource_routing.policy.active_llm_routing`.
- Startup admission for Qwen TTS and audio-understanding sidecars is behind
  `resource_routing.policy.startup_admission` and only changes `auto` device
  settings. Explicit configured devices bypass admission.

## Architecture Map

### Backend

- `src/gamma/main.py`
  Shana FastAPI entrypoint.

- `src/gamma/api/routes.py`
  Shana API routes for conversation, memory, status, stream events, stream
  logs, queues, temp memory, self-goals, stream stop, performer events/status,
  VTube Studio runner controls, performer target controls, audio artifacts,
  vision, voice, monitor redirects, overlay page, and dashboard redirects.

- `src/gamma/conversation/service.py`
  Main conversation pipeline. Builds persona/system prompt, resolves speaker
  context, retrieves memory context, runs LLM draft generation, optional
  metadata/tool extraction, memory persistence, speech filtering, and optional
  TTS.

- `src/gamma/persona/loader.py`
  Prompt assembly from `src/gamma/persona/` and `config/persona.yaml`.

- `src/gamma/memory/service.py`
  SQLite/SQLModel memory layer. Stores profile facts, episodic memories, known
  people, platform account links, recent memory lists, targeted deletion, and
  core-memory append support.

- `src/gamma/presence.py`
  Presence state construction, stale live-state downgrade, and stream-event
  gating helpers.

- `src/gamma/llm/`
  Mock, OpenAI, local/Ollama, capability probing, and router adapters. The
  router supports local/default/hosted paths, balanced small-model routing,
  hosted escalation, fallback/backoff, route traces, and optional
  resource-placement metadata.

### Voice And Audio

- `src/gamma/voice/stt.py`
  STT provider selection. Current local STT path is faster-whisper.

- `src/gamma/voice/tts.py`
  TTS provider selection and synthesis. Runtime providers are OpenAI, Piper,
  Qwen TTS, and test stub. RVC is an optional post-process layer, not a
  standalone provider.

- `src/gamma/voice/live.py`
  Dashboard live voice websocket session manager.

- `src/gamma/voice/live_jobs.py`
  Background live-turn job manager with subprocess isolation/cancellation.

- `src/gamma/run_live_voice_worker.py`
  Worker process used for live turns.

- `src/gamma/voice/audio_understanding.py` and
  `src/gamma/audio_understanding_server.py`
  Prosody, optional model-backed speaker emotion/audio events, and loopback
  sidecar server.

- `scripts/start_qwen_tts_server.py`, `scripts/stop_qwen_tts_server.py`, and
  `scripts/qwen_tts_server.py`
  Qwen TTS sidecar helpers.

### Stream Brain And Output

- `src/gamma/stream/brain.py`
  Stream decision engine. Classifies chat/events, applies runtime controls,
  safety policy, Presence gating metadata, conversation calls, speech queueing,
  fallback audio, self-goal proposals, and output emission.

- `src/gamma/stream/actions.py`
  Converts stream decisions and assistant replies into action plans.

- `src/gamma/stream/models.py`
  Shared stream input, decision, action, and result models.

- `src/gamma/stream/output.py`
  Stream output dispatch. The default dispatcher persists JSONL output records
  and publishes generic performer events.

- `src/gamma/stream/trace.py`
  Stream trace persistence and recent trace reading.

- `src/gamma/stream/replay.py`
  Recent stream turn replay/evaluation helpers.

- `src/gamma/stream/temp_memory.py`
  Temporary stream memory for bounded background context.

- `src/gamma/stream/self_goals.py`
  Proposed self-goal storage and approve/reject/clear workflow.

### Performer Output Bus

- `src/gamma/performer/models.py`
  Generic performer output event models and mapping from stream output events.
  Target policies include `dashboard_monitor`, `stream_public`, and
  `discord_call`.

- `src/gamma/performer/bus.py`
  In-process performer bus with recent history, per-target mute/clear control
  events, websocket subscribers, replay resume, and state persistence.

- `src/gamma/performer/turns.py`
  Derived spoken-turn view over recent performer events.

- `src/gamma/performer/vtube_studio.py`
  VTube Studio config, websocket client, adapter, and runner.

- `src/gamma/performer/static/performer.html`
  Stream PC / OBS browser-source page for subtitles, state, and Shana audio.

Shana performer routes currently include:

- `GET /performer`
- `GET /performer/assets/shana/default.png`
- `GET /v1/performer/events/recent`
- `GET /v1/performer/status`
- `POST /v1/performer/adapters/vtube-studio/start`
- `POST /v1/performer/adapters/vtube-studio/stop`
- `POST /v1/performer/targets/{target_policy}/mute`
- `POST /v1/performer/targets/{target_policy}/unmute`
- `POST /v1/performer/targets/{target_policy}/clear`
- `WebSocket /v1/performer/events`
- `GET /v1/audio/artifacts/{filename}`
- `GET /overlay/subtitles`

### Integrations

- `src/gamma/integrations/twitch/worker.py`
  Twitch IRC ingestion worker.

- `src/gamma/integrations/twitch/eventsub.py`
  Twitch EventSub websocket worker.

- `src/gamma/integrations/twitch/normalize.py`
  Twitch IRC/EventSub/replay normalization into `StreamInputEvent`.

- `src/gamma/integrations/twitch/sanitize.py`
  Twitch chat trust/safety classification.

- `src/gamma/integrations/twitch/trust.py`
  Viewer trust, pronunciation alias, and notes store.

- `src/gamma/integrations/twitch/replay.py`
  JSONL replay utility for repeatable stream tests.

- `src/gamma/integrations/discord/adapter.py`
  Discord text/voice normalization into stream events.

- `src/gamma/integrations/discord/runtime.py`
  Dependency-light Discord runtime and isolated `discord_call` output handling.

- `src/gamma/integrations/discord/worker.py`
  Optional `discord.py` text ingestion worker. It ignores bot/self messages,
  other guilds/channels, and empty messages before posting accepted text to the
  stream API with speech disabled.

### Dashboard

- `src/gamma/dashboard/main.py`
  Dashboard FastAPI app and dashboard API routes.

- `src/gamma/dashboard/service.py`
  Dashboard orchestration: status payloads, Presence, provider actions, memory,
  stream/Twitch/Discord operations, performer controls, monitor input, replay,
  settings, logs, resource panels, and proxying Shana-owned APIs.

- `src/gamma/dashboard/auth.py`
  Dashboard session auth helpers.

- `src/gamma/dashboard/static/index.html`
  Dashboard shell.

- `src/gamma/dashboard/static/*.js`
  Modular dashboard browser code. Use the relevant module instead of restoring
  logic to a legacy monolithic `dashboard.js`.

Important modules include:

- `api.js`
- `controls.js`
- `init.js`
- `live.js`
- `memory.js`
- `monitor.js`
- `nav.js`
- `presence.js`
- `providers.js`
- `render.js`
- `status.js`
- `stream.js`

### Resource Telemetry And Routing

- `src/gamma/resources/probe.py`
  Shared read-only resource snapshots with CPU, RAM, disk, GPU, and optional GPU
  compute-process attribution.

- `src/gamma/resources/models.py`, `policy.py`, `coordinator.py`, and
  `runtime_registry.py`
  Resource targets, endpoint refs, workload specs, deterministic ranking, shadow
  policy, and target validation.

- `src/gamma/resources/allocations.py`
  Reads recent sidecar allocation observations from structured logs.

Resource-routing behavior is intentionally conservative:

- Shadow placement is advisory unless active routing is explicitly enabled.
- Endpoint-aware local LLM routing is behind
  `resource_routing.policy.active_llm_routing = false` by default.
- Startup admission is behind
  `resource_routing.policy.startup_admission = false` by default.
- Automatic model loading, model eviction, managed GPU endpoint startup, and
  measured persistent-allocation reconciliation remain future work.

### Process Supervision

- `src/gamma/supervisor/manager.py`
  Starts/stops `shana`, `dashboard`, `audio-understanding`, Twitch workers, and
  local sidecars where supported.

- `src/gamma/supervisor/cli.py`
  CLI wrapper around the process manager.

Use:

```bash
.venv/bin/python -m gamma.supervisor.cli status all
.venv/bin/python -m gamma.supervisor.cli restart shana
.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

## Current File Structure

- `src/gamma/api/` - Shana API routes.
- `src/gamma/avatar_events/` - downstream avatar event models.
- `src/gamma/conversation/` - main assistant response pipeline.
- `src/gamma/dashboard/` - dashboard app, service layer, auth, and static UI.
- `src/gamma/identity/` - speaker profiles and known-user resolution.
- `src/gamma/integrations/` - Twitch and Discord adapters/workers.
- `src/gamma/llm/` - mock, OpenAI, local/Ollama, and router LLM adapters.
- `src/gamma/memory/` - SQLModel memory service.
- `src/gamma/performer/` - performer event bus, models, VTube Studio adapter,
  and browser performer page.
- `src/gamma/persona/` - Shana prompt source files and assistant state.
- `src/gamma/presence.py` - Presence state and stream gating helpers.
- `src/gamma/resources/` - resource telemetry and placement policy.
- `src/gamma/safety/` - privacy guard, speech filter, and safety review layers.
- `src/gamma/stream/` - stream brain, output, traces, replay, temp memory, and
  self-goals.
- `src/gamma/supervisor/` - process manager and CLI.
- `src/gamma/system/` - runtime status, CUDA/Torch helpers, Python resolution,
  and lazy singletons.
- `src/gamma/tools/` - assistant tool registry and built-in tools.
- `src/gamma/voice/` - STT, TTS, live voice, audio understanding, reply
  planning, RVC, and voice profiles.
- `config/` - layered app, model, memory, persona, voice, integration, and
  resource-routing config.
- `scripts/` - launchers, service scripts, and sidecar helpers.
- `specs/` - architecture and behavior docs.
- `tests/` - pytest coverage.

The untracked top-level `gamma/` directory is stale runtime bytecode/log state,
not source. Do not import from it or add files there.

## Deployment Facts

For networking, Nginx/OpenResty, ports, public URLs, dashboard startup, or
proxy routing, read `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` first. Do not
edit that file.

Current intended deployment model:

- Shana API binds internally on port `8000`.
- Dashboard binds internally on port `8001`.
- A reverse proxy terminates HTTPS and exposes the public dashboard/API URLs.
- Public dashboard `/dashboard/*`, dashboard `/api/*`, dashboard `/static/*`,
  `/login`, `/logout`, `/monitor*`, and `/overlay/*` browser requests should go
  to the dashboard process.
- Shana-owned `/health`, `/v1/*`, `/performer*`, and `/stream/*` requests
  should go to the Shana process.
- Public scheme/host/port are separate from bind host/port. Preserve
  `SHANA_*_PUBLIC_SCHEME`, `SHANA_*_PUBLIC_HOST`, and
  `SHANA_*_PUBLIC_PORT`.

Do not collapse public proxy port and internal bind port. They are intentionally
separate.

## Provider Defaults In Practice

Exact local `.env` values vary. The repo is designed around combinations like:

- LLM: `mock`, `openai`, `local`, `ollama`, or router-managed combinations.
- STT: `stub`, `openai`, `local`, or `faster-whisper`.
- TTS: `stub`, `openai`, `piper`, or `qwen-tts`.

Local does not mean the same thing everywhere:

- LLM `local` means an Ollama-compatible model.
- STT `local` means faster-whisper.
- TTS `piper` means local offline ONNX synthesis.
- TTS `qwen-tts` means a local HTTP-backed sidecar.

RVC can layer on top of generated WAV output. It is not a standalone TTS
provider.

## Dashboard And Operator UX

Current dashboard browser/operator behavior includes:

- Page-based navigation with overview, live, monitor, status, Presence, stream,
  memory, and settings sections.
- `Mute Mic`, `Mute Shana`, pop-out subtitles, monitor audio enable/mute, and
  compact/focus monitor modes.
- Presence controls for Sleep, Wake, Go Live, and Break.
- Provider status, provider tests, voice profile editor, TTS profile selection,
  Qwen sidecar start/stop, and generated audio management.
- Memory stats, known people, latest/recent memories, targeted edit/delete, and
  safer cleanup controls.
- Stream traces, safety findings, output events, queue, temporary memory,
  self-goals, rehearsal, local monitor input, and stream stop.
- Twitch IRC/EventSub controls, runtime settings, viewer trust, replay, and
  dry-run replay.
- Discord text-worker start/stop/status.
- Resource telemetry panels for placement shadow, startup admission, and sidecar
  allocation observations.

If behavior is operator-facing, verify both backend route support and dashboard
JS/UI behavior.

## Safety And Public Output

Public stream speech should stay operator-supervised and safe by default.

Preserve:

- Privacy guard checks before LLM calls.
- Speech safety filtering before spoken text is returned or synthesized.
- Stream safety review timeout/fallback behavior.
- Dry-run, queueing, rate limits, and operator review points.
- Presence gating for public stream output.
- Separation between dashboard monitor output, stream-public output, and
  Discord-call output.

Do not weaken authentication, privacy, speech safety, or stream moderation to
make a test pass.

## What To Check Before Editing

For dashboard/browser changes:

1. `src/gamma/dashboard/static/index.html`
2. The relevant module in `src/gamma/dashboard/static/`
3. `src/gamma/dashboard/main.py`
4. `src/gamma/dashboard/service.py`

For Presence:

1. `src/gamma/presence.py`
2. `src/gamma/dashboard/service.py`
3. `src/gamma/dashboard/main.py`
4. `src/gamma/dashboard/static/presence.js`
5. `src/gamma/api/routes.py`
6. `src/gamma/stream/brain.py`

For memory:

1. `src/gamma/memory/models.py`
2. `src/gamma/memory/service.py`
3. Dashboard memory routes/UI if operator-visible

For provider behavior:

1. `src/gamma/voice/stt.py`
2. `src/gamma/voice/tts.py`
3. `src/gamma/voice/voice_profiles.py`
4. `src/gamma/supervisor/manager.py`
5. Relevant scripts in `scripts/`

For LLM routing/resource behavior:

1. `src/gamma/llm/router_adapter.py`
2. `src/gamma/resources/`
3. `src/gamma/supervisor/manager.py`
4. `config/app.example.toml`
5. Dashboard status/resource panels

For stream/Twitch/Discord behavior:

1. `src/gamma/stream/brain.py`
2. `src/gamma/stream/models.py`
3. `src/gamma/api/routes.py`
4. `src/gamma/dashboard/main.py`
5. `src/gamma/dashboard/service.py`
6. Relevant integration files under `src/gamma/integrations/`

For performer/output-bus behavior:

1. `specs/shana_output_bus.md`
2. `src/gamma/performer/models.py`
3. `src/gamma/performer/bus.py`
4. `src/gamma/performer/turns.py`
5. `src/gamma/performer/vtube_studio.py`
6. `src/gamma/stream/output.py`
7. `src/gamma/api/routes.py`
8. `src/gamma/performer/static/performer.html`

## Validation Expectations

Use the repo virtual environment:

```bash
.venv/bin/python
```

Focused tests:

```bash
.venv/bin/python -m pytest tests/test_dashboard_routes.py tests/test_api_routes.py -q
.venv/bin/python -m pytest tests/test_stream_brain.py tests/test_stream_output.py -q
.venv/bin/python -m pytest tests/test_llm_router.py tests/test_resource_policy.py -q
.venv/bin/python -m pytest tests/test_audio_sidecar_runtime.py tests/test_sidecar_allocations.py -q
.venv/bin/python -m pytest tests/test_vtube_studio_adapter.py tests/test_discord_adapter.py -q
.venv/bin/python -m pytest tests/test_presence.py -q
```

Full suite:

```bash
.venv/bin/python -m pytest -q
```

For changed dashboard JS modules, run:

```bash
node --check src/gamma/dashboard/static/<changed-file>.js
```

Useful smoke tests:

```bash
.venv/bin/python -m gamma.run_llm_test "Dashboard LLM smoke test."
.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
.venv/bin/python -m gamma.run_tts_test "Dashboard TTS smoke test."
.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac
.venv/bin/python -m gamma.run_audio_understanding_test ./sample.wav --transcript "sample transcript"
```

Before finishing changes, run `git diff --check` and report which tests ran.

## Handoff Guidance

When taking over work in this repo:

- Read `README.md`, `specs/README.md`, `specs/current_implementations.md`, and
  the relevant domain spec before editing.
- For networking/proxy/service-port changes, read
  `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` first and do not edit it.
- Prefer small, concrete fixes over broad architecture churn.
- Preserve both Linux and Windows runnability.
- Keep the two FastAPI apps separate.
- Use structured config/TOML/JSON parsing instead of ad hoc string edits.
- Do not edit `.env`, `config/app.local.toml`, or `config/voices.local.toml`
  unless the user explicitly asks for a machine-local config change.
- Do not touch generated runtime data under `data/` or stale top-level
  `gamma/`.
- If browser behavior seems inconsistent, check stale static assets or proxy
  caching before overcomplicating the implementation.

For a condensed version, see `specs/llm-handoff-prompt-lite.md`.
