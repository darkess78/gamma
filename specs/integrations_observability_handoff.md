# Integrations And Observability Implementation Handoff

## Purpose

This document is the detailed restart point for the next Gamma implementation
session. It records the June 15, 2026 audit of:

- audio understanding
- Twitch IRC and EventSub
- Discord
- VTube Studio
- runtime logging and diagnostics

Read this document after `README.md`, `specs/README.md`, and
`specs/current_implementations.md`. For any networking, proxy, public URL,
service port, or deployment work, read
`specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` first and do not modify it.

The worktree already contains uncommitted audio-understanding implementation
work. Do not reset or overwrite those changes.

## Executive Status

| Area | Current state | Readiness |
| --- | --- | --- |
| Audio understanding | Models, sidecar, GPU controls, voice integration, probe, and tests implemented | Needs production-load and real-audio evaluation |
| Twitch IRC | Configured, supervised, connected, and posting normalized events | Operational in dry-run |
| Twitch EventSub | Worker and subscriptions implemented, but current runtime is in a reconnect loop | Unhealthy |
| Discord | Schemas, identity normalization, status stub, and output target exist | No external Discord connection |
| VTube Studio | WebSocket client, authentication flow, hotkey translation, runner, API controls, and tests exist | Disabled and not live-validated |
| Logging | Several useful JSONL stores and process logs exist | Fragmented, weakly correlated, and incomplete on exceptions |

## Verified Runtime Snapshot

The following was observed on June 15, 2026. Treat it as diagnostic evidence,
not permanent configuration:

- Shana API and dashboard processes were running.
- The performer bus had no active subscribers.
- Discord was disabled, unconfigured, and had handled no input or output.
- VTube Studio was disabled, disconnected, unauthenticated, and had no
  expression or motion mappings.
- Twitch IRC was configured and connected to channel `neety`.
- Twitch IRC had processed 55 messages and was running in dry-run with voice
  disabled and subtitles enabled.
- Twitch EventSub had a running process but was disconnected and reconnecting.
- EventSub state showed 1,421 reconnects, zero successful subscriptions, six
  subscription errors, and one historical notification.
- The repeated EventSub WebSocket failure was:
  `4003 (private use) connection unused`.
- Twitch IRC and EventSub stdout/stderr files were empty despite this failure.
- The audio-understanding sidecar was intentionally stopped after its probe.

Do not expose tokens, credentials, or machine-local configuration while
investigating these states.

## Recommended Work Order

1. Implement the minimum structured logging foundation and use it in Twitch
   EventSub.
2. Stabilize one EventSub subscription end to end.
3. Complete combined GPU load validation for audio understanding.
4. Evaluate audio understanding on labeled real microphone recordings.
5. Live-validate one VTube Studio expression.
6. Add a text-only Discord bot worker.
7. Expand VTube Studio mappings and Discord voice only after the simpler
   integrations are stable.

The first two items should be handled together. EventSub is currently failing
in a way that demonstrates the observability gap.

## Work Package 1: Structured Runtime Logging

### Implementation Result: June 15, 2026

The first observability slice is implemented:

- `src/gamma/observability.py` provides structured JSON operational logs,
  correlation context, recursive secret/audio redaction, exception tracebacks,
  and rotating file retention.
- Default structured logs are stored under `data/runtime/logs/`, rotate at
  5 MiB, and retain five backups. These defaults are configurable through
  tracked shared settings.
- Shana and dashboard accept or create `X-Request-ID`, return it on the HTTP
  response, and record normalized route, status, and duration.
- The stream event HTTP boundary copies the request ID into input-event
  metadata so the same ID is retained in the stream trace.
- Supervisor stdout/stderr logs retain five archived files per stream across
  restarts. Dashboard tail paths continue to point at the current files.
- Twitch EventSub is the first full adopter and records worker start/exit,
  token validation, connection, welcome, subscription result, keepalive,
  notification post, reconnect request, revocation, exception, and backoff.
- Twitch IRC is the second adopter and records TCP/TLS connection,
  authentication and join commands/confirmation, ping responses, normalized
  event posts, post failures, disconnects, reconnects, backoff, and exit.
  Operational records retain message/event/request/session identifiers but do
  not retain chat text.
