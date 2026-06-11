# Current Implementations

Last updated: 2026-05-24

This is a baseline inventory of features that are already implemented in Gamma. It is intended to support future TODO planning, so it focuses on concrete working surfaces in the codebase rather than aspirational roadmap items.

Runtime assumption: Gamma is primarily run on Linux. Windows compatibility exists mainly for local development, smoke testing, and validation of cross-platform behavior.

## Core Application

- FastAPI app entry point in `src/gamma/main.py`.
- API route layer in `src/gamma/api/routes.py`.
- Root and dashboard HTML routes.
- Conversation, memory, system status, vision, stream, and voice endpoints.
- Optional API bearer-token auth for the Shana API.
- Layered runtime configuration from `config/app.example.toml`, `config/app.toml`, `config/app.local.toml`, `.env`, and process environment variables.
- Separate bind-host and public-host configuration for local-only or LAN use.
- Runtime data directories for audio, images, timings, stream logs, and live voice job state.
- Stream/Twitch runtime settings and operator controls are represented in layered config.

## Conversation Pipeline

- Main conversation response flow through `POST /v1/conversation/respond`.
- Persona prompt construction from editable persona files and structured config.
- Session-aware memory context injection.
- Speaker identity resolution from request context.
- Normal, fast, brief, and micro response modes for lower-latency voice use.
- LLM draft generation through the configured adapter.
- Optional metadata extraction for emotion, motions, tool calls, memory candidates, and internal summaries.
- Reply cleanup for spoken output, including removal of markdown/code formatting.
- Speech safety filtering before spoken output is returned or synthesized.
- Timing capture for draft, tools, metadata, memory persistence, TTS, and total request latency.
- JSONL timing logs under `data/runtime/conversation.timings.jsonl`.
- Optional TTS synthesis on conversation responses.

## Persona And Assistant State

- Current persona target is Shana.
- Persona content is loaded from `gamma/persona/` and `config/persona.yaml`.
- Core persona and boundary files are part of prompt assembly.
- Assistant emotional state tracking is implemented.
- Emotion memory tracks recent emotional patterns and can be shown in the dashboard.
- Hidden style/emotion tags can be stripped from generated text while preserving emotion metadata.

## Memory

- SQLite-backed memory service using SQLModel.
- Runtime memory database stored under `data/memory/gamma.db` with WAL mode, busy timeout, foreign-key enforcement, and composite lookup indexes.
- Profile facts and episodic memories are stored separately.
- Selective memory candidate generation from conversation turns.
- Subject metadata for primary user, other people, relationship labels, and known people.
- Deduplication/canonicalization for common profile facts and preferences.
- Configurable memory enablement, write mode, top-k retrieval, and memory personality.
- Memory stats endpoint at `GET /v1/memory/stats`.
- Dashboard memory stats, known people, recent memory items, and memory clearing.
- Dashboard editing and deletion for individual profile facts and episodic memories.
- First-class known-person records with trust, notes, relationship, and multiple linked platform account IDs.
- Speaker resolution from linked Twitch, Discord, game, or other platform identities before config fallback.
- Clear all memory, clear recent memory, and clear selected memory support.
- Core memory append tool backed by `data/core_memories.md`.

## Built-In Tools

- `memory_stats` returns memory counts and configuration.
- `known_people` returns stored known-person records.
- `provider_status` returns LLM, STT, and TTS provider health/status.
- `recent_artifacts` returns recently generated runtime artifacts.
- `search_memory` searches profile and episodic memory.
- `save_memory` persists scoped profile or episodic memory.
- `save_core_memory` stores owner-only permanent facts.
- Conversation service can infer direct tool calls from user text and render direct replies for tool results.

## LLM Backends And Routing

- Mock LLM adapter for safe development.
- OpenAI adapter for hosted model calls.
- Local/Ollama-compatible adapter.
- LLM factory chooses the active provider from configuration.
- Router adapter supports routing between local/default/hosted paths.
- Router profiles, hosted escalation, fallback controls, backoff state, and capability reporting are implemented.
- Balanced routing is enabled: short/helper work prefers the configured small local worker while default and heavy persona work remains on the primary model.
- Route traces are captured and surfaced in dashboard status.
- Ollama model capability probing exists for local model metadata and vision support checks.
- Image-capable LLM calls are represented through `LLMImageInput`.

## Vision

