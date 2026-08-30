# Current Implementations

Status: Current
Last verified: 2026-08-30

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
- Disabled-by-default Shana-owned Minecraft coordinator wiring and authenticated
  loopback WebSocket control transport at `/v1/minecraft/control`.
- Independently started Node sidecar with a narrow Mineflayer 4.37.1 adapter for
  explicit offline-development join and leave; Gamma, Dashboard, and the
  supervisor do not start it automatically.
- Bounded Minecraft companion follow, wait, come, look, stop, and emergency-stop
  execution uses direct steering without a pathfinder dependency. Movement is
  limited to clear, flat, loaded, direct Overworld terrain and fails closed on
  unknown terrain, obstacles, jumps, drops, liquids, hazards, portals, or a
  dimension mismatch. No real-server movement smoke test has passed.
- An explicitly opted-in manual smoke harness can exercise the real sidecar and
  Mineflayer adapter against an already-running private loopback Java 1.21.11
  offline server through a temporary fake Gamma controller. It does not use
  active Shana, is excluded from default start and test paths, and has not yet
  produced a successful real-server movement result.
- Minecraft companion owner authorization is an exact case-insensitive match of
  one configured and observed offline username. It is development-only and is
  not online UUID-strength authorization. Dashboard controls, natural-language
  commands, Microsoft authentication, and online owner UUID authorization remain
  unimplemented.

## Persistent Shana Core

- Text conversation through `POST /v1/conversation/respond`.
- Conversation generation now crosses an explicit structured turn boundary:
  action, requested delivery, final communicable text, safe summary, emotion,
  presentation hints, authorized tool requests, memory candidates, bounded state
  updates, and a stable reason code. Raw chain-of-thought is neither requested
  nor retained.
- `AssistantResponse.display_text` owns intentional text communication and
  `AssistantResponse.speech_text` is the sole TTS/spoken-subtitle input.
  `spoken_text` remains as a compatibility field during migration.
- Deterministic delivery policy can resolve a turn to speech, text-only,
  silence, or deferral. Private-marker, planner-payload, contradictory, or
  malformed structured results receive one bounded regeneration; direct turns
  then use a safe visible fallback while ambient/public turns fail closed.
- Stable persona prompt assembly and assistant emotion state.
- Speaker identity resolution across local, Twitch, Discord, game, and linked accounts.
- SQLite/SQLModel profile facts, episodic memory, known people, selective writes, and memory tools.
- Durable per-session turn journals, rolling summaries, working-state checkpoints, and bounded recent-turn context.
- Silent completed exchanges update bounded working/emotional state without
  writing a fabricated assistant utterance; only communicated text and safe
  summaries enter continuity.
- Durable last-output text restoration without replaying stale speech after restart.
- Privacy guard and layered speech-safety filtering before public or synthesized output.
- Mock, OpenAI, and Ollama-compatible LLM adapters.
- Deterministic LLM routing, fallback/backoff state, and route traces.
- Route failures/skips include a stable non-content error class for aggregate triage.
- Non-content conversation timings distinguish prompt-context assembly,
  draft-request assembly, the routed draft LLM call, and the complete draft stage.
- Explicit primary-quality `presence_wake` routing with candidate capability checks and prompt rebuilding.
- Model-aware full-prompt budgets, priority compaction, and overflow-specific retry without provider backoff.
- Image analysis and image-aware conversation.
- Faster-Whisper, OpenAI, and stub STT paths.
- Piper, OpenAI, and Qwen TTS paths with named voice profiles and optional RVC post-processing.
- File roundtrip, CLI microphone modes, and browser live voice.
- TTS, live-voice chunking, stream speech safety/budgets, performer speech
  events, and spoken subtitles consume `speech_text`; Monitor/text clients use
  `display_text`.

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
- Overview, Live, Monitor, Presence, Status, Stream, Memory, Improvement, and Settings pages.
- Presence UI exposes explicit audience selection, Wake history, Monitor listener readiness, working continuity, and bounded scheduler state.
- Dashboard-to-Shana requests use a centralized HTTP client; assistant domain services and state stores remain owned by Shana.
- Lightweight header polling avoids full provider, memory, log, and machine-status work on interaction and operations pages.
- Status, Settings, and Memory use separate bounded status contracts rather than transferring the legacy full diagnostics payload.
- Service lifecycle and provider smoke-test controls.
- TTS profile selection/editor and generated-audio controls.
- Memory and known-person management.
- Twitch/EventSub/Discord worker controls and stream operations.
- Stream trace, output, queue, temporary-memory, self-goal, and rehearsal views.
- Self-improvement visibility plus authenticated owner controls for durable,
  bounded directed or evidence-selected work requests. Owners can choose a
  goal, focus, local models, wall-clock budget, discovery cycles, and isolated
  attempts, then queue, cooperatively pause, resume, or stop the worker. The
  page cannot approve, promote, deploy, or mutate the live checkout.
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
- Local proxy, Nginx templates, systemd templates, platform launchers.
- Standalone TTS dataset preparation GUI and CLI pipeline.
- Versioned improvement contract, bounded aggregate observer, isolated
  baseline-versus-candidate evaluator, fictional conversation fixture catalog,
  state-isolated evaluation requests, sanitized fixture artifacts, local-only-by-
  default multi-model proposal analysis, observer-bound evidence, deterministic
  proposal screening and consensus, pinned source hash/symbol/call/line
  grounding, bounded verified source excerpts plus metric-adjacent same-file
  callee bodies and explicit source-refuted outcomes for local grounding-only
  plans,
  proposal-only experiment manifests, scope validation, disabled-by-default
  detached worktrees, hash- and citation-bound local-model candidate edits,
  atomic isolated application receipts, and fixed safety/full regression
  profiles inside a no-network, read-only-source Linux sandbox. It cannot edit
  the live checkout, promote changes, or operate services. A bounded sanitized
  dashboard reader exposes its ignored series and observation artifacts without
  returning raw prompts, candidate patches, model transcripts, or artifact paths.
  A durable separate worker can translate explicit bounded owner requests into
  observation, local multi-model proposal, deterministic review, pinned source
  grounding, and isolated candidate series. It stops successful work at
  review-ready and provides no live promotion authority.
- Pytest coverage for API, dashboard, conversation, memory, voice, stream, integrations, safety, routing, resources, and performer behavior.

## Current Architectural Debt

- `DashboardService` still combines several dashboard-owned concerns: supervisor controls, machine status, local provider configuration, and integration operations.
- Existing operator pages retain compatibility-oriented classic scripts and global handlers; new focused clients use native modules.
- Browser live capture still uses deprecated `ScriptProcessorNode` rather than `AudioWorkletNode`.
- Conversation reliability and latency have an aggregate comparison foundation,
  but representative fixtures and an owner-facing soak/evaluation gate remain incomplete.
