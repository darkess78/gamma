# Modular Shana worker runtime

Status: Design proposal. Runtime refactor not implemented.
Date: 2026-09-19

Revision 2 expands the design after tracing startup, conversation entry points,
state stores, voice jobs, playback, integrations, safety, resource routing,
improvement, and dashboard/log consumers. See the accompanying
[source audit](modular_worker_runtime_audit.md) for evidence and remaining unknowns.
The owner removed the separate architecture-approval rule. This document is a
design and implementation guide, not a new permission gate.

## Goal

Start Shana from the dashboard and independently control brain activity,
listening, speech, avatar presentation, integrations, and background jobs.
Shana can process events and maintain bounded working state without speaking.
Speech synthesis or playback must not block the brain's next eligible cycle.

This proposal preserves the current two-application deployment: dashboard
routes stay on 8001 and Shana routes stay on 8000. No public ports or proxy
changes are proposed. The refactor is about runtime ownership and lifecycle.

## Evidence and existing components

Inspected Windows HEAD `0da6caa` and Gamma PC HEAD `38e3fbf`. Both worktrees
were clean before this proposal. They are different histories/states and must
not be synchronized by copying the entire Windows tree over Gamma PC.

- `conversation/service.py` already supports structured decisions and silent
  delivery, but directly invokes TTS in several response paths.
- `stream/brain.py` also coordinates synthesis and safety review. Its parallel
  review path can return `playback_approved: True` while review is pending.
- `proactive.py` gates cycles on listener availability and active output turns.
  This is suitable for proactive output, but not independent silent cognition.
- `supervisor/manager.py` manages applications and selected sidecars. Extend
  this lifecycle authority instead of adding competing process managers.
- `observability.py` has rotating JSON logs, redaction, and correlation context.
  Extend these contracts rather than introducing an unrelated logging stack.
- `performer/` has an event bus and VTube Studio adapter. Existing specs mark
  live VTube Studio validation as outstanding.
- Existing Minecraft support is a narrow companion integration. It does not
  establish general autonomous game-playing capability.

The core conversation, proactive, stream brain, observability, and supervisor
files above have no diff between the inspected heads. Other code does differ.
These are source findings, not a live runtime acceptance test.

## Ownership

One brain owns Shana's ordered decisions and requests versioned state updates.
A single persistence authority commits those updates and authorized operator
edits. Other LLMs are advisers or task workers whose results become inputs to
that brain. They do not independently write her identity, memory, or conversation
history. Inference must run outside the state/control request loop.

A worker is a capability with an explicit contract and lifecycle. It need not
be an LLM or always occupy a separate process. Use separate processes where
independent restart, blocking inference, language boundaries, or crash isolation
justify them. Keep small deterministic functions as modules.

Dashboard cards represent controllable capabilities, including lightweight
modules that share a host process. They show that host and the consequences of
restarting it. The tables do not mean one new process per row.

### Core ownership and control

| Component | Owns | Placement and controls | Existing source |
| --- | --- | --- | --- |
| Supervisor | Inventory, launch/stop, dependencies, instance identity, restart backoff | Extend existing lifecycle manager; serialize CLI/dashboard mutations; available with brain stopped | `supervisor/manager.py`, `cli.py` |
| Runtime gateway/event hub | Authenticated ingress, job receipts, bounded queues, compatibility APIs | Shana API, responsive without model readiness | `api/routes.py`, `main.py` |
| Attention/autonomy scheduler | Rank/coalesce observations, cooldowns, goal wakeups, foreground/background admission | Lightweight runtime module with pause/budget controls | `proactive.py`, `stream/brain.py:decide`, `voice/idle_policy.py` |
| Brain | Context, structured decisions, conversational realization, goal/tool intents | Dedicated worker; independently startable and pausable | `conversation/service.py`, `turn_contract.py`, `llm/` |
| State/memory authority | Journal, versioned working state, approved memories, emotion persistence, recovery | Single writer behind core-runtime requests initially; extract process only if justified | `memory/`, `persona/assistant_state.py`, `persona/emotion_service.py` |
| Presence/permissions | Audience, modes, capabilities, operator overrides | Runtime policy module with enforcement at execution boundaries | `presence.py`, `identity/`, privacy guards |
| Model admission/provider gateway | Per-endpoint queues, concurrency/resource leases, routing, cancellation accounting | Shared scheduling authority using existing provider adapters | `llm/router_adapter.py`, `resources/` |