- Structured vision analysis endpoint at `POST /v1/vision/analyze`.
- Image-aware conversation endpoint at `POST /v1/conversation/respond-with-image`.
- Image upload validation and size limits.
- Local image input storage under the configured image input directory.
- Vision analysis can be attached to the conversation response.
- Dashboard routes support standalone image analysis and image-aware replies.
- Local vision can be enabled for compatible Ollama multimodal models.

## STT

- STT service with pluggable backends.
- Stub STT backend for local safe testing.
- Faster-Whisper/local transcription backend.
- OpenAI transcription backend.
- STT model, device, device index, and compute type are configurable.
- CLI smoke test entry point: `python -m gamma.run_stt_test`.
- API transcription endpoint at `POST /v1/voice/transcribe`.
- Dashboard STT smoke test action.

## TTS

- TTS service with pluggable backends.
- Piper local offline TTS backend.
- OpenAI hosted TTS backend.
- Qwen TTS HTTP-backed local TTS backend.
- Optional RVC post-processing layer on generated WAV files.
- Voice profile loading from layered voice config files.
- Voice profile selection and saving from the dashboard.
- TTS provider/profile selection persisted to `config/app.local.toml`.
- TTS smoke test entry point: `python -m gamma.run_tts_test`.
- Dashboard text-to-speech synthesis endpoint and generated audio serving/deletion.
- Qwen sidecar start/stop scripts and dashboard controls.
- Qwen emotion/instruction handling through expressive text helpers.

## Voice Roundtrip And CLI Voice

- Voice roundtrip endpoint at `POST /v1/voice/roundtrip`.
- Voice roundtrip service combines STT, conversation response, and optional TTS.
- CLI smoke entry point: `python -m gamma.run_voice_roundtrip`.
- Microphone voice mode CLI in `gamma.run_voice_mode`.
- Turn-based and always-listening voice mode policies.
- Windows audio capture/playback through `sounddevice` and `winsound`.
- Linux audio capture/playback support through `arecord`/`aplay` with `sounddevice` fallback.
- Recording device listing support.
- Stop-command detection in voice mode.

## Live Browser Voice

- Dashboard browser voice has turn-based upload mode.
- Dashboard browser voice has live half-duplex WebSocket mode at `/api/voice/live`.
- Live WebSocket protocol supports ready, ping/pong, start turn, end turn, cancel, interrupt, and interrupt probe messages.
- Partial transcript snapshots are emitted while recording when enough audio has accumulated.
- Live turns run through a subprocess worker to isolate/cancel long-running STT/LLM/TTS work.
- Live job manager tracks queued, running, speaking, completed, cancelled, and failed states.
- Live jobs expose worker PID, transcript, reply text, chunks, audio, timings, cancel reason, and cancel latency.
- Hard cancel/barge-in is implemented for dashboard live browser voice.
- Completed/cancelled/failed live job history is persisted as JSONL.
- Live voice history endpoint exists for API and dashboard use.
- Reply chunking and interruptibility metadata exist for chunked live responses.

## Proactive Idle And Stream Control

- Live idle policy evaluates conversation silence, cooldowns, open turns, remote jobs, completed turns, interruption state, and per-topic attempt limits.
- Proactive idle settings are configurable.
- Conversation-lull events can be emitted as dry-run stream events.
- Stream event endpoint at `POST /v1/stream/events`.
- Stream stop endpoint can cancel active live turns and clear stream-facing speech/subtitle state.
- Stream brain classifies events such as chat messages, owner commands, mic transcripts, donations, redeems, moderator actions, game state, system events, and conversation lulls.
- Stream brain decides whether to reply, acknowledge, defer, ignore, escalate moderation, play fallback audio, queue speech, or propose a self-goal.
- Stream brain can apply per-event Twitch runtime controls for dry-run, voice/subtitle enablement, ambient chat, mention replies, spam quips, self-goal proposals, LLM safety review, and rate limits.
- Stream events can call the conversation service with actor/speaker context.
- Stream action planner converts assistant responses into action plans.
- Stream output events include subtitle lines, emotion changes, and avatar motion events.
- JSONL stream output adapter persists stream output events.
- Stream traces are stored and exposed through recent trace endpoints.
- Recent stream output endpoint is implemented.
- Pending stream speech queue endpoint is implemented.
- Temporary stream memory listing and clearing endpoints are implemented.
- Stream self-goal listing, approve, reject, and clear endpoints are implemented.
- Stream replay/evaluation service can inspect recent stream turns and report findings.
- Filtered/fallback audio for stream safety responses is available as a tracked runtime asset.

## Twitch Integration

