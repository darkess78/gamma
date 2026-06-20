# Gamma LLM Handoff Prompt Lite

Quick orientation for the current repo state. For full context, see
`specs/llm-handoff-prompt.md`.

Last refreshed: 2026-06-20.

## What Gamma Is

Gamma is a local Shana assistant stack with:

- Shana FastAPI backend in `src/gamma/main.py`.
- Separate dashboard FastAPI app in `src/gamma/dashboard/main.py`.
- Browser live voice, voice roundtrip, CLI voice, STT, TTS, and optional audio
  understanding.
- SQLite/SQLModel memory with known people, platform identities, profile facts,
  episodic memories, and safer dashboard cleanup.
- Pluggable LLM, STT, and TTS backends.
- Stream brain, Twitch controls, Discord text ingestion, safety review,
  temporary stream memory, self-goals, and operator review points.
- Performer output bus, `/performer`, `/overlay/subtitles`, VTube Studio
  adapter support, and target policies for `dashboard_monitor`,
  `stream_public`, and `discord_call`.
- Resource telemetry, advisory placement shadow logs, optional endpoint-aware
  local LLM routing, and optional startup admission for local sidecars.

Authoritative source is `src/gamma`. The top-level `gamma/` directory is stale
runtime bytecode/log state and should not be used as source.

## Important Files

- `src/gamma/api/routes.py`
  Shana routes for conversation, memory, status, stream events, performer
  events/status, VTube Studio runner controls, target mute/clear, audio
  artifacts, vision, voice, dashboard redirects, and overlays.

- `src/gamma/dashboard/main.py`
  Dashboard pages and dashboard `/api/*` routes.

- `src/gamma/dashboard/service.py`
  Dashboard orchestration for status, Presence, providers, memory, stream,
  Twitch, Discord text-worker, performer controls, monitor input, settings, and
  resource panels.

- `src/gamma/dashboard/static/`
  Modular dashboard JS/CSS/HTML. Edit the relevant module; do not restore logic
  to a legacy monolithic `dashboard.js`.

- `src/gamma/presence.py`
  Shana Presence modes and stream-output gating.

- `src/gamma/conversation/service.py`
  Persona, memory, LLM draft, metadata/tool extraction, safety filtering, and
  optional TTS.

- `src/gamma/llm/router_adapter.py`
  Balanced local/default/hosted routing, hosted escalation, fallback/backoff,
  route logs, resource-placement metadata, and optional endpoint-aware local
  routing.

- `src/gamma/resources/`
  Resource snapshots, target validation, workload ranking, endpoint refs,
  shadow policy, startup admission, and sidecar allocation log parsing.

- `src/gamma/voice/stt.py`
  STT provider selection. Local STT is faster-whisper.

- `src/gamma/voice/tts.py`
  TTS provider selection. Runtime providers are OpenAI, Piper, Qwen TTS, and
  test stub. RVC is an optional post-process.

- `src/gamma/voice/audio_understanding.py` and
  `src/gamma/audio_understanding_server.py`
  Prosody, optional model-backed speaker emotion/audio events, and the loopback
  sidecar.

- `src/gamma/stream/brain.py`
  Stream event decision engine, safety/policy handling, Presence suppression,
  speech queueing, self-goals, fallback audio, and output emission.

- `src/gamma/stream/output.py`
  Stream output dispatcher. Persists JSONL records and publishes generic
  performer events.

- `src/gamma/performer/models.py`
  Runtime-agnostic performer event models and stream-output mapping.

- `src/gamma/performer/bus.py`
  Performer bus with recent history, target controls, replay support, and
  websocket subscribers.

- `src/gamma/performer/vtube_studio.py`
  VTube Studio config, websocket client, adapter, and runner.

- `src/gamma/performer/static/performer.html`
  Stream PC / OBS browser-source page.

- `src/gamma/integrations/twitch/`
  Twitch IRC, EventSub, normalization, sanitization, replay, and viewer trust.

- `src/gamma/integrations/discord/`
  Discord text/voice normalization, dependency-light runtime, and optional
  text ingestion worker.

## Current Operational Facts

- Shana API runs on port `8000`.
- Dashboard runs on port `8001`.
- Keep Shana and dashboard route ownership separate.
- For networking/proxy/service-port work, read
  `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` first and do not edit it.
- Public dashboard URL is separate from bind host/port.
- Public `/dashboard/*`, dashboard `/api/*`, dashboard `/static/*`, `/login`,
  `/logout`, `/monitor*`, and `/overlay/*` browser routes belong to the
  dashboard process.
- Shana-owned routes include `/health`, `/v1/*`, `/performer*`, and `/stream/*`.
- Shana redirects valid dashboard subpage requests to the configured dashboard
  public URL to avoid JSON 404s on misrouted page requests.
- Dashboard pages are `/dashboard`, `/dashboard/live`, `/dashboard/monitor`,
  `/dashboard/status`, `/dashboard/presence`, `/dashboard/stream`,
  `/dashboard/memory`, and `/dashboard/settings`.
- `/dashboard/twitch` redirects to `/dashboard/stream`; `/monitor` redirects to
  `/dashboard/monitor`.
- Browser live voice works through dashboard `WebSocket /api/voice/live`;
  browser capture still uses deprecated `ScriptProcessorNode`.
- Presence modes are `sleep`, `wake`, `go_live`, and `break`. Stale persisted
  `go_live` is downgraded after Shana restart until confirmed.
- `Stop Output` stops active speech/output and clears `dashboard_monitor`,
  `stream_public`, and `discord_call` without stopping Shana, dashboard, or
  ingestion workers.
- Performer clients use `WebSocket /v1/performer/events`,
  `GET /v1/performer/events/recent`, `GET /v1/performer/status`, and
  `GET /v1/audio/artifacts/{filename}`.
- Performer clients should use `audio_url`/artifact routes, not Shana PC local
  file paths.
- VTube Studio adapter support exists but is disabled by default and requires
  ignored machine-local endpoint/token/hotkey config for real use.
- Discord text ingestion exists; Discord replies and voice transport remain
  deferred.
- Resource shadow placement is advisory unless active routing is explicitly
  enabled. Startup admission only affects `auto` sidecar device settings and is
  disabled by default.

## Provider Notes

- LLM providers: `mock`, `openai`, `local`, `ollama`, or router-managed
  combinations.
- STT providers: `stub`, `openai`, `local`, `faster-whisper`.
- TTS providers: `stub`, `openai`, `piper`, `qwen-tts`.
- Local LLM means Ollama-compatible.
- Local STT means faster-whisper.
- Qwen TTS is a local HTTP sidecar, commonly on port `9882`.
- Audio understanding sidecar is loopback-only, commonly on port `9883`.
- RVC layers on generated WAV output; it is not a standalone TTS provider.

## Do Not Break

- API/dashboard route ownership.
- Public-vs-bind URL separation.
- Dashboard HTTPS/proxy compatibility.
- Linux and Windows runnability.
- Stream safety, dry-run, queueing, rate limits, and operator review.
- Presence gating for public stream output.
- Privacy guard and speech filtering.
- Network-safe performer audio URLs.
- The protected deployment spec.

## Validation

Use `.venv/bin/python`, not system Python.

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

For changed dashboard JS:

```bash
node --check src/gamma/dashboard/static/<changed-file>.js
```

Before finishing, run:

```bash
git diff --check
```