### Input, output and presentation

| Component | Owns | Placement and controls | Existing source |
| --- | --- | --- | --- |
| Audio capture | Source selection, VAD, timestamped audio, capture permission/mute | Browser first; optional device-side worker later | `dashboard/static/live.js`, `voice/audio_input.py`, `voice/live.py` |
| STT | Partial/final transcripts with utterance IDs and source metadata | Persistent inference worker independent of TTS; partials do not create duplicate final inputs | `voice/stt.py` |
| Audio perception | Prosody, sound events, optional speaker-affect context | Optional existing sidecar with separate device/budget | `voice/audio_understanding.py`, `audio_understanding_server.py` |
| Vision perception | Requested images or sampled frames into bounded observations | On-demand worker; continuous capture is a separate future adapter | `vision/service.py` |
| Safety review | Rules, contextual checks, model review and receipts | Deterministic gate module plus isolated reviewer; required gate persists if reviewer stops | `safety/`, stream review logic |
| Delivery | Output order, approval validation, cancellation, target routing | Runtime module independent of brain; later process extraction optional | `stream/output.py`, `performer/bus.py`, `performer/turns.py` |
| TTS | Approved text to audio, voice profiles and chunks | Independent worker/adapters; reuse isolated Qwen service | `voice/tts.py`, `voice_profiles.py`, `scripts/qwen_tts_server.py` |
| Playback | Devices/channels, actual playback, immediate stop and acknowledgements | Runs where audio plays; browser first, headless/device adapter later | `dashboard/static/monitor.js`, performer client |
| Avatar | Boot/fallback/idle, bounded emotes, speaking and mouth motion | Separate presentation-machine adapter worker | `performer/vtube_studio.py`, `avatar_events/` |
| OBS/stream production | Scenes, overlays, stream/record state | Future optional adapter separate from Twitch transport | Generic performer events; no functioning OBS controller assumed |

### Integrations and background work

| Component | Owns | Lifecycle and boundary | Existing source |
| --- | --- | --- | --- |
| Twitch group | IRC and EventSub ingress; later approved chat/moderation output | Child connections individually visible; retries do not restart brain | `integrations/twitch/` |
| Discord group | Text ingress; future voice and reply adapters | Report readiness per actual connection/capability | `integrations/discord/` |
| Game group | Sessions and game-specific action execution | Adapter per active game, heartbeat/deadline/emergency stop | Minecraft coordinator and TypeScript sidecar |
| Capability broker | Validate tool, identity, scope, budgets and request ID before execution | Runtime module, including existing internal tools | `tools/`, `stream/actions.py` |
| Search/fetch | Bounded query/fetch jobs with source metadata | On demand; existing web-search capability is not assumed | New broker adapter |
| Specialist models | Classification, research, summarization, planning advice | Job-scoped, shared admission; no direct speech or state mutation | Existing LLM adapters/contracts |
| Improvement/evaluation | Observations, isolated experiments, replay and reports | Optional low-priority worker with isolated state/output | `improvement/`, `stream/replay.py`, `evaluations/` |
| Log collector/query | Common events, filtering, retention and loss reporting | Separate optional collector with local producer fallback | `observability.py` and existing readers |
| Artifact maintenance | Temporary audio/image expiry and job-artifact cleanup | Periodic bounded job, no permanent process required | Existing cleanup/retention code |

Twitch must not own the brain, global safety policy, TTS, or all performer state.
That would recreate the same coupling inside a different worker.

Do not create separate LLM workers for identity, mood, log filtering, process
health or basic routing. These are deterministic services or state. RVC remains
an optional TTS post-process until independent lifecycle needs justify extraction.
Voice dataset tools and coursework remain offline tooling outside Start Shana.

## Practical process layout

Source paths in the worker tables are under `src/gamma/` unless an explicit
repository directory is named. The first working architecture has a small core:

```text
Dashboard / lifecycle commands
    supervisor and authenticated operator APIs

Shana API / runtime core
    ingress, state authority, attention/Presence, delivery, job status
    shared model admission and capability authorization
        |
        +-- brain worker
        +-- STT worker                         when listening is enabled
        +-- TTS adapter / existing Qwen service when speech is enabled
        +-- model safety reviewer              when policy requires it
        +-- audio/vision jobs                  when enabled or requested
        +-- Twitch/Discord/game workers        selected profiles only
        +-- improvement jobs                   low-priority budgeted work

Presentation machine or browser
    playback adapter, avatar worker, optional future OBS adapter

Log collector
    bounded events from all of the above
```

Core modules use explicit interfaces and dependency injection. They do not
secretly construct another brain, memory store or provider. Brain failure leaves
controls, state queries, output cancellation and avatar idle available. Core
runtime failure stops new jobs and expires output authority; the avatar falls
back locally and the supervisor stays accessible.

Split `api/routes.py` into domain routers with a separate composition root.
Split `DashboardService` into runtime controls, configuration, telemetry,
integration proxies and log queries. Keep facade methods during migration.
File movement follows ownership changes; moving functions alone does not
establish worker isolation.

## State, model and delivery contracts

State snapshots carry a version and privacy scope. Brain jobs return decisions
and proposed changes against that version. The state authority rejects or
reconciles stale writes and atomically commits decisions with output/tool outbox
entries. This prevents a crash between committing intent and queuing its work
from silently losing the work. Startup recovery is scoped to terminated worker
generations and expired leases, not every pending database row.

Separate input observed, decision committed, output approved, audio generated,
playback started, playback ended/interrupted, and tool result. Memory must not
report an unheard reply as something Shana successfully said. Keep generated
and actually delivered content distinct, including interruption position when
the playback adapter can report it.

Model clients submit purpose, model constraints, deadline, cancellation
generation and resource budget. Admission is shared across brain, safety,
vision, improvement and local STT/TTS capacity. Process-local VRAM reservations
are not global enforcement. Prioritize interactive work and review of imminent
output above idle thinking and improvement, with fairness for deferred work.
Never hold a brain resource lease while waiting for a tool or review that needs
the same limited inference resource.

If a provider cannot abort inference, discard its stale result but keep its
resource lease until execution exits. A timeout is not proof the GPU is free.
Worker separation permits overlap but does not guarantee simultaneous inference
fits the GPUs. Measure cold/warm use before enabling concurrency or eviction.

Keep the current combined structured decision/final-text generation for ordinary
conversation. Silent cycles can finish without communication. Do not introduce
an obligatory second LLM call to turn every decision into words. Specialist
realization is optional and its text still passes normal output review.

## Data flow and concurrency

```text
STT / Twitch / game observations / dashboard / autonomous timer
                         |
                  bounded input queue
                         |
                  brain and state owner
                   /                \
        state-only completion     output or tool intent
                                     |
                         authorization and safety
                              /             \
                      delivery queue      tool job
                       /         \           |
                  text/avatar    TTS      result event
                                  |
                         approved playback
```

Each envelope carries a schema version, event/job ID, worker identity, trace ID,
session ID, optional turn ID, creation time, expiry, priority, and cancellation
generation. Serialize correlation context across boundaries; context variables
alone do not cross processes. Reject unsupported versions and invalid payloads.

Use bounded queues with visible depth and age. Coalesce disposable idle events,
expire stale observations and speech, and reserve capacity for stop/cancel and
owner input. Acknowledge accepted jobs. Deduplicate retries by job ID and persist
completion receipts for external side effects. Do not promise exactly-once
execution across crashes. An uncertain external action needs reconciliation
before retry. Never replay old speech automatically after restart.

The brain releases its execution slot after committing a decision. It may then
process another input while earlier approved speech plays. New input can cancel
or supersede pending output. Delivery rechecks cancellation, current Presence,
audience, mute state, expiry, and policy version immediately before dispatch.
Workers discard late results from obsolete generations.

Use separate channels for durable accepted jobs, disposable observations,
telemetry, and presentation. The performer bus remains presentation-facing.
Private state, prompts and raw tool results do not belong on it. Use artifact
references instead of duplicating large audio/image blobs through every queue.

