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
- The production Linux host uses lingering systemd user units for boot-time
  Shana and dashboard ownership. In `systemd-user` lifecycle mode, dashboard
  Shana controls delegate to `gamma-shana.service`, dependency hooks restore
  configured local voice sidecars, and runtime stop controls preserve the
  always-on dashboard.
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

### Staged foundation status

The isolated `gamma-foundation-rework` branch contains accepted staged source
contracts verified with synthetic checks. None of these foundation slices is
activated in production; production services and storage have not been migrated
or restarted for them.

October 11 update: the foundation entries below reconcile accepted staged
repairs only; the document-wide verification date and live capability claims
are unchanged.

- Journal ownership, scoped lifecycle recovery and atomic terminal persistence;
  summaries and checkpoints use exact session/privacy scope. See [memory](memory.md).
- Recipient-based prompt, background, tool, candidate and diagnostic disclosures
  preserve authored Shana content and separate disclosure from execution authority.
  See [persona](persona.md), [memory](memory.md) and [output](shana_output_bus.md).
- Source-aware private memory records retain reporter, subject, ingress/privacy
  and correlation separately; private retrieval qualifies claims as unverified.
  Importance ranks salience, not truth. See [attribution](memory.md#staged-source-aware-private-memory-attribution).
- Prompt/status emotion readers use EmotionMemoryService; compatibility-file
  failures cannot suppress canonical writes. Cross-turn emotion ordering remains
  separate. See [writer boundary](persona.md#staged-canonical-emotion-writer-boundary).
- Per-file locks cover state read/mutate/save and publish complete JSON atomically.
  They do not create a transaction across stores. See [persona](persona.md).
- Scoped receive cursors preserve newer working context through retention and
  row-ID reuse; older completions remain history. Unknown pre-change order stays
  conservative. See [checkpoint ordering](memory.md#staged-scoped-working-checkpoint-receive-ordering).
- Stored Presence bookkeeping preserves newer owner controls/work and publishes
  complete state atomically. See [Presence](shana_presence.md#staged-latest-owner-state-and-atomic-persistence).
- Interrupted coding startup can reconcile a completed job-local application
  journal against installed source hashes, retaining interrupted status and no
  replay. See [coding evidence](coding_improvement.md#staged-interrupted-application-evidence-reconciliation).
- Private coding-status pulls expose bounded stored recovery/application outcomes
  and explicitly unconfirmed follow-up, without raw job/evidence content or a
  notification bridge. See [private progress](coding_improvement.md#staged-bounded-private-coding-progress-pull).
- Journaled responses recheck their exact owned pending exchange before each new
  tool admission; explicit cancellation blocks later calls while admitted effects
  may finish. See [tool checkpoint](memory.md#staged-cooperative-owned-tool-admission-checkpoint).
- Summary coverage marks the greatest selected journal ID; a late quiet completion
  cannot regress that selected coverage. See [memory](memory.md).

- Escaping control-flow interruptions attempt to close the owned pending journal
  as interrupted and re-raise unchanged; persistence failure can leave it pending.
  See [memory](memory.md).
- Late safety clears and filtered replacements retain the original output target.
  See [output](shana_output_bus.md).
- Discord reply accounting counts successful callbacks only when their playback
  generation is current at callback arrival. See [integrations](integrations.md).
- Enabled text-only LLM review runs synchronously, including when synthesis was
  requested, without TTS. Actual speech prepares TTS concurrently but waits for
  enabled review to allow before release. See [output](shana_output_bus.md).
- Shared-backend OS admission lets an active coding response finish, then admits
  registered conversation/reviewer work before new coding generation. Waiting
  coding retains owner controls and resource guards. See [LLM routing](llm_router.md).
- Autonomous coding rechecks cooperative pause/stop and the time budget before
  dispatching new generation after availability. Pre-admission resource waits use
  100 ms sleep slices for those checks while retaining two-second availability
  probes. Paused waits retain their separate one-second loop; admitted network
  requests and generation remain nonpreemptive. See
  [`RepositoryAgent.ask`](../src/gamma/coding/autonomous.py).

Accepted composed synthetic checks exercise concurrent owner status/stop and
unrelated public output while coding waits, enforcing authority, public/private
disclosure and the stop checkpoint. Provider, TTS and classifier responses are
synthetic. These checks do not establish live model quality, resource contention,
audible playback or an automatic private completion bridge.

Remaining boundaries include trusted private ingress, foreign-subject tool
authority, name-based identity, assumed local voice and semantic verification of
claims. Attribution and canonical writer isolation are accepted repairs, not full
identity, truth or emotion-learning guarantees.

Stored Presence and scoped checkpoint freshness do not establish general state,
effect or output cancellation, cross-turn emotion ordering, safe restart task
continuation or automatic private completion delivery. Existing owner status/stop
checks and process ownership are not human authentication.

Live model/provider quality, resource contention, STT lifecycle and audible
receiving-target behavior still need representative proof. Pending 4D is
unaccepted; its rejected actual Monitor/UI verification must not be retried.
Production activation and storage migration require separate human authorization
and recovery planning. Machine maintenance did not activate this staged source.

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
- Staged backend Presence writes use the existing per-file lock and atomic publication helper. Fresh-state mutations and captured-context checks preserve newer owner controls/session/work during late Wake, scheduler, conversation and polling bookkeeping. Backend-only proof does not establish Monitor/output-time cancellation; see `shana_presence.md`.
- Public output and stream autonomy are gated independently from process lifecycle.
- Shana-owned dynamic Wake events with explicit unknown/owner/known-person audience selection.
- Wake targets Monitor, skips TTS without an audio-ready listener, and keeps bounded opening history.

## Streamer Foundation

- Normalized mic, chat, owner, system, moderation, donation, redeem, game, and lull events.
- StreamBrain reply/acknowledge/ignore/defer/moderation/self-goal decisions.
- Per-event output, safety, speech-budget, and Twitch controls.
- Temporary stream context, pending speech, self-goal approval, traces, replay, and evaluation.
- Twitch IRC and EventSub workers, sanitization, viewer trust, dry-run replay, and durable worker state.
- Discord allowlisted text ingestion, configuration-gated reviewed text replies
  and owner-controlled voice sessions. Portable defaults are disabled; current
  live settings and audible playback are not verified here. See
  [integrations](integrations.md).
- Public-output safety runs fast filters and optional LLM review before release,
  with parallel TTS preparation and filtered fallback output. Enabled review with
  no model cannot approve the original. See [output](shana_output_bus.md).

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
- Systemd-aware Shana lifecycle controls with no dashboard self-stop control
  on the operator page.
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

- Initial coding-practice foundation in `src/gamma/coding`: dependency-aware
  fictional exercises, local-only bounded proposals, protected regression and
  feature checks in a Linux sandbox, honest blocked/checker states, bounded
  capability summaries, a withheld generated-case verification gate, retained
  review-only candidate solutions, editable observed lessons, and optional idle-only
  practice. The Improvement dashboard exposes authenticated controls and
  evidence. A separate owner-directed executor now discovers ordinary Python
  source, plans bounded dependent tasks, repairs executed failures, independently
  reviews and applies verified source with rollback. A separate isolated QLoRA
  trial trains candidate adapters and evaluates held-out tasks; production model
  activation remains unavailable. See
  `coding_improvement.md` for implemented boundaries and outstanding work.

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

- Source-aware automatic private memory attribution separates reporter from subject, retains ingress/privacy/correlation metadata, qualifies all stored claims as unverified in authorized private retrieval, and separates profile importance from truth confidence. Source-separated deduplication preserves other reporters. Legacy/manual/unbound-tool origin remains explicit; name-based identity and foreign-subject tool authority remain limited. Additive schema tested only on disposable/copied SQLite; see `memory.md`.

## Current Architectural Debt

- `DashboardService` still combines several dashboard-owned concerns: supervisor controls, machine status, local provider configuration, and integration operations.
- Existing operator pages retain compatibility-oriented classic scripts and global handlers; new focused clients use native modules.
- Browser live capture still uses deprecated `ScriptProcessorNode` rather than `AudioWorkletNode`.
- Conversation reliability and latency have an aggregate comparison foundation,
  but representative fixtures and an owner-facing soak/evaluation gate remain incomplete.

# Registered repository coding and Shana controls

The Improvement dashboard can run local-model repository coding on the registered
Discord status helper. Gamma produces a plan and edits, repairs failed protected
checks, requests another local model's diagnosis after five failures, verifies
withheld cases and applies the helper with baseline conflict protection and a
rollback copy. This narrow scope does not establish general project autonomy or
change model weights. Evidence and candidate examples remain in ignored runtime
storage; successful lessons are editable in the Improvement dashboard.

The Presence dashboard controls Shana's Discord bot lifecycle and voice join,
leave, pause and resume. Commands are serialized with live bot receipts. Status
executes the installed Gamma-authored pure helper and includes its source hash.
Existing dashboard authentication and proxy access policies apply.

## Autonomous coding and candidate learning

The Improvement page accepts owner goals and exposes pause/resume/stop, executed
test receipts, installed-source application evidence and editable learning.
Successful work retains frozen feature tests in the protected regression suite.
Lesson revisions preserve provenance and owner edits. The runtime uses local
Qwen3.8:27b for coding and GPT-OSS:20b for independent review. Protected controller,
identity, safety and authentication code cannot be changed by the learner.

The local candidate pilot uses the pinned Qwen3-4B-Instruct-2507 checkpoint in a
separate training environment. A real 12-step NF4 LoRA trial changed adapter
parameters, preserved the base model and scored 1/8 held-out tasks versus 0/8 for
its base. It was rejected by the 80 percent pilot floor. This is hardware and
pipeline proof, not a successful quality upgrade. No production adapter was
activated; incumbent comparison and serving validation remain required.