- Focused tests cover JSON shape, correlation fields, redaction, traceback
  output, bounded rotation, request-ID handling, supervisor preservation, fast
  worker exit, EventSub lifecycle events, and Twitch IRC lifecycle/failure
  events.

Runtime verification on June 15, 2026 confirmed TCP and TLS connection to
Twitch IRC, followed by an authentication rejection using the current
machine-local credential. The worker now records a durable
`configuration_error` with traceback and exits at zero reconnects. The
configured OAuth value was absent from the structured log, state file, and
stderr. Dry-run remained enabled and voice remained disabled.

Operational logs intentionally do not replace domain stores. Stream traces and
stream outputs may still contain complete event/response payloads for local
replay and evaluation. Their existing rotation behavior remains separate and
should receive an explicit privacy-retention review in a later work package.

### Current Behavior

Gamma has useful specialized records:

- `data/runtime/conversation.timings.jsonl`
- `data/runtime/llm.routes.jsonl`
- `data/runtime/stream_traces/current.jsonl`
- `data/runtime/stream_outputs/current.jsonl`
- `data/runtime/live_jobs/history.current.jsonl`
- `data/runtime/live_jobs/lifecycle.jsonl`
- per-service stdout and stderr files
- Twitch worker and EventSub `state.json` snapshots
- dashboard browser events in `data/runtime/dashboard.client.log`

However:

- only STT and the Qwen TTS server substantially use Python `logging`
- many broad exception handlers retain only `str(exc)`
- worker state files overwrite prior state instead of preserving transitions
- process stdout/stderr logs are truncated on every supervisor start
- Uvicorn access logs are disabled
- there is no consistent request or correlation identifier across services
- there is no shared redaction or retention policy
- several JSONL stores contain user text or complete stream event payloads

Relevant files:

- `src/gamma/supervisor/manager.py`
- `src/gamma/main.py`
- `src/gamma/dashboard/main.py`
- `src/gamma/conversation/service.py`
- `src/gamma/stream/trace.py`
- `src/gamma/stream/output.py`
- `src/gamma/voice/live_jobs.py`
- `src/gamma/integrations/twitch/worker.py`
- `src/gamma/integrations/twitch/eventsub.py`
- `src/gamma/performer/vtube_studio.py`
- `src/gamma/integrations/discord/runtime.py`

### Initial Scope

Create a small shared logging module under `src/gamma/`, following existing
repository patterns. It should provide:

- one configuration entry point per process
- structured JSON lines with UTC timestamp, level, service, event name, and
  message
- optional identifiers such as request ID, trace ID, turn ID, event ID,
  platform message ID, and session ID
- `logger.exception(...)` support so unexpected failures include stack traces
- recursive redaction for token, authorization, secret, password, cookie, and
  raw audio fields
- bounded file rotation and retention
- human-readable stderr output during direct development runs, if practical

Do not replace domain JSONL stores such as stream traces. The shared logger is
for lifecycle, failure, and operational evidence. Domain stores remain useful
for replay and evaluation.

Add HTTP middleware to Shana and dashboard that:

- accepts or creates an `X-Request-ID`
- returns that ID in the response
- logs method, normalized route/path, status, and duration
- does not log authorization headers, cookies, uploaded bodies, or full
  transcripts
- logs unhandled exceptions with the request ID and traceback

Change supervisor log handling so restarting a service does not destroy the
previous incident evidence. Rotate or rename old stdout/stderr files before a
new start instead of truncating them without preservation.

### First Adopters

Instrument these paths first:

1. Twitch EventSub connection, welcome, subscription result, keepalive,
   notification, reconnect request, revocation, exception, backoff, and exit.
2. Twitch IRC connect, authenticate, join, ping, disconnect, post failure,
   reconnect, and exit.
3. VTube Studio connect, token request, authenticate, API request failure,
   runner start/stop, and event handling failure.
4. Audio sidecar preload, request completion, provider failure, and shutdown.

### Privacy Requirements

- Never log OAuth tokens, Discord bot tokens, bearer tokens, cookies, or VTube
  Studio authentication tokens.
- Do not log uploaded audio bytes.
- Prefer text length, hash, classification, or a short explicitly permitted
  preview over full private transcripts.
- Preserve raw public Twitch text only in the existing intentionally local
  diagnostic surfaces where already required, not in every operational log.
- Document log retention because stream traces currently retain complete event
  and response payloads.

### Acceptance Criteria