Support supervised stdio for new local workers and adapters around existing
authenticated HTTP/WebSocket transports. Reserve stdout for protocol frames
and stderr for diagnostics. Drain both streams, limit frame sizes, validate
schemas, and monitor heartbeats independently of jobs. Do not add a general
broker dependency until throughput or multi-host needs justify it. Transport
changes must preserve acceptance, deduplication, cancellation and visibility.

## Autonomous thinking

Separate permission to run a brain cycle from permission to communicate. An
absent audio listener or disabled TTS must not prohibit a permitted silent cycle.

Cycles are event-driven with bounded idle timers, not an unrestricted inference
loop. Configure cooldown, cycle budget, token/time budget, input priority, resource
admission, and allowed goals. Silence and no-op are valid results. Do not feed
unbounded self-generated text back into the next prompt.

Preserve Sleep and Break behavior initially. Expose internal autonomy and output
permissions separately, without silently enabling autonomy in existing profiles.
An explicit dashboard start profile may opt into autonomous cycles after startup.
Restart never restores public broadcasting without existing live confirmation.

The operator's activity view shows validated summaries and structured facts such
as selected goal, observed event, requested action, delivery choice, and reason
code. It does not request or retain raw model chain-of-thought. Activity summaries
must be labelled as summaries, not a transcript of hidden reasoning.

## Startup and controls

Start logging and the control layer first. Start the avatar as soon as its adapter
is available and show a deterministic boot expression while the brain loads.
Idle animation must continue without model calls. Once ready, approved brain
expression requests temporarily override idle behavior, with expiry and fallback.
Drive lip sync from actual playback, not inference or synthesis completion.

Dashboard worker cards show desired state, observed state, health, heartbeat age,
current job, queue depth, resource allocation, restart count, and last failure.
Offer start, stop, restart, pause/resume where supported, autostart profile,
drain/cancel policy, and filtered logs. Distinguish muted output, paused cognition,
disabled input, and a stopped process. Unsupported controls are visibly unavailable.

Use lifecycle states such as stopped, starting, ready, busy, paused, degraded,
stopping, and failed. A live PID is not readiness. Dependency failures identify
what is blocked. Restart backoff has a cap. Optional worker failure does not
restart the brain. TTS being stopped must leave text and silent cycles usable.

Separate hard startup dependencies from optional capabilities. Brain readiness
depends on accessible state and an eligible model, not TTS. Speech depends on
policy approval, TTS and a ready playback target. Avatar idle depends only on
its local adapter. Stop only providers owned by this runtime, respecting active
leases and other workers that still use them.

Suggested dashboard profiles:

| Profile | Starts/enables | Expected behavior |
| --- | --- | --- |
| Control only | Dashboard and lifecycle controls | Inspect/start/stop workers without loading Shana's models |
| Quiet Shana | Runtime, brain, summaries; configured avatar | Bounded internal cycles and text, no microphone or TTS required |
| Conversation | Runtime, brain, capture, STT, safety, TTS, playback, optional avatar | Normal interruptible conversation |
| Stream | Conversation capabilities plus selected Twitch and presentation adapters | Observation may connect during boot; public output follows live confirmation |
| Game companion | Selected base profile plus one game adapter | Game session and emergency stop visible independently |
| Evaluation | Isolated state/output and selected model workers | Replay/tests without real speech, chat sends or game control |

Profile selection records autostart preference, not a permanent grant to every
tool. Controls distinguish process enabled, worker paused, model resident,
input muted and output muted. Config edits indicate whether they apply now,
to the next job, or after restart. Each job records the effective configuration
version. Keep the current layered TOML precedence and secret references.

Provide an emergency action that cancels speech, pending external actions and
game movement without stopping the dashboard. Adapter-side expiry/watchdogs
handle loss of contact; the operator's stop path never waits for an LLM.

An example session should behave as follows:

1. Start the Conversation profile. Avatar shows booting while the model loads.
2. Avatar begins idle animation; brain readiness changes independently.
3. An allowed idle cycle updates a goal and activity summary without speech.
4. STT supplies one final utterance event; Shana may choose not to respond.
5. If she chooses speech, review approves it and TTS queues audio.
6. Playback begins; the brain can process a new observation concurrently.
7. A confirmed interruption cancels stale audio and tool intents by generation.
8. Stopping TTS leaves brain, STT, text, logs and avatar independently usable.

