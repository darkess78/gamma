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

4. TTS dataset prep tooling
   The Tkinter dataset-prep tool still exists, but it is not the primary focus of the current repo state.

Current practical focus: the dashboard/browser voice workflow and Linux/Windows compatibility for the live assistant path.

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

Recent important changes:

- Qwen3-TTS startup now falls back cleanly when requested CUDA devices are unavailable
- Local faster-whisper STT also falls back more safely across device availability differences
- Dashboard public URL handling now separates bind port from public/proxied URL
- Dashboard memory panel now includes "Latest Memories"
- Memory cleanup is no longer a blind full wipe in normal UI flow; there is now a recent-memory selection/delete flow
- Browser voice controls now include mute mic, mute assistant playback, and pop-out subtitles

## Architecture Map

### Backend

- [gamma/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/main.py)
  FastAPI entrypoint for the assistant backend.

- [gamma/api/routes.py](/home/neety/.openclaw/workspace/gamma-main/gamma/api/routes.py)
  Main API routes for conversation, vision, and voice APIs.

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

### Dashboard

- [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
  Dashboard FastAPI app and dashboard API routes.

- [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)
  Dashboard-facing orchestration: status payloads, provider test actions, memory operations, audio serving.

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

- [gamma/dashboard/static/dashboard.css](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.css)
  Dashboard styling.

### Process Supervision

- [gamma/supervisor/manager.py](/home/neety/.openclaw/workspace/gamma-main/gamma/supervisor/manager.py)
  Starts/stops `shana` and `dashboard`, plus managed local TTS sidecars.

- [gamma/supervisor/cli.py](/home/neety/.openclaw/workspace/gamma-main/gamma/supervisor/cli.py)
  CLI wrapper around the process manager.

### Config

- [gamma/config.py](/home/neety/.openclaw/workspace/gamma-main/gamma/config.py)
  Central settings loader from `.env` and `config/*.toml`.

- `.env`
  Local active environment config in this workspace.

- `config/app.example.toml`, `config/app.toml`, `config/app.local.toml`
  App config layering.

- `config/voices.example.toml`, `config/voices.toml`, `config/voices.local.toml`
  Voice profile layering.

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

## Test / Validation Expectations

Current useful validation commands:

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m gamma.supervisor.cli start dashboard
./.venv/bin/python -m gamma.supervisor.cli start shana
./.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

Useful provider smoke tests:

```bash
./.venv/bin/python -m gamma.run_llm_test "Dashboard LLM smoke test."
./.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
./.venv/bin/python -m gamma.run_tts_test "Dashboard TTS smoke test."
./.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac
```

## Handoff Guidance

When taking over work in this repo:

- assume the dashboard/browser voice path matters more than old speculative roadmap text
- prefer small, concrete fixes over broad architecture churn
- preserve both Linux and Windows runnability
- if behavior is operator-facing, verify both backend route support and dashboard JS/UI behavior
- if browser behavior seems inconsistent, suspect stale static assets or proxy/browser caching before overcomplicating the logic

For a condensed version, see [specs/llm-handoff-prompt-lite.md](/home/neety/.openclaw/workspace/gamma-main/specs/llm-handoff-prompt-lite.md).