- A forced EventSub connection failure creates a structured event with service,
  event name, error class, detail, reconnect count, backoff, and traceback.
- A request can be followed from HTTP request ID into a stream trace or live
  turn when those identifiers exist.
- Restarting Shana or a worker preserves the previous log file.
- Secrets are absent from tests and generated log records.
- Repeated identical reconnect failures are visible without flooding logs at
  uncontrolled volume.
- Dashboard status can continue showing current stdout/stderr tails.

### Tests

Add focused tests for:

- JSON record shape
- context/correlation fields
- secret redaction
- exception traceback recording
- file rotation/retention
- request ID creation and propagation
- supervisor preservation of prior logs
- EventSub lifecycle event emission

Run:

```bash
.venv/bin/python -m pytest tests/test_twitch_integration.py -q
.venv/bin/python -m pytest tests/test_dashboard_routes.py tests/test_api_routes.py -q
.venv/bin/python -m pytest -q
git diff --check
```

## Work Package 2: Stabilize Twitch EventSub

### Implementation And Diagnostic Result: June 15, 2026

The active reconnect loop was diagnosed and stopped:

- Twitch requires a subscription within 10 seconds of the welcome message.
  The previous worker attempted six synchronous Helix requests in the async
  receive loop with 30-second timeouts.
- EventSub now defaults to only `channel.follow`, validates the OAuth token
  before opening the WebSocket, and runs the blocking Helix request in a worker
  thread with an 8-second timeout.
- `channel.follow` validation requires `moderator:read:followers` and verifies
  that the configured moderator ID matches the user ID represented by the
  access token.
- Helix failures retain HTTP status, error class, safe detail, duration, and
  fatal/transient classification. Fatal authentication, scope, client-ID, and
  identity failures stop the worker instead of reconnecting.
- `session_reconnect` now uses Twitch's provided reconnect URL, while ordinary
  failures return to the canonical WebSocket URL with bounded exponential
  backoff.

Runtime verification kept Twitch dry-run enabled and voice disabled. The
machine-local token validation endpoint returned HTTP 401 with `invalid access
token`. The worker now exits with durable `configuration_error`, zero
reconnects, and a traceback in the structured log. The configured token was
verified absent from the structured log, state file, and stderr.

A controlled `channel.follow` event was posted through
`/v1/stream/events?synthesize_speech=false`. The HTTP response preserved the
request ID, the stream trace retained that request ID alongside the generated
Gamma trace ID, and the decision completed in dry-run with voice disabled.

Live acceptance remains blocked on replacing the invalid token in ignored
machine-local configuration. After replacement, validate:

1. The token belongs to the configured moderator/broadcaster user and includes
   `moderator:read:followers`.
2. One `channel.follow` subscription returns HTTP 202.
3. The worker remains connected for at least 15 minutes with stable reconnect
   count and keepalives.
4. A real follow notification reaches Gamma and shares message, event,
   request, and trace identifiers across the operational log and stream trace.

### Current Implementation

`src/gamma/integrations/twitch/eventsub.py`:

- connects to Twitch EventSub WebSocket
- reads the welcome session ID
- creates Helix subscriptions
- supports follows, raids, cheers, subscriptions, resub messages, and channel
  point redeems
- normalizes notifications into `StreamInputEvent`
- posts events through `GammaStreamClient`
- records current status in `data/runtime/twitch_eventsub/state.json`
- reconnects with exponential backoff

Dashboard process lifecycle and status are in:

- `src/gamma/dashboard/service.py`
- `src/gamma/dashboard/main.py`
- `src/gamma/dashboard/static/stream.js`

Tests are primarily in:

- `tests/test_twitch_integration.py`
- `tests/test_dashboard_routes.py`

Operator instructions are in:

- `specs/streamer_plan/twitch_operator_setup.md`
- `specs/streamer_plan/twitch_stream_module.md`

### Immediate Diagnostic Goal

Reduce the active subscription set to one known-required event, preferably
`channel.follow`, and make it remain connected with one successful
subscription. Do not attempt to repair all six subscriptions simultaneously.

Investigate:

- the exact HTTP responses recorded for each failed Helix subscription
- token type, owner, expiration, and required scopes without printing the token
- broadcaster and moderator user ID conditions
- whether duplicate active subscriptions are being created after reconnects
- EventSub welcome timeout and `4003 connection unused`
- whether synchronous subscription HTTP calls delay the WebSocket loop long
  enough for Twitch to close it