## Safety and external capability authority

Run normalization, hard rules and configurable profanity policy, contextual
checks, then the configured LLM review before authorizing output. Grammar review
alone is not a safety check. No checker can guarantee platform compliance.

Bind approval to the exact final text/action, target, audience, policy version,
and expiry. Any rewrite requires renewed checks. Required review failure or
timeout blocks that output. Public playback cannot begin while a required review
is pending. Prefer review before TTS initially; speculative synthesis can be
considered later only if unapproved audio is inaccessible to playback clients.

Allow the owner to tune profanity and review profiles. Preserve privacy,
authorization, mandatory blocks, and emergency-stop enforcement independently.
The brain cannot switch off its own required checks or grant itself capabilities.
Text, subtitles, Twitch sends, and game actions need the appropriate checks too.

Shana requests named permitted capabilities, not arbitrary process commands.
The supervisor resolves a registered worker manifest with an allowlisted launcher,
protocol version, resource budget, permissions, timeout, and idle-shutdown policy.
Cross-language workers can use versioned JSON over supervised stdio initially;
existing sidecars can retain their authenticated transports behind adapters.
Remote workers require authenticated channels and scoped credentials.

Game workers should share a small job contract but use game-specific adapters.
Minecraft can adapt the existing Mineflayer sidecar. Its current movement and
authorization limits remain until separately implemented and tested. Search can
be a short-lived job without starting a new process for every request.

Treat profanity allowance, optional stylistic rewriting, contextual review and
mandatory privacy/authorization checks as distinct settings. The existing broad
`none` policy must not become a blanket runtime permission bypass. A missing or
malformed review response is an unavailable verdict, never implicit approval.
Changing a profile or audience invalidates pending approvals when relevant.

Tool results carry provenance and remain untrusted observations. External text
cannot register workers, grant capabilities, change settings or request arbitrary
shell execution merely by appearing in a result. Worker manifests declare their
input/output schemas, permitted resources and execution identity. Registering a
C++, C#, Python, Node or remote API adapter does not require changing the brain's
conversation implementation.

## Logging

Use common fields for worker, instance, event type, severity, trace, turn, job,
duration, outcome, and stable reason code. Provide filters for all workers,
selected workers, types, severity, time, and one complete interaction trace.
Viewer filters do not change what other viewers see or silently disable storage.

Redact and allowlist fields before publishing logs. Keep transcript/content
logging opt-in and access-controlled. Retain structured summaries only for brain
activity. Bound payload size, rotation, retention, and dashboard pagination.

Producers must not block indefinitely on the collector. Use bounded local
buffers/files with explicit dropped-event counters and degraded logging status.
Avoid multiple processes rotating the same file. Mandatory safety decisions need
an auditable receipt before external dispatch; general telemetry is separate.

Keep operational telemetry, activity summaries and audit receipts typed and
separately queryable. Offer a concise Activity view, a filterable Logs view, and
an interaction trace spanning input through playback/action. Categories include
lifecycle, input, decision, state, inference, safety, synthesis, playback, tool,
resources and errors. Show suppression reasons such as no listener, worker
paused, safety unavailable or resource queue full.

Existing timing, routing, stream and live-job readers need compatibility adapters
while producers adopt the common envelope. Ingestion handles rotation, partial
trailing records, duplicates and restarts. Dashboard filtering is bounded and
server-side; avoid scanning every history file on every poll. Reserve correlation
fields so worker-supplied fields cannot overwrite identity or trace attribution.
Provider exceptions and stderr need redaction too.

Artifact maintenance retains files referenced by active jobs, expires abandoned
leases, and removes only files under configured owned artifact roots. It reports
counts and errors. It is not a general cleanup process for arbitrary paths.

## Migration and acceptance

Implement vertical slices that exercise the real entry point, queue, worker,
state, output and dashboard. Avoid a large unused worker framework followed by
an all-at-once switch. Use scoped coding-worker proposals, independent tests and
Codex review for implementation work.