- Twitch IRC ingestion worker reads chat over IRC, normalizes messages, applies runtime controls, and posts events to the Gamma stream API.
- Twitch EventSub websocket worker handles stream events such as follows, raids, redeems, bits, subscriptions, and moderation-style events.
- Twitch replay utility can post JSONL replay events through the stream API for repeatable tests.
- Twitch dry-run replay is exposed through the dashboard.
- Twitch chat sanitizer classifies spam, bots, blocked content, and trust-sensitive messages before stream handling.
- Viewer trust store tracks Twitch viewer trust level, pronunciation alias, and notes.
- Dashboard controls can start/stop Twitch IRC and EventSub workers.
- Dashboard controls can view/save Twitch runtime settings and viewer trust records.
- Stream readiness/status payloads combine Twitch configuration, worker state, EventSub state, and stream safety state.

## Dashboard

- Separate dashboard FastAPI app in `src/gamma/dashboard/main.py`.
- Static browser UI assets in `src/gamma/dashboard/static/`.
- Optional dashboard login/session auth.
- Dashboard pages now use a compact top navbar rather than the older stacked title/tab controls.
- Dashboard route structure:
  - `/dashboard` overview with live mini cards and links to major areas.
  - `/dashboard/live` focused browser live voice testing.
  - `/dashboard/monitor` minimalist output monitor.
  - `/dashboard/status` detailed service, process, provider health, metrics, and log view.
  - `/dashboard/stream` combined Stream and Twitch operations.
  - `/dashboard/memory` memory stats, latest memories, known people, and safer cleanup controls.
  - `/dashboard/settings` central settings hub.
- `/dashboard/twitch` redirects to `/dashboard/stream`; `/monitor` redirects to `/dashboard/monitor`.
- The reverse proxy should send public `/dashboard/*`, dashboard `/api/*`, and dashboard `/static/*` browser requests to the dashboard process.
- The Shana API app redirects `/dashboard` and valid dashboard subpage requests to the configured dashboard public URL so misrouted browser links do not fall through to JSON 404 pages.
- Rendered dashboard navbar and overview links use `settings.dashboard_base_url` from `SHANA_DASHBOARD_PUBLIC_*` rather than assuming root-relative `/dashboard/*` links are safe on every public host.
- Navbar includes dashboard page links, visually separate Performer/Subtitles output links, compact status chips, a status dropdown, mobile menu behavior, and a permanent `Stop Output` button.
- `Stop Output` stops current speech output and clears dashboard, stream, and Discord performer targets without stopping Shana, dashboard, Twitch, EventSub, or ingestion workers.
- `/dashboard/monitor` is a dedicated monitor page with one-click audio enable, monitor mute, clear output, connection/current event state, subtitle text, expression metadata, actor/input context, and dashboard/compact/focus themes saved in local storage.
- Latest dashboard/output-bus validation for this state: `node --check src/gamma/dashboard/static/dashboard.js`, focused dashboard/API pytest (41 passed, 50 subtests), stream output/brain pytest, and full pytest suite (`218 passed`).
- Dashboard status endpoint includes app, providers, Shana process, machine metrics, memory, assistant state, timings, and LLM routing.
- Runtime status endpoint checks Shana process and API health.
- Start, stop, and restart Shana controls.
- Stop dashboard and stop all services controls.
- Machine CPU, memory, disk, and optional NVIDIA GPU metrics.
- Provider action status tracking.
- TTS provider/profile dropdown and profile editor.
- Test STT, test TTS, synthesize TTS, test LLM, and test voice roundtrip actions.
- Memory management controls.
- Recent conversation timing summary.
- Recent LLM route summary.
- Recent live voice, stream trace, stream eval, and stream output panels.
- Stream safety, pending speech queue, temporary memory, and self-goal panels.
- Stream stop control for stopping speech and clearing stream subtitles.
- Twitch worker, EventSub, runtime settings, viewer trust, replay, and dry-run replay controls.
- Dashboard vision analysis and vision response routes.
- WebSocket live voice route.

## Supervisor, Tray, And Launchers

- Process manager for managed services.
- Shared Python launchers for opening Gamma, starting Shana, starting the dashboard, starting the tray, and stopping services.
- Managed Twitch IRC worker and Twitch EventSub worker services.
- Stream and voice stack starter scripts for launching practical Shana runtime bundles.
- Windows convenience wrappers.
- Linux convenience wrappers.
- Linux desktop entry templates.
- Systemd service templates for Shana and dashboard.
- Tray app support for starting/stopping/opening services where a desktop tray is available.
- Runtime Python resolution for repo-local virtualenvs and platform fallbacks.
- Managed service stdout/stderr logs.