- correct handling of Twitch `session_reconnect` URLs

A likely area requiring scrutiny is that subscription creation is synchronous
inside the async WebSocket receive loop. Measure it before deciding whether to
move Helix requests to a thread or async HTTP client.

### Acceptance Criteria

- Worker remains connected for at least 15 minutes.
- At least one subscription is successful and visible in status.
- Reconnect count remains stable during the observation window.
- A real or controlled follow notification reaches `/v1/stream/events`.
- The resulting stream trace includes the external subscription/message
  evidence and Gamma trace ID.
- Missing scopes produce a durable, specific error rather than a generic
  reconnect loop.
- Dry-run remains enabled and voice remains disabled during validation.

### Simple Operational Validation

1. Start Shana and dashboard.
2. Keep Twitch dry-run enabled and voice disabled.
3. Start EventSub from the dashboard.
4. Confirm one successful subscription.
5. Observe 15 minutes of keepalives.
6. Trigger or replay one event.
7. Confirm worker log, state, stream trace, and output decision share enough
   identifiers to reconstruct the path.

## Work Package 3: Audio Understanding Production Validation

The detailed implementation state and commands are in
`audio_understanding_handoff.md`. Do not duplicate or replace that document.

### Immediate Tasks

1. Load the intended Ollama model on GPU 0.
2. Load production faster-whisper and Qwen TTS in their intended placements.
3. Run simultaneous STT, TTS, and audio-understanding requests.
4. Capture baseline, resident, and peak VRAM plus latency and failures.
5. Compare:
   - emotion and AST both on GPU 1
   - emotion on GPU 1 and AST on CPU
   - both audio models on CPU if required
6. Select the placement with acceptable peak headroom, not merely idle memory.

Use:

```bash
.venv/bin/python scripts/probe_audio_gpu_placement.py --start-sidecar
```

The prior isolated result with both models on GPU 1 was:

- 1,020 MiB allocated to the sidecar process
- 6,800 MiB free after inference
- 117-124 ms warm client requests

### Quality Evaluation

Build a small consented and labeled microphone set containing:

- neutral speech
- happy, angry, sad, and excited speech where naturally available
- laughter
- coughing
- clapping
- keyboard and desk impacts
- room noise and silence
- background media or another speaker

Record expected labels, model outputs, confidence, false positives, and missed
events. Tune thresholds from measured precision and recall.

Keep `audio_understanding_prompt_enabled = false` until the evaluation has an
explicit pass threshold. Synthetic sine-wave probes validate runtime and
memory only.

### Acceptance Criteria

- Final device placement and peak VRAM are documented.
- No out-of-memory failures occur during the combined workload.
- Audio-sidecar failure still fails open without breaking STT.
- A labeled evaluation report records per-class precision and recall.
- Prompt influence remains disabled until false-positive behavior is approved.

## Work Package 4: VTube Studio Live Connection

### Current Implementation

`src/gamma/performer/vtube_studio.py` includes:

- configurable WebSocket endpoint
- VTube Studio token request and authentication
- expression and motion hotkey mappings
- translation from generic performer events to hotkey requests
- a runner subscribed to `stream_public` performer events
- connection, authentication, request, action, and error status

API start/stop/status routes are in `src/gamma/api/routes.py`.
Tests are in `tests/test_vtube_studio_adapter.py`.

Current runtime is disabled, disconnected, unauthenticated, and unmapped.

### Simple Next Step

Configure and validate exactly one expression:

1. Run VTube Studio on the Stream PC with its public API enabled.
2. Set the machine-local WebSocket endpoint.
3. Request and approve the plugin token.
4. Save the token only in ignored machine-local configuration.
5. Map Gamma expression `happy` to one VTube Studio hotkey ID.
6. Start the runner.
7. Publish one controlled `expression_set` performer event.
8. Confirm the model changes expression and status records the handled event.

Do not begin mouth tracking or broad motion mapping in this milestone.

### Risks To Address

- The runner currently records only the latest in-memory error.
- Failed actions still increment the handled count.
- There is no durable adapter event history.
- Reconnect behavior needs live testing.
- The endpoint must be reachable from the machine running Gamma; do not alter
  protected proxy architecture for this internal adapter.