| Phase | Change | Evidence required before replacing its old path |
| --- | --- | --- |
| 0. Baseline | Isolated Gamma-PC checkout using its current source; map all conversation/state writers and capture existing behavior | Relevant tests, cold/warm timings, representative text/voice/stream fixtures; actual source import path; record pre-existing failures |
| 1. Lifecycle and logs | Common worker manifests/status, serialized supervisor controls, common event envelope and dashboard worker/log views over existing services | Start/stop one existing optional worker without restarting Shana; correlate its work; bounded log filtering and loss visibility |
| 2. State ownership | Single persistence authority, versioned updates, scoped recovery, decision/outbox transactions | Concurrent voice/text input and worker construction cannot interrupt another generation's pending exchange; reject stale state and prove crash recovery |
| 3. Delivery and safety | Remove TTS from conversation/StreamBrain authority; pre-execution tool gate; required review before publication; playback acknowledgements | TTS off permits text/state-only work; slow/invalid review releases no output; synthesis completion cannot falsely complete playback |
| 4. Persistent brain | All input paths use one brain queue; silent autonomy independent of output; shared model admission | Background cycle without listener; new decision during playback; bounded queues; no helper bypass of resource admission |
| 5. Voice and avatar | Replace bundled live-voice child through `LiveTurnRuntime`; independent capture/STT/TTS/playback and boot/idle/avatar adapter | Real audio/adapter tests for boot, interruption, lip sync, stopping TTS and independent restarts |
| 6. Existing integrations | Register Twitch/EventSub, Discord text, Minecraft and improvement with common lifecycle/status; preserve domain protocols | Fast ingress receipt, duplicate/reconnect handling, Minecraft cancel/watchdog behavior, improvement isolation and idle shutdown |
| 7. New capabilities | Search, additional model advisers, headless capture/playback, OBS and additional games as separately tested adapters | One job accepted/executed/reported/cancelled through its real adapter with scoped permissions and bounded resource use |

Preserve existing response schemas with compatibility facades that may await
jobs. Add asynchronous APIs explicitly. Keep optional legacy paths behind a
temporary migration setting until each replacement passes its tests. Never run
both paths against real speech or external actions; comparisons use isolated
outputs. Remove the legacy path after its replacement passes acceptance.

Before state/schema migration, retain a verified backup and define whether the
old version can read the new state. Rollback must not replay a tool action or
restore stale live-output permission. Do not copy the Windows checkout over
Gamma PC or include coursework/runtime data in an integration patch.

Each phase needs behavioral evidence before the next replaces its old path:

- Stop TTS and complete a real silent brain cycle and a text turn.
- Hold playback open while the brain handles a new event without waiting for it.
- Slow, fail, or block safety review and prove no public output escapes.
- Cancel during synthesis and prove the late result never plays.
- Restart after synthesis and prove stale speech and external actions do not replay.
- Crash optional workers and verify brain health and actionable dashboard status.
- Start the avatar before model readiness and observe boot then idle behavior.
- Trace STT input through brain decision, safety, TTS, and playback using one trace.
- Filter logs by worker/type and verify redaction, bounded reads, and loss counters.
- Restart one brain instance while another generation has pending work; reconcile
  only the expired instance's work and reject its late commits.
- Submit a tool result containing instructions to change permissions; prove it
  cannot bypass the capability broker or mutate operator configuration.
- Fill the observation queue and stall a worker; prove stop/cancel still works,
  overload is reported, and memory use stays bounded.
- Give a provider an uncancellable job; prove a client timeout does not release
  capacity while that provider is still executing it.
- Disconnect and reconnect two playback targets; prove acknowledgements belong
  to the correct target lease and stale audio does not replay.
- Exercise existing dashboard HTML and API routes, authentication, Presence,
  privacy, memory continuity, performer events, and live voice compatibility.

Run Python/runtime validation on Gamma PC with its repository virtual environment.
Check changed JavaScript syntax and `git diff --check`. Live avatar/audio tests
require actual adapters and listeners. Unit tests alone cannot establish those
outcomes. This documentation task changes no runtime code, services, model
configuration, public streaming, or Git remotes. Implementation is not blocked
by a separate architecture-approval rule; the current request is to improve the
design through project inspection.
