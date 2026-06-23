# Current Implementations

Status: Current
Last verified: 2026-06-22

This is a concise inventory of behavior present in the repository. Domain
specs own detailed contracts; target documents own future work.

## Runtime

- Shana FastAPI application on port 8000.
- Dashboard FastAPI application on port 8001.
- Layered TOML, `.env`, and process-environment configuration.
- Separate internal bind and browser-facing public URLs.
- Optional bearer authentication for Shana and session authentication for the dashboard.
- Supervisor-managed Shana, dashboard, Qwen TTS, Twitch, Discord, and audio-understanding processes.
- Structured rotating logs and request correlation.

## Persistent Shana Core

- Text conversation through `POST /v1/conversation/respond`.
- Stable persona prompt assembly and assistant emotion state.
- Speaker identity resolution across local, Twitch, Discord, game, and linked accounts.
- SQLite/SQLModel profile facts, episodic memory, known people, selective writes, and memory tools.
- Durable per-session turn journals, rolling summaries, working-state checkpoints, and bounded recent-turn context.
- Durable last-output text restoration without replaying stale speech after restart.
- Privacy guard and layered speech-safety filtering before public or synthesized output.
- Mock, OpenAI, and Ollama-compatible LLM adapters.
- Deterministic LLM routing, fallback/backoff state, and route traces.
- Explicit primary-quality `presence_wake` routing with candidate capability checks and prompt rebuilding.
- Model-aware full-prompt budgets, priority compaction, and overflow-specific retry without provider backoff.
- Image analysis and image-aware conversation.
- Faster-Whisper, OpenAI, and stub STT paths.
- Piper, OpenAI, and Qwen TTS paths with named voice profiles and optional RVC post-processing.
- File roundtrip, CLI microphone modes, and browser live voice.

## Live Voice

- Dashboard WebSocket at `/api/voice/live` delegates inference to Shana `/v1/voice/*` jobs.
- Browser-side VAD, partial transcript snapshots, chunked playback, and transcript-confirmed barge-in.
- Subprocess-isolated live turns with queued/running/speaking/completed/cancelled/failed states.
- Hard cancellation, cancel latency, persisted job history, and reply interruptibility metadata.
- Shana-owned bounded proactive scheduler exists, is configurable and disabled by default, and does not depend on browser WebSockets.
- Browser capture still uses deprecated `ScriptProcessorNode`; AudioWorklet migration remains future work.

## Audio Understanding

- Shared bounded mono 16 kHz normalization for WAV and FFmpeg-readable input.
- Lightweight affect/prosody analysis.
- Optional Hugging Face speaker-emotion and audio-event providers.
- Optional persistent loopback sidecar with independent device placement.
- Fail-open behavior preserves voice turns when analysis is unavailable.
- Prompt injection of audio context is opt-in and confidence-gated.

## Presence

- Sleep, wake, go-live, and break lifecycle modes.
- Persisted runtime state under `data/runtime/presence/`.
- Stale go-live confirmation is downgraded after a Shana restart.
- Public output and stream autonomy are gated independently from process lifecycle.
- Shana-owned dynamic Wake events with explicit unknown/owner/known-person audience selection.
- Wake targets Monitor, skips TTS without an audio-ready listener, and keeps bounded opening history.

## Streamer Foundation

- Normalized mic, chat, owner, system, moderation, donation, redeem, game, and lull events.
- StreamBrain reply/acknowledge/ignore/defer/moderation/self-goal decisions.
- Per-event output, safety, speech-budget, and Twitch controls.
- Temporary stream context, pending speech, self-goal approval, traces, replay, and evaluation.
- Twitch IRC and EventSub workers, sanitization, viewer trust, dry-run replay, and durable worker state.
- Discord allowlisted text ingestion; Discord reply and voice output remain disabled.
- Public-output safety runs fast filters and optional parallel LLM review with filtered fallback output.

## Performer And Outputs

- Ordered performer event bus with replay windows and target policies.
- `stream_public`, `dashboard_monitor`, and `discord_call` targets.
- Speech, subtitle, emotion, motion, clear, and speaking-state events.
- Network-safe audio artifacts for remote consumers.
- Browser performer, subtitle overlay, and dashboard monitor clients.
- VTube Studio adapter and runner exist but remain optional and require live validation.

## Dashboard

- Persistent `/dashboard/monitor` room for local text, performer replay, speech playback, and live-voice controls.
- Legacy Talk routes redirect to Monitor.
- Overview, Live, Monitor, Presence, Status, Stream, Memory, and Settings pages.
- Dashboard-to-Shana requests use a centralized HTTP client; assistant domain services and state stores remain owned by Shana.
- Lightweight header polling avoids full provider, memory, log, and machine-status work on interaction and operations pages.
- Service lifecycle and provider smoke-test controls.
- TTS profile selection/editor and generated-audio controls.
- Memory and known-person management.
- Twitch/EventSub/Discord worker controls and stream operations.
- Stream trace, output, queue, temporary-memory, self-goal, and rehearsal views.
- Machine/provider/process health, logs, timings, and routing status.
- Vision analysis and image-aware replies.

## Resource Routing

- Shared CPU, RAM, disk, GPU, and GPU-process telemetry.
- Target registry and deterministic shadow placement policy.
- Advisory LLM route metadata and temporary reservations.
- Optional endpoint-aware local LLM routing.
- Optional startup admission for auto-placed Qwen and audio-understanding sidecars.
- Observed sidecar VRAM reconciliation and dashboard reporting.
- Automatic model eviction/loading remains unimplemented.

## Tooling And Platform

- Linux-first runtime with Windows development/smoke-test compatibility.
- Local proxy, Nginx templates, systemd templates, platform launchers, and tray tooling.
- Standalone TTS dataset preparation GUI and CLI pipeline.
- Pytest coverage for API, dashboard, conversation, memory, voice, stream, integrations, safety, routing, resources, and performer behavior.

## Current Architectural Debt

- `DashboardService` still combines several dashboard-owned concerns: supervisor controls, machine status, local provider configuration, and integration operations.
- Existing operator pages retain compatibility-oriented classic scripts and global handlers; new focused clients use native modules.
- Browser live capture still uses deprecated `ScriptProcessorNode` rather than `AudioWorkletNode`.
- Conversation reliability and latency do not yet have a repeatable owner-facing soak/evaluation gate.