### Acceptance Criteria

- Authentication survives a restart using local configuration.
- One `happy` event triggers the intended hotkey.
- Disconnecting VTube Studio produces a durable error and visible degraded
  status without blocking the performer bus.
- Reconnection succeeds after VTube Studio returns.

## Work Package 5: Discord Text-Only Worker

### Current Implementation

`src/gamma/integrations/discord/adapter.py` already:

- represents Discord text messages and transcribed voice utterances
- resolves Discord platform IDs through the shared identity resolver
- normalizes text to `chat_message`
- normalizes transcribed voice to `mic_transcript`
- preserves guild, channel, message, role, trust, and owner metadata

`src/gamma/integrations/discord/runtime.py` currently:

- loads enabled/token/guild/voice-channel/output configuration
- exposes status counters and last input/output
- validates only that a token exists
- changes an in-memory running flag
- recognizes isolated `discord_call` performer events

It does not connect to Discord, receive messages, join voice, transcribe
audio, or play output. Its `handle_output_event()` currently records an event
as handled without transmitting it.

Tests are in `tests/test_discord_adapter.py`.

### Simple Next Step

Implement a separate text-only Discord worker:

- use an established Discord library rather than implementing the gateway
  protocol
- connect with the configured bot token
- allowlist one guild and one text channel
- ignore the bot's own messages
- convert incoming messages to `DiscordMessage`
- normalize with the existing adapter
- post to `/v1/stream/events` through the same boundary used by Twitch
- preserve Discord user ID for identity resolution
- expose supervised start, stop, status, reconnect, and error state
- default to no Discord text reply and no Discord voice output

Keep public Discord text subject to stream input safety and trust handling.
Do not route raw messages directly to `ConversationService`.

### Deferred Voice Scope

Discord voice is a separate milestone requiring:

- voice channel join/leave lifecycle
- per-user audio reception and decoding
- speaker attribution
- buffering and voice activity boundaries
- STT and optional audio understanding
- `discord_call` audio playback
- interruption and output clearing

Do not bundle voice into the initial text worker.

### Acceptance Criteria

- Bot connects to one allowlisted channel.
- One test message becomes a normalized stream input with Discord identity.
- Messages from other guilds/channels and the bot itself are ignored.
- API posting failures and reconnects are durably logged.
- Tokens and private message content are not leaked into operational logs.
- Worker lifecycle is available through the existing supervisor/dashboard
  pattern.

## Cross-Cutting Correlation Model

Use the identifiers already present instead of inventing one overloaded ID:

- `request_id`: HTTP boundary
- `trace_id`: stream decision
- `turn_id`: conversation, live voice, or performer turn
- `event_id`: normalized input or performer event
- `message_id`: Twitch or Discord external message
- `session_id`: conversation or platform session

Each log record should include the identifiers available at that boundary.
When creating a downstream object, retain the upstream identifier in typed
metadata. This should allow an operator to reconstruct:

```text
external message
  -> ingestion worker
  -> HTTP request
  -> stream trace
  -> conversation/voice turn
  -> output dispatch
  -> performer adapter
```

## Definition Of Done

For each work package:

1. Requested behavior is implemented.
2. Relevant specs and current implementation inventory are updated.
3. Focused and full tests pass.
4. `git diff --check` passes.
5. Runtime behavior is verified against real endpoints where applicable.
6. No credentials or runtime data are committed.
7. The final report states exact tests, runtime checks, and remaining
   operational requirements.

## Baseline Validation

Before this handoff was written, the audio-understanding implementation had:

```text
276 passed, 2 skipped, 63 subtests passed
```

No code was changed during the integration/logging audit itself. Re-run the
full suite after implementation because the worktree contains active,
uncommitted audio changes.

## Fresh Session Start

Use this sequence in a new coding session:

1. Read `AGENTS.md`, `README.md`, `specs/README.md`,
   `specs/current_implementations.md`, and this document.
2. Read `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` before touching service
   lifecycle, ports, URLs, WebSockets, or proxy behavior.
3. Run `git status --short` and preserve all existing changes.
4. Inspect the current Twitch worker and EventSub state without exposing
   credentials.
5. Start with Work Package 1 and the EventSub-specific slice of Work Package 2.
6. Run focused tests while developing, then the full suite and runtime
   validation.