## System Status And Runtime Health

- System status service reports app details, provider configuration, provider health, and recent artifacts.
- LLM, STT, and TTS provider health checks are surfaced through API, tools, and dashboard.
- Torch/CUDA device helpers support local GPU-aware STT setups.
- CUDA library path helper for subprocess launches.
- Recent generated artifacts are discoverable.
- Runtime logs include conversation timings, LLM routes, stream traces, stream outputs, and live voice job lifecycle/history.

## Safety

- Privacy guard refuses doxxing-style requests for private identifying information before the LLM is called.
- Privacy guard output filtering replaces accidental IP, email, phone, street address, or coordinate leaks with a refusal.
- Privacy guard includes streamer-facing checks for unsafe private identity, location, contact, and credential leakage patterns.
- Speech safety policy combines hard blocklist, heuristic checks, optional LLM review, and rewrite behavior.
- Speech filtering is applied before spoken text is returned to clients or synthesized.
- Configurable speech filter level and layer enablement.
- Stream safety review timeout and timeout action are configurable.
- Stream/Twitch safety behavior can skip, fall back, or use filtered audio depending on decision and runtime controls.
- Public synthesized speech runs fast checks before output, then runs TTS and the small-model reviewer concurrently; a late block clears active output and plays the cached filtered WAV/subtitle.
- An operator-editable banned phrase file augments contextual hard-block and heuristic rules.
- Matched rules, action, blocked status, and layer metadata can be attached to TTS metadata.
- Rewrite guard exists for safe text rewrite actions.

## Identity And Speaker Context

- Speaker profile model captures source, platform id, display name, roles, owner status, tool permission, and memory-write permission.
- Identity resolver can parse configured game usernames.
- Conversation and stream flows pass speaker context through to memory/tool permissions.
- Owner-only tool visibility is enforced for core memory.

## Avatar And Image Assets

- Avatar event model exists for downstream avatar/event consumers.
- Stream output adapter maps emotion and motion events into avatar event payloads.
- Tracked image assets exist under `images/me/` and `images/shana/`.
- Shana image variants cover jacket/no-jacket, mouth open/closed, and eyes open/closed states.
- User image variants cover mouth and eye open/closed states plus a `.veado` asset.

## TTS Dataset Tooling

- Standalone GammaTTSDataPrep GUI entry point at `src/gamma/run_tts_dataset_gui.py`.
- Dataset staging/preparation CLI entry points exist.
- GUI build scripts for Windows and Linux/macOS.
- PyInstaller spec for packaging.
- Pipeline/review/transcribe workflow documented in the README.
- Source media staging, faster-whisper segmentation, candidate clip extraction, review, labeling, duplicate finding, trimming, and subset export are implemented at the tooling level.

## Packaging And Platform Support

- Python package metadata in `pyproject.toml`.
- Runtime dependencies include FastAPI, Uvicorn, SQLModel, OpenAI SDK, PyYAML, dotenv, faster-whisper, sounddevice, psutil, Pillow, pystray, multipart, and websockets.
- Optional dev dependency for pytest.
- Linux is the primary operating environment for normal app/runtime use.
- Windows support is maintained mainly for local development, smoke tests, and compatibility testing.
- Windows and Linux scripts exist for common service and TTS sidecar operations.
- Host binding and LAN access configuration are implemented.
- Local/machine-specific config override files are supported and ignored by git.

## Automated Coverage Currently Present

- API route tests.
- Conversation pipeline tests.
- Dashboard route tests.
- Expression and routing tests.
- Live idle policy tests.
- Live voice runtime tests.
- LLM router tests.
- Memory service tests.
- Stream brain tests.
- Stream output tests.
- Stream replay tests.
- Twitch integration tests.
- Torch device tests.
- Voice affect tests.

## Known Limits Still Reflected In Current Docs

- Gamma is still a scaffold/prototype, not a polished production assistant.
- Browser live voice partial transcripts are snapshot updates, not true token-streaming ASR.
- Dashboard live voice hard-cancel is implemented for the browser path; CLI voice still uses its separate non-worker path.
- Dashboard browser capture currently uses `ScriptProcessorNode`, which should later move to `AudioWorkletNode`.
- Live voice remains phrase-based rather than true word-by-word streaming transcription.
- Local audio behavior still depends on OS-level audio tools and devices.
- Ollama is health-checked but not lifecycle-managed by Gamma.
