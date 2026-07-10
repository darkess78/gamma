# Minecraft Companion Integration Inspection And Implementation Plan

Status: Proposed

Inspection date: 2026-07-10

This document records the read-only repository inspection and concrete implementation plan for adding a companion-first Minecraft Java Edition integration for Shana. It proposes no change to the protected deployment topology in `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`.

## 1. Preflight State

Confirmed repository facts:

| Check | Observed state |
| --- | --- |
| Repository | `/home/neety/Documents/gamma-main` |
| Branch | `refactor/persistent-shana-core` |
| HEAD | `8297ee2005cdef589285f315b366d375bf87f4b9` |
| Subject | `refactor: retire desktop tray subsystem` |
| Worktree | Clean |
| Upstream | `origin/refactor/persistent-shana-core` |
| Ahead/behind | 17 ahead, 0 behind |
| Python | 3.14.4 via `.venv/bin/python` |
| Node | v22.22.2 |
| npm | 11.12.1 |
| Port 8000 | Listening on `127.0.0.1`, Python PID 200489 |
| Port 8001 | Listening on `127.0.0.1`, Python PID 200298 |

The starting state matched every expected Git condition. Both expected services appeared to be running. No mismatch was corrected or otherwise acted upon.

## 2. Baseline Validation Results

Canonical test configuration is in `pyproject.toml`, where `testpaths = ["tests"]` and `tests/integration` is excluded. The documented full-suite command is in `README.md`.

Exact command:

```bash
.venv/bin/python -m pytest -q
```

Result:

- Passed: 389
- Failed: 0
- Skipped: 2
- Errors: 0
- Subtests passed: 74
- Runtime: 17.32 seconds
- Warning: one Starlette deprecation warning concerning `httpx`/`TestClient`
- Existing-state failures: none

`git diff --check` also passed with no output.

The recent tray-removal cleanup therefore has a fresh, passing full-suite baseline.

## 3. Repository Rules That Apply

Material rules found in `AGENTS.md`, `specs/architecture.md`, and the protected network deployment specification:

- Active source is exclusively under `src/gamma`; top-level `gamma/` is stale bytecode.
- Shana and Dashboard remain separate FastAPI processes on ports 8000 and 8001.
- Minecraft intent, policy, conversation, memory, voice, performer state, and integration health are Shana-owned.
- Dashboard may authenticate operators, proxy supported Shana APIs, show status, and manage processes. It must not instantiate the Minecraft coordinator or access Shana-owned state directly.
- Dashboard-owned `/api/*` routes must not be moved to Shana.
- Shana-owned Minecraft routes should be under `/v1/*`; the sidecar control socket can therefore use the existing Shana listener rather than a third public port.
- Public URLs and internal bind addresses must remain separate.
- WebSocket routing must remain reverse-proxy compatible.
- `specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md` must never be edited.
- Portable, non-secret defaults belong in `config/app.example.toml`.
- Host-specific paths, account data, and secrets belong in `.env` or `config/app.local.toml`.
- Optional integrations must be disabled by default and fail closed when incompletely configured.
- Dashboard JavaScript changes must be modular; no logic should be restored to a monolithic `dashboard.js`.
- Every changed JavaScript file requires `node --check`.
- Public contracts require corresponding specs/tests.
- Runtime state belongs under ignored `data/`, never in tracked files.
- Existing dirty changes must be preserved; the inspection began and ended clean.
- `specs/roadmap.md` currently says game adapters were frozen during Persistent Shana. The explicit product decision authorizes planning this later milestone, but implementation should update the roadmap/specification rather than silently contradict it.
- Discord and VTube Studio are active future integrations and must remain intact.

## 4. Relevant Existing Architecture

Confirmed architecture:

```text
Browser
  -> authenticated Dashboard API
  -> Shana HTTP API
  -> Shana-owned services
```

The dependency direction is explicit in `specs/architecture.md` and enforced by `tests/test_dashboard_boundary.py`.

Reusable primitives include:

- `LazySingleton` for Shana-owned services: `src/gamma/system/lazy_singleton.py:10`.
- FastAPI lifespan startup/shutdown: `lifespan()` in `src/gamma/main.py:13`.
- Shana-owned bounded background task lifecycle: `ProactiveScheduler.start()`, `stop()`, and `run()` in `src/gamma/proactive.py:15`.
- External worker process management: `ProcessManager.start_module()`, `stop_module()`, and `module_status()` in `src/gamma/supervisor/manager.py:283`.
- Worker reconnection, backoff, heartbeat evidence, redacted state, and fail-closed configuration: `TwitchIrcWorker` in `src/gamma/integrations/twitch/worker.py:95`.
- Generic WebSocket client with narrow adapter API: `VTubeStudioClient` in `src/gamma/performer/vtube_studio.py:58`.
- In-memory ordered event delivery, bounded replay, queue overflow behavior, and subscriber lifecycle: `PerformerEventBus` in `src/gamma/performer/bus.py:37`.
- Command-like cancellation with terminal states: `LiveVoiceJobManager.cancel_job()` and `cancel_active_jobs()` in `src/gamma/voice/live_jobs.py:333`.
- Restart-safe stale state handling: `ContinuityService.mark_pending_interrupted()` through construction in `src/gamma/memory/continuity.py:67`, and Presence stale-live downgrade in `src/gamma/presence.py:160`.
- Structured request/trace context and secret redaction: `bind_context()`, `log_event()`, and `redact()` in `src/gamma/observability.py:20`.
- Shana-to-Dashboard proxy boundary: `ShanaApiClient` in `src/gamma/dashboard/shana_client.py:20`.

No existing generic game framework, action executor, reusable command protocol, or resumable external-action state machine exists.

## 5. Closest Existing Integration Precedents

The closest overall precedent is the Twitch worker family, but Minecraft should combine several existing patterns:

1. **Twitch IRC/EventSub workers — closest operational precedent.** They are optional external integrations with configuration validation, durable redacted status, reconnect/backoff, structured evidence, supervisor controls, and Shana API ingestion. See `TwitchIrcWorker.run_forever()` in `src/gamma/integrations/twitch/worker.py:208` and `TwitchEventSubWorker` in `src/gamma/integrations/twitch/eventsub.py:108`.
2. **Discord runtime — closest bounded adapter boundary.** `DiscordRuntime` owns configuration/status and normalization without embedding Discord logic in conversation. See `src/gamma/integrations/discord/runtime.py:36`.
3. **VTube Studio — closest internal WebSocket client example.** It narrows platform APIs behind an adapter and records connection/authentication status. See `src/gamma/performer/vtube_studio.py:58`.
4. **Proactive scheduler — closest Shana-owned long-running task lifecycle.** It starts and stops with Shana, is disabled by default, and applies deterministic gates before model work. See `src/gamma/proactive.py:15`.
5. **Live voice jobs — closest cancellation/terminal-outcome precedent.** They model explicit terminal states, cancellation reasons, and cancel latency. See `src/gamma/voice/live_jobs.py:265`.

Minecraft differs because it needs bidirectional commands, immediate reflexes, authoritative sidecar state, deduplication, and strict movement cancellation. No single existing integration provides all of those.

## 6. Recommended Minecraft Architecture

Recommended flow:

```text
Authorized voice / Monitor / Dashboard / owner Minecraft chat
  -> Shana-owned MinecraftIntentService
  -> MinecraftPolicy validates requester, feature, state, bounds
  -> MinecraftCoordinator assigns command_id/deadline/trace_id
  -> existing Shana WebSocket endpoint
  -> outbound-connected Mineflayer sidecar
  -> bounded TypeScript executor and immediate safety reflexes
  -> protocol events/results
  -> coordinator state + Minecraft trace
  -> optional StreamInputEvent
  -> existing StreamBrain/output dispatcher/performer bus
```

### Required design decisions

| # | Decision and chosen option | Alternatives and evidence | Lock status |
| ---: | --- | --- | --- |
| 1 | Python package: `src/gamma/integrations/minecraft/` | Better than placing platform logic in conversation or stream. Architecture names `integrations` for bounded external adapters. | Lock before implementation |
| 2 | TypeScript sidecar: `sidecars/minecraft/` | `scripts/` is launcher-oriented; `helper_projects/` contains separate tooling rather than runtime dependencies. A top-level sidecar directory makes the process boundary explicit. | Lock before implementation |
| 3 | Sidecar is a WebSocket client; Shana is server | Matches the existing Shana WebSocket ownership direction, avoids a new exposed listener, and lets the sidecar stop immediately when its controlling socket closes. | Lock |
| 4 | Sidecar initiates connection | Alternatives are Gamma dialing the sidecar or both exposing servers. Outbound sidecar connection is simpler to supervise and safer under reverse proxies. | Lock |
| 5 | Use `ws://127.0.0.1:8000/v1/minecraft/control`; no new port | Reuses the Shana listener and protected route ownership. Do not change the locked deployment topology. | Lock |
| 6 | Require loopback plus a dedicated control token when enabled | Loopback-only without authentication is vulnerable if proxying or host boundaries change; global API bearer alone over-couples integrations. Token must be supplied only through local config/environment. | Lock |
| 7 | Python protocol models are canonical; target spec documents semantics | Gamma owns command/policy semantics. Pydantic is already the Python schema convention. | Lock |
| 8 | Hand-maintained TypeScript validators plus shared JSON fixtures; no code generation | Generation adds tooling and versioning complexity. Shared fixtures give cross-language evidence without a schema-generation framework. | Flexible after v1 |
| 9 | `MinecraftCoordinator` runs inside Shana as a lifespan-managed async service | A separate Python worker would duplicate Shana-owned policy/state and require another IPC hop. `ProactiveScheduler` proves lifespan-managed background tasks. Mineflayer itself remains separate. | Lock |
| 10 | `[minecraft].enabled = false` in `config/app.example.toml`; local override required | Matches VTube Studio/Discord nested optional config. | Lock |
| 11 | Coordinator owns authoritative Gamma health/status; sidecar snapshots are reported facts | Dashboard must query Shana. Sidecar process status may be added by Dashboard/supervisor, but companion state remains Shana-owned. | Lock |
| 12 | Dashboard controls proxy to `/v1/minecraft/*`; never connect directly to sidecar | Follows existing `DashboardService` proxy methods. | Lock |
| 13 | Add an integration-specific bounded JSONL trace under `data/runtime/minecraft/traces.current.jsonl` using existing observability context | Stream traces assume `StreamTurnResult`; forcing raw movement events into them would distort the stream contract. Correlated speech still receives normal stream traces. | Flexible storage format; lock correlation fields |
| 14 | Raw Minecraft facts do not become new performer event types; selected facts become `StreamInputEvent(kind="game_state")`, then normal output events | Performer types are presentation events. This avoids a parallel presentation architecture. | Lock |
| 15 | Add a dedicated `MinecraftIntentService` before coordinator dispatch; do not register movement as a normal conversation tool | Existing tools execute synchronously in `ConversationService._execute_tool_calls()`, while `ActionPlanner` audits after execution. That is unsafe for movement. | Lock |
| 16 | Minecraft chat is normalized as untrusted input; only strict owner-command syntax is considered actionable | All other chat/sign/book/item/server text is context-only and never enters system/policy prompts. Twitch sanitization is precedent. | Lock |
| 17 | Online mode: owner UUID is mandatory and authoritative; username is display/fallback only. Offline dev mode: normalized configured username is allowed with explicit warning | Existing game identity matches names only, which is insufficient for online-mode authorization. | Lock |
| 18 | Sidecar owns the execution state machine; coordinator mirrors and validates it | Sidecar must stop safely even when Gamma is gone. Gamma retains desired-command and authorization state, but cannot be the only movement-state authority. | Lock |
| 19 | Sidecar owns immediate reflexes: clear movement, avoid illegal dimensions, flee/warn on critical danger, never attack players | Network/LLM latency makes Gamma unsuitable for tick-level safety. | Lock |
| 20 | Restart always creates a new control session and returns to non-moving `IDLE`; no command restoration | Mirrors continuity's interruption-on-reconstruction principle. | Lock |
| 21 | Structured logging through `log_event`; pass all payloads through `redact`; never log raw auth cache, tokens, full chat, books, signs, or item names | Existing redaction covers token/password/secret keys. | Lock |
| 22 | No database table in MVP; keep live state in memory and bounded trace/status files under ignored `data/runtime/minecraft/` | Existing long-term memory is selective and raw untrusted input must not be promoted. | Flexible after soak evidence |
| 23 | Most tests use an in-process fake sidecar and shared fixtures; real-server tests remain opt-in | Matches the repository's default exclusion of `tests/integration`. | Lock |
| 24 | Extend `ProcessManager` with a narrowly defined managed command service for the Node sidecar; Dashboard owns start/stop controls | `ManagedService` assumes uvicorn, while `start_module()` assumes Python. Do not misrepresent Node as either. | Lock before process work |
| 25 | One sidecar-local `package.json` and committed `package-lock.json`; npm only; exact runtime pins | Repository has no Node package today. npm is already present. Do not add pnpm/yarn/workspaces. Add `node_modules/` to `.gitignore`; global `dist/` is already ignored. | Lock |
| 26 | Pin Minecraft to exact `1.21.11` initially and reject mismatched negotiated versions | Mineflayer 4.37.1 lists support through 1.21.11 and requires Node 22; the inspected machine satisfies that requirement. | Lock for MVP |
| 27 | Initially pin `mineflayer: 4.37.1` and `mineflayer-pathfinder: 2.4.5`, then change only through a compatibility-validation commit | Pathfinder 2.4.5 is comparatively old and defaults to digging/building-capable movement, so Gamma must explicitly disable those features. | Exact pins lock per validated release |

Primary external compatibility references:

- <https://github.com/PrismarineJS/mineflayer>
- <https://raw.githubusercontent.com/PrismarineJS/mineflayer/master/package.json>
- <https://www.npmjs.com/package/mineflayer-pathfinder>
- <https://raw.githubusercontent.com/PrismarineJS/mineflayer-pathfinder/master/package.json>

## 7. Exact Proposed File Layout

Proposed target layout:

```text
specs/
  minecraft_companion.md

src/gamma/
  integrations/minecraft/
    __init__.py
    config.py
    coordinator.py
    intent.py
    models.py
    policy.py
    protocol.py
    trace.py
  schemas/
    minecraft.py

sidecars/minecraft/
  README.md
  package.json
  package-lock.json
  tsconfig.json
  src/
    index.ts
    config.ts
    protocol.ts
    control-client.ts
    companion-state.ts
    command-executor.ts
    mineflayer-runtime.ts
    safety.ts
    status.ts
  test/
    protocol.test.ts
    companion-state.test.ts
    command-executor.test.ts
    safety.test.ts

tests/
  fixtures/minecraft_protocol/v1/
    handshake.json
    command-follow-owner.json
    command-ack.json
    state-snapshot.json
    terminal-result.json
    error.json
  test_minecraft_protocol.py
  test_minecraft_coordinator.py
  test_minecraft_authorization.py
  test_minecraft_routes.py
  test_minecraft_lifecycle.py
  test_minecraft_trace.py

tests/integration/
  test_minecraft_fixture_server.py

src/gamma/dashboard/static/
  minecraft.mjs
```

Expected edits later:

- `src/gamma/main.py`
- `src/gamma/api/routes.py`
- `src/gamma/config.py`
- `src/gamma/stream/models.py`
- `src/gamma/dashboard/main.py`
- `src/gamma/dashboard/service.py`
- `src/gamma/dashboard/static/index.html`
- `src/gamma/dashboard/static/dashboard.css`
- `src/gamma/supervisor/manager.py`
- `src/gamma/supervisor/cli.py`
- `config/app.example.toml`
- `.gitignore`
- `README.md`
- `specs/README.md`
- `specs/current_implementations.md`
- `specs/roadmap.md`
- `tests/test_api_routes.py`
- `tests/test_dashboard_routes.py`
- `tests/test_dashboard_boundary.py`
- `tests/test_packaging.py`

No change should be made to `config/app.toml` merely to enable the feature. Machine-specific activation belongs in ignored local configuration.

## 8. Ownership Boundaries

### Shana API owns

- Feature enablement and policy.
- Owner authorization.
- Natural-language intent interpretation.
- Command IDs, trace IDs, deadlines, cancellation, and terminal-result tracking.
- Sidecar control WebSocket.
- Companion status and state exposed to other Gamma components.
- Deciding whether an event yields speech, subtitles, chat, Monitor output, or silence.
- Conversation and memory decisions.
- Integration trace storage.

### Sidecar owns

- Minecraft protocol and authentication.
- Mineflayer lifecycle.
- Pathfinder and movement controls.
- Companion execution state.
- Position, health, hunger, dimension, entity, and path facts.
- Immediate movement cancellation and danger reflexes.
- Enforcement against digging, placing, containers, PvP, and forbidden dimensions.
- Bounded command execution and terminal outcomes.
- Stopping on control-channel loss.

### Dashboard owns

- Session authentication.
- Displaying Shana-reported Minecraft status.
- Proxying controls to Shana.
- Starting/stopping the separate Node process through the supervisor.
- Never constructing `MinecraftCoordinator` or reading its state files directly.

### Performer/output system owns

- User-facing speech/subtitle/expression/motion events after Gamma decides to speak.
- It does not carry raw per-tick Minecraft telemetry.

## 9. Companion State Machine

### States

| State | Meaning | Entry actions | Exit actions | Permitted commands |
| --- | --- | --- | --- | --- |
| `DISCONNECTED` | No active Minecraft server session | Clear pathfinder and controls; cancel active command | None | `join`, `report_status`, `emergency_stop` |
| `IDLE` | Connected, spawned, alive, no movement goal | Clear stale goal; emit snapshot | Cancel pending reflex timer | `leave`, `follow_owner`, `wait_here`, `come_here`, `look_at_owner`, `report_status`, `stop`, `emergency_stop` |
| `FOLLOWING` | Maintaining bounded distance to owner | Install dynamic owner goal; start deadline/stall tracking | Clear dynamic goal and controls | `wait_here`, `come_here`, `look_at_owner`, `report_status`, `stop`, `emergency_stop`, `leave` |
| `WAITING` | Holding current safe position | Clear goal and controls; remember wait anchor ephemerally | Discard wait anchor | `follow_owner`, `come_here`, `look_at_owner`, `report_status`, `stop`, `emergency_stop`, `leave` |
| `RETURNING` | Executing `come_here` toward current owner location | Install bounded goal; start timeout/radius checks | Clear goal | `wait_here`, `report_status`, `stop`, `emergency_stop`, `leave` |
| `FLEEING` | Immediate non-combat danger reflex | Cancel user goal; choose short safe retreat bounded by radius/time | Clear retreat goal; emit terminal result for interrupted user command | `report_status`, `stop`, `emergency_stop`, `leave` |
| `DEAD` | Bot death observed; no movement possible | Cancel active command as `BOT_DEAD`; clear controls | None | `report_status`, `leave`, `emergency_stop` |
| `STOPPED` | Emergency-stop latch is active | Force `setGoal(null)`, clear all control states, reject movement commands | Latch clears only through `leave` followed by a fresh `join`, or process restart | `report_status`, `leave`, `emergency_stop` |

`EATING` and `DEFENDING` are not MVP states. They should not be added until their commands exist. `FLEEING` exists only as a bounded safety reflex, not as a user command.

### Valid transitions

| From | To | Trigger |
| --- | --- | --- |
| `DISCONNECTED` | `IDLE` | Successful join, spawn, owner-independent initialization |
| `IDLE` | `FOLLOWING` | Authorized `follow_owner` accepted |
| `IDLE`/`WAITING`/`FOLLOWING` | `RETURNING` | Authorized `come_here` accepted |
| `IDLE`/`FOLLOWING`/`RETURNING` | `WAITING` | `wait_here` completed |
| Movement states | `IDLE` | `stop`, successful one-shot completion, ordinary cancellation |
| Alive states | `FLEEING` | Critical health or immediate environmental threat |
| `FLEEING` | `IDLE` | Safe distance reached or short reflex deadline reached |
| Any connected state | `DEAD` | Death event |
| `DEAD` | `IDLE` | Respawn event; never restore prior movement |
| Any state | `STOPPED` | Emergency stop |
| Any connected state | `DISCONNECTED` | `leave`, server disconnect, kick, authentication failure |
| Any movement state | `IDLE` | Gamma/control WebSocket disconnect while Minecraft remains connected |
| Any state | `IDLE` or `DISCONNECTED` | Sidecar restart, depending on whether Minecraft reconnect is explicitly requested |

### Exceptional behavior

- Timeout: clear path and controls; emit `command_timed_out`; transition to `IDLE`.
- Owner loss: stop dynamic following immediately, allow a short configured grace window without moving farther, then fail `OWNER_NOT_PRESENT` or `OWNER_TOO_FAR`; transition to `IDLE`.
- Gamma disconnect/control-channel loss: clear all movement immediately; terminalize active work as `SIDECAR_DISCONNECTED`; do not resume on reconnect.
- Sidecar process loss: coordinator marks sidecar unavailable and terminalizes active command.
- Minecraft server disconnect: sidecar enters `DISCONNECTED`; active command fails `MINECRAFT_SERVER_DISCONNECTED`.
- Death: enter `DEAD`; cancel active movement.
- Respawn: enter `IDLE`; never resume the pre-death command.
- Emergency stop: enter `STOPPED`, clear pathfinder and every control state synchronously before acknowledging.
- Process restart: all command caches and movement goals are discarded; state begins non-moving.
- Prior state restoration: prohibited. Only non-actionable last-result/trace history may survive.

## 10. Command Protocol

### Common v1 envelope

```json
{
  "protocol": "gamma.minecraft",
  "version": 1,
  "type": "command",
  "message_id": "unique-message-id",
  "connection_id": "control-session-id",
  "sent_at": "UTC ISO-8601",
  "trace_id": "gamma-trace-id",
  "command_id": "stable-command-id",
  "payload": {
    "name": "follow_owner",
    "deadline_at": "UTC ISO-8601",
    "requested_by": {
      "source": "dashboard",
      "platform_id": "owner",
      "authorization": "owner"
    },
    "args": {}
  }
}
```

Every command gets:

1. `command_ack` with `accepted` or `rejected`.
2. Optional throttled `command_progress`.
3. Exactly one terminal `command_result`: `completed`, `cancelled`, `failed`, `rejected`, or `timed_out`.

### Connection and reliability messages

- Sidecar sends `hello` with supported versions, sidecar build, instance ID, capabilities, Node version, Mineflayer version, pathfinder version, and current non-moving state.
- Gamma sends `welcome` selecting v1 and assigning `connection_id`, heartbeat interval, maximum message size, and policy limits.
- No common version produces `protocol_error` and close code 1002.
- Ping/pong interval: 5 seconds; stale after 15 seconds.
- Sidecar status and Minecraft status are separate payloads.
- State snapshot is sent on handshake, state transition, material health/hunger/owner change, and at a low maximum cadence such as every 5 seconds while moving.
- Duplicate `command_id`: return the cached ack/result without re-execution.
- Sidecar retains a bounded in-memory terminal-result cache for at least 10 minutes or 1,000 commands.
- Expired `deadline_at`, wrong `connection_id`, or previous-control-session commands are rejected as stale.
- `cancel_command` names the target `command_id`.
- `emergency_stop` is a distinct highest-priority message and may preempt any command.
- Gamma graceful shutdown sends `shutdown`; sidecar clears movement, acknowledges, and may remain connected to Minecraft only if policy explicitly permits. MVP recommendation: leave the server.
- Unexpected socket loss clears movement before reconnect backoff.
- Reconnection requires a new handshake and state snapshot; no command replay.

### MVP command definitions

| Command | Definition and limits | Invocation sources |
| --- | --- | --- |
| `join` | Args: optional approved connection profile ID; server/version come from policy, not arbitrary request input. Preconditions: enabled, sidecar connected, `DISCONNECTED`, owner configured. Deadline 45s. Success: spawned in Overworld and `IDLE`. Failures include auth, version mismatch, timeout, forbidden dimension. Cancellation disconnects partial login. | Voice: yes. Monitor: yes. Dashboard: yes. Minecraft chat: no, because bot is absent. Reflex: no. |
| `leave` | No required args; optional reason. Valid in any Minecraft-connected/dead state. Deadline 15s. Clears movement before disconnect. Success: `DISCONNECTED`. | Voice/Monitor/Dashboard/chat: yes when authorized. Reflex: yes for forbidden dimension or shutdown. |
| `follow_owner` | Optional `distance` and duration, clamped to policy. Requires alive, Overworld, owner present/authorized. Default distance 3 blocks; permitted 2–6. Max command duration 15 minutes, max displacement 64 blocks from start, owner-loss grace 10s, owner distance cap 32 blocks, 3 path retries. | Voice/Monitor/Dashboard/chat: yes. Reflex: no. |
| `wait_here` | No required args. Valid while alive/connected. Clears goal and controls immediately; transition to `WAITING`. Deadline 2s. Success requires `isMoving() == false`. | All owner sources: yes. Reflex: yes as an ordinary safety stop. |
| `come_here` | Optional arrival distance, clamped 2–4 blocks. Requires owner visible and within 32 blocks. Deadline 60s, max radius 64 blocks, 3 retries, stall timeout 5s. Success: reaches radius and enters `IDLE`. | Voice/Monitor/Dashboard/chat: yes. Reflex: no. |
| `look_at_owner` | Optional maximum look duration, capped at 10s. Requires owner present and visible in same dimension. No movement. Success: rotation request applied. | Voice/Monitor/Dashboard/chat: yes. Reflex: no. |
| `report_status` | No required args; optional detail level from fixed enum. Valid in every state. Returns bounded snapshot: state, connections, owner presence, dimension, rounded position, health, hunger, active command, last result/failure. Must never include tokens or raw untrusted text. | Voice/Monitor/Dashboard/chat: yes. Reflex: yes for diagnostics, but no speech decision in sidecar. |
| `stop` | Optional reason. Cancels ordinary active command, sets goal null, clears controls, enters `IDLE`; deadline 2s. Idempotent. Does not clear a `STOPPED` emergency latch. | Voice/Monitor/Dashboard/chat: yes. Reflex: yes. |
| `emergency_stop` | Optional reason only. Valid always, bypasses ordinary command queue, clears pathfinder and controls before ack, terminalizes active work, enters `STOPPED`. Target reaction under 250ms inside sidecar. | Voice/Monitor/Dashboard/chat: yes, but Dashboard gets a dedicated confirmed control. Reflex: yes. |

Explicitly out of scope as commands: `eat`, `sleep`, `defend_self`, user-directed `flee`, `return_to_owner`, gathering, breaking, placing, containers, building, crafting, PvP, and dimension travel.

## 11. Event Protocol

Common required fields for every event: `event_id`, `connection_id`, `occurred_at`, `sequence`, `state`, `trace_id` when applicable, and `command_id` when applicable. “Performer: derived” means the fact may trigger a Gamma-generated response that uses the existing performer bus; the raw event itself is not a performer event.

| Event | Specific fields; frequency | Persistence / routing |
| --- | --- | --- |
| `sidecar_connected` | instance/build/capabilities/protocol; once per handshake | Trace yes; Monitor yes; performer no; speech normally no; chat no |
| `sidecar_disconnected` | reason, clean flag, last sequence; once | Trace yes; Monitor yes; performer derived; speech conditional warning; chat no |
| `minecraft_connecting` | host redacted to safe label, port, version, account mode; once/attempt | Trace yes; Monitor yes; no performer/speech/chat |
| `minecraft_connected` | negotiated version, server brand if bounded, bot UUID; once | Trace yes; Monitor yes; performer derived; speech/chat conditional |
| `minecraft_disconnected` | stable reason code, kicked flag; once | Trace yes; Monitor yes; performer derived; speech conditional; chat impossible |
| `spawned` | dimension, rounded position; once/spawn | Trace yes; Monitor yes; performer derived; speech/chat conditional |
| `owner_detected` | owner UUID, display name, distance; on absent-to-present, not per tick | Trace yes; Monitor yes; performer derived; speech/chat conditional |
| `owner_lost` | last distance/reason/grace deadline; once per loss | Trace yes; Monitor yes; performer derived; speech conditional warning; chat no |
| `follow_started` | target UUID, desired distance, deadline; once | Trace yes; Monitor yes; performer derived; speech/chat conditional confirmation |
| `follow_progress` | rounded distance, path status; max every 5s and only material change | Not separately persisted except sampled trace; Monitor yes; performer/speech/chat no |
| `follow_stopped` | terminal cause, final distance; once | Trace yes; Monitor yes; performer derived; conditional speech/chat |
| `wait_started` | rounded anchor; once | Trace yes; Monitor yes; performer derived; conditional confirmation |
| `come_started` | starting distance, arrival radius, deadline; once | Trace yes; Monitor yes; performer derived; conditional confirmation |
| `path_blocked` | stable obstacle category, retry count; per distinct block/retry, max 1/2s | Trace sampled; Monitor yes; performer derived only after terminal/repeated issue; speech/chat normally no |
| `path_stalled` | duration, retry count, distance trend; once per stall episode | Trace yes; Monitor yes; performer derived; speech conditional if terminal |
| `command_accepted` | command name, deadline; once | Trace yes; Monitor yes; performer/speech/chat usually no |
| `command_rejected` | command name, failure code, safe detail; once | Trace yes; Monitor yes; performer derived; conditional speech/chat |
| `command_completed` | command name, duration, bounded result; once | Trace yes; Monitor yes; performer derived; conditional speech/chat |
| `command_failed` | command name, failure code, retriable; once | Trace yes; Monitor yes; performer derived; conditional speech/chat |
| `command_cancelled` | command name, reason, cancelled_by; once | Trace yes; Monitor yes; performer derived; conditional speech/chat |
| `command_timed_out` | command name, deadline, last progress; once | Trace yes; Monitor yes; performer derived; conditional warning |
| `health_critical` | health, threshold, damage source category; threshold crossing plus 10s cooldown | Trace yes; Monitor yes; performer derived; speech conditional warning; chat normally no |
| `hunger_low` | hunger, threshold; threshold crossing plus 60s cooldown | Trace sampled; Monitor yes; performer derived; speech/chat optional low-priority |
| `threat_detected` | entity category, count, nearest distance; material change plus 10s cooldown | Trace sampled; Monitor yes; performer derived; speech conditional warning; chat no |
| `death` | dimension, rounded position, cause category; once | Trace yes; Monitor yes; performer derived; speech conditional; chat no |
| `respawn` | dimension, rounded position; once | Trace yes; Monitor yes; performer derived; speech/chat conditional |
| `emergency_stop_activated` | source, reason, interrupted command; every activation, idempotently | Trace yes; Monitor yes; performer derived; speech conditional; chat optional confirmation |
| `protocol_error` | stable code, offending type, safe detail; once per invalid message with rate limit | Trace yes; Monitor yes; performer/speech/chat no |

No position, entity, health, or path event is emitted per tick.

## 12. Failure and Rejection Codes

| Code | Meaning | Retriable / intervention |
| --- | --- | --- |
| `FEATURE_DISABLED` | Integration disabled | Owner must enable/configure |
| `SIDECAR_UNAVAILABLE` | No healthy control connection | Retriable after sidecar recovery |
| `PROTOCOL_MISMATCH` | No shared protocol version | Deployment/version intervention |
| `MINECRAFT_NOT_CONNECTED` | Command requires server connection | Retriable after `join` |
| `OWNER_NOT_CONFIGURED` | No usable owner identity | Owner configuration required |
| `OWNER_NOT_PRESENT` | Owner absent from observed players | Retriable when owner returns |
| `UNAUTHORIZED_REQUESTER` | Requester is not configured owner/operator | Not automatically retried |
| `INVALID_COMMAND` | Unknown command or malformed arguments | Caller correction required |
| `INVALID_STATE` | Command is unsafe in current state | Retriable after state changes |
| `COMMAND_ALREADY_ACTIVE` | Single movement slot occupied | Retry after cancellation/completion |
| `DESTINATION_UNAVAILABLE` | Target lacks a safe reachable position | Conditionally retriable |
| `PATH_NOT_FOUND` | Pathfinder returned no path | Conditionally retriable within capped attempts |
| `PATH_STALLED` | Movement made no bounded progress | Conditionally retriable; owner may need terrain change |
| `OWNER_TOO_FAR_AWAY` | Owner exceeds policy distance | Owner must return closer |
| `DEADLINE_EXCEEDED` | Command deadline expired | Retry only as a new command |
| `SAFETY_POLICY_BLOCKED` | Requested behavior violates policy | Owner/config/code change required; no auto-retry |
| `UNSUPPORTED_DIMENSION` | Bot or owner is outside allowed dimensions | Owner intervention; sidecar stops/leaves |
| `SIDECAR_DISCONNECTED` | Control connection lost | Retry only after new handshake |
| `MINECRAFT_SERVER_DISCONNECTED` | Server session ended | Retry after server availability |
| `BOT_DEAD` | Bot cannot execute movement | Retry only after respawn |
| `EMERGENCY_STOP_ACTIVE` | Emergency latch blocks movement | Leave/rejoin or safe restart required |
| `INTERNAL_SIDECAR_ERROR` | Unexpected sidecar exception | Limited reconnect/retry; inspect logs if repeated |
| `INTERNAL_GAMMA_ERROR` | Coordinator/policy failure | No blind retry; inspect Gamma trace |

Every failure includes `retriable: bool`, but retry policy remains in Gamma. The sidecar must never autonomously retry a movement command after its terminal result.

## 13. Configuration Plan

Add a nested `[minecraft]` section to tracked `config/app.example.toml`:

```toml
[minecraft]
enabled = false
control_websocket_url = "ws://127.0.0.1:8000/v1/minecraft/control"
server_host = "127.0.0.1"
server_port = 25565
version = "1.21.11"
account_mode = "offline"
bot_username = "Shana"
profiles_folder = ""
owner_uuid = ""
owner_username = ""
default_state = "IDLE"
follow_distance = 3.0
lost_owner_distance = 32.0
owner_loss_grace_seconds = 10
movement_radius = 64.0
command_timeout_seconds = 60
follow_timeout_seconds = 900
path_stall_seconds = 5
path_retry_limit = 3
flee_health_threshold = 6
threat_radius = 8.0
allowed_dimensions = ["minecraft:overworld"]
chat_output_enabled = false
```

Recommended environment names use existing `SHANA_` conventions:

- `SHANA_MINECRAFT_ENABLED`
- `SHANA_MINECRAFT_CONTROL_WEBSOCKET_URL`
- `SHANA_MINECRAFT_SERVER_HOST`
- `SHANA_MINECRAFT_SERVER_PORT`
- `SHANA_MINECRAFT_VERSION`
- `SHANA_MINECRAFT_ACCOUNT_MODE`
- `SHANA_MINECRAFT_BOT_USERNAME`
- `SHANA_MINECRAFT_PROFILES_FOLDER`
- `SHANA_MINECRAFT_OWNER_UUID`
- `SHANA_MINECRAFT_OWNER_USERNAME`
- `SHANA_MINECRAFT_CONTROL_TOKEN`

`MinecraftConfig.from_app_config()` should validate:

- Loopback control URL for MVP.
- Exact supported protocol version.
- Online mode requires owner UUID.
- Offline mode requires owner username and emits a status warning.
- Default state must be `IDLE`.
- Overworld must be the only allowed dimension for MVP.
- Distances, retry counts, and deadlines must be clamped.
- Feature cannot become operational without a control token.

Do not add auto-eat settings until `eat` enters scope.

## 14. Authentication and Secret Handling

- Microsoft credentials and cached tokens remain entirely sidecar-local.
- Mineflayer's `profilesFolder` must point into an ignored local path, recommended `data/minecraft/auth/`.
- Do not put Microsoft email, device codes, refresh tokens, access tokens, or cached profiles in tracked TOML.
- Do not return the profiles path or token contents through status APIs.
- Online-mode setup should use Mineflayer's Microsoft authentication cache; Mineflayer documents that tokens are cached in the specified profile folder.
- Offline mode is only for the private resettable development server.
- Sidecar WebSocket authentication uses `Authorization: Bearer <dedicated-control-token>`, compared with `secrets.compare_digest`.
- Reject non-loopback control peers in MVP even with a valid token.
- Add `control_token`, Microsoft fields, profile payloads, and device-code fields to explicit redaction tests.
- Status exposes only booleans such as `control_auth_configured` and `minecraft_auth_mode`.

## 15. Owner Authorization and Prompt-Injection Safety

Authorization hierarchy:

1. Online-mode Minecraft UUID exact match.
2. Offline development username exact normalized match, with a visible degraded-auth warning.
3. Dashboard direct controls require an authenticated Dashboard session and Shana's internal authenticated API path.
4. Voice/Monitor intent must carry server-created owner provenance, not browser-supplied roles alone.
5. Unknown Minecraft players may converse only if normal stream policy permits; they cannot invoke companion commands.

Minecraft text is always untrusted:

- Never concatenate chat, signs, books, server MOTD, scoreboard text, item names, advancements, or kick text into system prompts.
- Strict owner chat commands should be parsed by a deterministic prefix grammar such as `Shana, follow`, not by sending raw chat to an LLM for action selection.
- The parser returns a small enum and bounded arguments.
- Raw text cannot set owner UUID, enable commands, change dimensions, relax policy, or select methods.
- The LLM never receives raw Mineflayer objects or method names.
- No arbitrary JavaScript or code generation.
- Any user-facing paraphrase receives a bounded fact object, not raw in-world instructions.
- Never infer owner status from display name when an online UUID is available.
- Never authorize using roles supplied directly by an unauthenticated `/v1/stream/events` caller.

One security item must be locked before Phase 4: how authenticated owner provenance is cryptographically or transport-authentically carried from Dashboard live/Monitor input into Shana. The current `StreamActor.roles` field is data, not sufficient proof.

## 16. Dashboard and Monitor Integration

The smallest MVP surface is one compact “Minecraft Companion” panel on the existing `/dashboard/stream` page, not a new page. The Stream page already hosts integration workers, traces, replay, and operator stops.

Panel fields:

- Enabled/configured.
- Sidecar process and control connection.
- Minecraft connecting/connected.
- Companion state.
- Owner UUID/display name and present/absent.
- Rounded position and dimension.
- Health and hunger.
- Current command/deadline.
- Last terminal result.
- Last safe failure.
- Buttons: start/stop sidecar process, join, leave, follow, wait, come, stop, emergency stop.

Exact route direction:

```text
Browser
  POST /api/minecraft/commands/{command}
  -> DashboardService.minecraft_command()
  -> ShanaApiClient.post("/v1/minecraft/commands", ...)
  -> MinecraftCoordinator
```

Status:

```text
GET /api/minecraft/status
  -> DashboardService.minecraft_status()
  -> GET /v1/minecraft/status
```

Sidecar process controls remain Dashboard/supervisor-owned:

```text
POST /api/minecraft/sidecar/start
POST /api/minecraft/sidecar/stop
```

Use a new native module `minecraft.mjs`, modeled after `presence.mjs` and `core.mjs`, rather than adding more globals to classic scripts.

Emergency stop must remain available when ordinary status polling fails. It should send directly to the authenticated Dashboard route, which attempts the Shana emergency endpoint and also requests the supervisor's sidecar stop only if Shana cannot confirm control-channel delivery.

## 17. Performer, Voice, Subtitle, and Chat Routing

- Sidecar facts enter `MinecraftCoordinator`, not `PerformerEventBus`.
- Coordinator may normalize selected facts into `StreamInputEvent(kind="game_state")`, already present in `src/gamma/stream/models.py`.
- `StreamBrain.handle_event()` remains the decision/safety/conversation entry point.
- Gamma-generated `AssistantResponse` becomes subtitle, speech, emotion, and motion events through `output_events_from_response()`.
- `StreamOutputDispatcher` sends those to JSONL and the existing performer bus.
- Monitor output targets `dashboard_monitor`; public stream output remains separately gated.
- Minecraft chat is a separate output adapter controlled by `chat_output_enabled`, with short, filtered, rate-limited confirmations only.
- The sidecar never creates dialogue. It receives either a bounded `send_chat` output command generated by Gamma or no chat output.
- Emergency, death, and owner-loss facts may trigger speech only if Gamma policy selects it and an eligible output listener exists.
- Routine follow progress never generates voice or subtitles.

## 18. Storage and Memory Plan

### Ephemeral in memory

- Current control connection.
- Current state snapshot.
- Owner entity reference and distance.
- Active command, ack, progress, cancellation token, deadline.
- Duplicate-command result cache.
- Wait anchor and path progress.
- Health/hunger/threat debounce state.

### Integration-specific structured storage

Under `data/runtime/minecraft/`:

- `state.json`: bounded redacted last-known status for operator diagnostics only.
- `traces.current.jsonl`: command/event/result correlation, rotated like `StreamTraceStore`.
- Supervisor stdout/stderr logs.
- `auth/`: ignored Microsoft profile cache.

Movement state is never restored from `state.json`.

### Conversation history

Only actual owner/Shana conversational turns and concise user-facing outcomes. Do not journal every movement event.

### General long-term memory

Only selectively approved durable facts, such as a named private server preference or owner-requested companion preference. Routine positions, hunger, deaths, paths, threats, and chat do not enter profile or episodic memory automatically.

### Trace/replay data

Record:

- Requester authorization class.
- Command enum and bounded arguments.
- Command/trace/correlation IDs.
- State before and after.
- Ack, sampled progress, terminal result, failure code, duration.
- No raw books/signs/chat or secrets.

No new SQLModel table is justified for MVP.

## 19. Testing Strategy

### Python unit/contract tests

- Parse every fixture into Pydantic models.
- Reject unknown versions, message types, extra dangerous arguments, missing deadlines, stale connection IDs, and oversized payloads.
- Coordinator handshake, heartbeat, liveness timeout, reconnect, duplicate command, stale command, cancellation, and terminal-result tests.
- Authorization tests for UUID, offline username fallback, unknown player, forged roles, and disabled feature.
- Emergency-stop priority and terminalization tests.
- Gamma-disconnect and sidecar-disconnect tests.
- Restart reconstructs `IDLE` without replay.
- Trace redaction and correlation tests.
- Shana route tests and error-code mapping.
- Dashboard proxy/auth/boundary tests.

### Fake sidecar

Implement an async test double with scripted behavior:

- Handshake and heartbeat.
- Ack then completion.
- Ack then progress then timeout.
- Reject.
- Disconnect before ack.
- Disconnect after ack.
- Duplicate command replay.
- Invalid message and wrong protocol.
- Delayed emergency-stop acknowledgment.

It should not import Mineflayer or require Node.

### TypeScript tests

Use Node's built-in test runner where practical:

- Runtime validation against all shared fixtures.
- State transitions.
- Disabled digging/building/container/PvP policies.
- Immediate control clearing.
- Command deadline and cancellation.
- Owner UUID authorization.
- Duplicate/stale command behavior.
- Minecraft/control disconnect behavior.

### Cross-language contract

Both Python and TypeScript test suites load the same `tests/fixtures/minecraft_protocol/v1/*.json`. Each side validates accepted fixtures and a shared set of invalid fixtures. Do not generate code in MVP.

### Existing patterns to reuse

- Async queue tests using `anyio.run`, as in `tests/test_stream_output.py`.
- WebSocket `TestClient` tests in `tests/test_dashboard_routes.py`.
- Worker-state and reconnect tests in `tests/test_twitch_integration.py`.
- Lifecycle tests modeled after `tests/test_proactive_scheduler.py`.
- Temporary paths and patched settings rather than real local configuration.
- `tests/integration` remains opt-in.

## 20. Real-Server Validation Strategy

Use a private, resettable Java 1.21.11 server with:

- Overworld-only world border around a small test area.
- Offline mode for early deterministic tests.
- Whitelist enabled.
- No valuable world state.
- Terrain fixtures: flat ground, small slopes, doors, water edge, safe drop, blocked corridor, unloaded edge.
- Separate non-owner player account for authorization testing.
- Server logs retained for proof of no attack/break/place/container actions.

Manual sequence:

1. Start sidecar independently.
2. Confirm it connects to Gamma but does not join automatically.
3. Issue `join`.
4. Verify owner UUID/name detection.
5. Follow on ordinary terrain for five minutes.
6. Wait and prove zero continued movement.
7. Move owner 10–20 blocks and issue come.
8. Look at owner.
9. Report bounded status.
10. Stop during follow.
11. Emergency-stop during active pathfinding.
12. Disconnect Gamma/control socket during follow.
13. Restart sidecar and prove no movement resumes.
14. Attempt commands from unauthorized player.
15. Put injection text in chat/sign/book/item name and prove no policy change.
16. Attempt Nether portal proximity and verify refusal/leave.
17. Kill and respawn bot; verify `IDLE`.
18. Restart server during a command.
19. Run a 30–60 minute bounded follow/wait/come soak with event-rate and terminal-outcome assertions.

Real-server tests must be separately selected, for example:

```bash
.venv/bin/python -m pytest tests/integration/test_minecraft_fixture_server.py -q
npm --prefix sidecars/minecraft run test:integration
```

## 21. Phased Commit Plan

These are proposed commits only; none were created during the inspection.

| Commit | Purpose, files, symbols, tests, validation, stopping condition |
| --- | --- |
| 1. `spec: define Minecraft companion v1 contract` | Create `specs/minecraft_companion.md`; edit `specs/README.md`, `specs/roadmap.md`. Document ownership, protocol, state machine, safety, and acceptance criteria. Validation: spec-consistency tests and `git diff --check`. Stop if any design question changes protocol ownership or connection direction. |
| 2. `feat: add Minecraft protocol models and fixtures` | Create package `src/gamma/integrations/minecraft/`, `protocol.py`, `models.py`, shared fixture JSON, and `tests/test_minecraft_protocol.py`. Main symbols: `ProtocolEnvelope`, `CommandEnvelope`, `CommandAck`, `CommandResult`, `StateSnapshot`, `ProtocolError`. Validation: focused pytest then full suite. Depends on commit 1. |
| 3. `feat: scaffold TypeScript Minecraft sidecar contract` | Create `sidecars/minecraft/package.json`, lockfile, `tsconfig.json`, `src/protocol.ts`, and protocol tests. Exact pins only for TypeScript/test tooling initially. Validation: `npm test`, `npm run typecheck`, shared-fixture agreement. Stop before Mineflayer connection. |
| 4. `feat: add fake sidecar and coordinator lifecycle` | Add `coordinator.py`, config/model helpers, Shana `LazySingleton`, lifespan start/stop, control WebSocket, heartbeat, status, cancellation, emergency stop, fake-sidecar tests. Edit `main.py`, `api/routes.py`, `config.py`, `app.example.toml`. No real Mineflayer. Validation: coordinator/API tests and full suite. |
| 5. `feat: add Minecraft trace and redaction` | Add `trace.py`, observability redaction cases, trace route/status summaries. Tests: correlation, rotation, no raw text/secrets. Stop if any secret appears in API snapshots. |
| 6. `feat: connect Mineflayer sidecar to private server` | Add exact `mineflayer` pin, `control-client.ts`, `mineflayer-runtime.ts`, config/status, join/leave only. Validate 1.21.11 negotiation offline. Tests mock Mineflayer; real join remains opt-in. |
| 7. `feat: enforce companion movement safety policy` | Add `safety.ts` and `companion-state.ts`; configure `Movements.canDig=false`, disable towers/scaffolding/parkour as required, clear goals/control states, forbid dimensions and player attacks. Tests must prove prohibited methods are never called. |
| 8. `feat: implement bounded follow wait come and look` | Add `command-executor.ts`; implement owner lookup, follow, wait, come, look, stop, stall/timeout/radius checks. TypeScript tests cover every terminal result and state transition. Depends on commits 6–7. |
| 9. `feat: add Minecraft authorization and intent routing` | Add `policy.py`, `intent.py`, `schemas/minecraft.py`; integrate strict owner commands and structured Shana command routes. Do not use normal conversation tools. Tests cover voice/Monitor provenance and chat injection. Stop if trusted-owner provenance is not locked. |
| 10. `feat: route Minecraft facts through Gamma output policy` | Normalize selected facts to `game_state`, use `StreamBrain`, output dispatcher, and existing performer targets. Add trace/performer/safety tests. No per-tick events. |
| 11. `feat: add supervised Minecraft sidecar process` | Extend `ProcessManager`/CLI with a narrow Node command service; add PID/log/status tests and `.gitignore` entry. Do not auto-start with Shana. Validate scoped start/status/stop manually only when authorized. |
| 12. `feat: add minimal Minecraft operator controls` | Add Dashboard proxy/status routes, `minecraft.mjs`, compact Stream-page panel, CSS, and route/boundary/auth tests. Validate `node --check`, dashboard/API tests, full pytest. |
| 13. `docs: add Minecraft companion smoke and soak procedures` | Update README/current implementations only after behavior exists; document offline/online setup without secrets and opt-in real-server tests. Run full Python/TypeScript suites and manual checklist. |

Each commit should remain independently reviewable and leave the feature disabled unless explicitly configured.

## 22. Acceptance Criteria

The first end-to-end milestone is accepted only when all are demonstrated:

1. `[minecraft].enabled` defaults to false.
2. Existing Gamma behavior and test results are unchanged while disabled.
3. The sidecar starts independently of Shana and Dashboard.
4. Gamma detects sidecar health and heartbeat loss.
5. Sidecar joins a private local Java 1.21.11 server.
6. Gamma receives a correlated Minecraft-connected event.
7. Configured owner is identified, preferring UUID online.
8. `follow_owner` works on ordinary terrain.
9. Following distance stays within the configured bounded range.
10. `wait_here` clears pathfinding and stops movement.
11. `come_here` reaches a nearby owner within deadline/radius.
12. `look_at_owner` rotates without moving.
13. `report_status` returns bounded structured state without secrets/raw text.
14. `stop` cancels ordinary movement.
15. `emergency_stop` clears goals and controls immediately.
16. Gamma shutdown/disconnection stops movement.
17. Control WebSocket loss stops movement.
18. Restart never resumes a prior command.
19. Unauthorized Minecraft players cannot issue commands.
20. Chat/sign/book/item/server text cannot change authorization or policy.
21. No player attack occurs.
22. No block is broken or placed.
23. No container is opened.
24. No dimension except Overworld is entered.
25. Every accepted command reaches exactly one terminal outcome.
26. Deadlines, retry caps, radius limits, and owner-loss limits are enforced.
27. Command/result traces share command and trace IDs.
28. Monitor receives useful throttled status without per-tick spam.
29. All dialogue is generated by Gamma, never the sidecar.
30. Existing Python suite passes.
31. New Python suite passes.
32. New TypeScript suite passes.
33. Python and TypeScript accept/reject the same shared fixtures.
34. Documented manual smoke test passes.
35. Bounded follow/wait/come soak passes without stale movement or event flooding.

## 23. Risks and Mitigations

| Risk | Likelihood / impact | Mitigation | MVP blocker? |
| --- | --- | --- | --- |
| Mineflayer 1.21.11 compatibility | Medium / high | Exact version pin; join/spawn/movement smoke before feature work. Current Mineflayer explicitly lists 1.21.11 support. | Blocks if join/spawn fails |
| Pathfinder compatibility | Medium / high | Exact pin, mocked API contract, real terrain smoke; explicitly disable its digging/building defaults. | Blocks movement phase |
| Node supervision | Medium / high | Narrow managed-command abstraction, PID verification, scoped termination, logs, no auto-start. | Blocks operator lifecycle |
| Microsoft authentication | High / high | Begin offline; add online mode only with dedicated account and ignored profile cache. | Online mode can follow MVP |
| Token storage | Medium / critical | Local ignored path; never API/log/display token contents. | Blocks online mode if unsafe |
| Offline vs online UUIDs | High / high | UUID mandatory online; username fallback only in explicitly offline development mode. | Must be locked |
| Local WebSocket trust | Medium / high | Loopback URL, loopback peer check, dedicated token, protocol handshake. | Must be locked |
| Prompt injection | High / critical | Deterministic command grammar; raw world text never becomes policy/system text. | Must pass tests |
| Owner authorization | Medium / critical | Exact UUID and authenticated operator provenance; fail closed. | Must be locked |
| Gamma/sidecar desynchronization | Medium / high | Connection IDs, monotonic sequences, snapshots, one active command, terminal cache. | No |
| Duplicate commands | Medium / high | Stable command IDs and idempotent cached results. | No |
| Stale movement | Medium / critical | Deadlines, connection-session binding, clear-on-disconnect/restart. | Must pass |
| Restart safety | Medium / critical | No state restoration; `IDLE`; no replay. | Must pass |
| Event flooding | High / medium | Material-change emission, cooldowns, max cadence, bounded queues. | No |
| LLM latency | High / medium | LLM never participates in immediate movement/reflex loop. | No |
| Pathfinder stalls | High / high | Progress delta, five-second stall detection, retry cap, radius/deadline. | No |
| Doors/water/drops/lava | High / high | Conservative movement settings and obstacle fixtures; flee/stop rather than improvise. | Dangerous terrain can be excluded initially |
| Unloaded terrain | Medium / high | Movement radius and loaded-owner requirement; fail destination unavailable. | No |
| Server restarts | Medium / medium | Fail active command, reconnect control only, require explicit rejoin. | No |
| Bot death | Medium / high | `DEAD`, cancel movement, respawn to `IDLE`. | No |
| Dashboard bypass | Medium / critical | Dashboard only proxies Shana; test forbidden imports/direct sidecar URLs. | Must pass |
| Secret leakage | Medium / critical | Redaction tests, bounded status DTOs, no raw auth errors. | Must pass |
| Mixed Python/TypeScript maintenance | Medium / medium | Small protocol, shared fixtures, no generation framework, exact ownership. | No |
| Node CI availability | Medium / medium | Declare Node 22 requirement; separate TS job; Python suite remains independent when disabled. | Must resolve before merge |
| Real Minecraft test dependence | High / medium | Default fake tests; opt-in local fixture server and documented smoke/soak. | Manual evidence required for milestone |
| Sidecar dependency drift | Medium / high | Committed lockfile and upgrade-only compatibility commits. | No |
| Pathfinder old release | Medium / high | Wrapper isolates API, exact tests, no direct method exposure to Gamma/LLM. | Blocks if 1.21.11 terrain smoke fails |

## 24. Explicitly Deferred Work

Do not implement during the companion MVP:

- Generic game adapter or game-playing framework.
- Generic agent framework.
- Autonomous curriculum or progression.
- Voyager, Mindcraft, or MCP game-agent integration.
- LLM-generated code or arbitrary JavaScript.
- Vision-based gameplay.
- Prismarine Viewer.
- OBS-specific Minecraft integration.
- Resource gathering, mining, farming, crafting, building, or equipment optimization.
- Block breaking or placing.
- Containers, inventories beyond bounded status summary, or item chasing.
- Boats, minecarts, horses, or Elytra.
- PvP or advanced PvE.
- User-commanded combat, `defend_self`, or attack skills.
- `eat`, `sleep`, user-commanded `flee`, or `return_to_owner`.
- Nether, End, portals, or any dimension travel.
- Automatic death-item recovery.
- Multi-owner permissions.
- Twitch-viewer control.
- Public-server support.
- Plugin marketplace.
- Shared schema code generation.
- New database tables.
- Large dedicated Dashboard page.
- New package manager or monorepo/workspace framework.
- Automatic task resumption after restart.
- Removal or retirement of Discord or VTube Studio.

## 25. Open Questions Requiring Neety's Decision

1. **Owner provenance for voice/Monitor commands:** require global Shana API authentication, or add a dedicated internal action token between Dashboard and Shana? Recommendation: dedicated internal action token plus authenticated Dashboard session.
2. **Online-mode scope:** must Microsoft-authenticated online mode be part of the first accepted milestone, or may the first milestone be accepted on a private offline-mode development server with online mode as the next gate? Recommendation: offline first, online before any non-development use.
3. **Sidecar shutdown behavior:** on graceful Shana shutdown, should the bot leave Minecraft or remain connected but immobile? Recommendation: leave in MVP.
4. **Emergency latch recovery:** require leave/rejoin, or add a separate explicit `clear_emergency_stop` command? Recommendation: leave/rejoin for MVP.
5. **Follow maximum duration:** recommended 15 minutes. If “persistent” requires longer, choose a hard upper bound and require periodic owner-presence renewal rather than unbounded execution.
6. **Minecraft chat output:** keep disabled for the first movement slice, or allow short owner-only confirmations? Recommendation: disabled until output safety and rate limiting are tested.
7. **Sidecar supervisor CLI shape:** add `minecraft` to `gamma.supervisor.cli`, or expose it only through Dashboard initially? Recommendation: add the CLI so lifecycle is independently testable.
8. **Tracked specification name:** recommended `specs/minecraft_companion.md`.

## 26. Recommended First Implementation Prompt

```text
You are working in /home/neety/Documents/gamma-main.

Implement only the first safe Minecraft companion slice: the documented v1
protocol models and shared JSON contract fixtures. Do not implement Mineflayer,
a WebSocket route, a coordinator lifecycle, Dashboard controls, process
supervision, natural-language intent handling, or real movement.

Expected starting state:
- branch: refactor/persistent-shana-core
- HEAD: 8297ee2005cdef589285f315b366d375bf87f4b9
- expected clean worktree
- baseline: 389 passed, 2 skipped, 74 subtests passed

First read:
- AGENTS.md
- README.md
- specs/README.md
- specs/current_implementations.md
- specs/roadmap.md
- specs/architecture.md
- specs/integrations.md
- specs/shana_output_bus.md
- specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md

Stop and report without editing if the branch, HEAD, or worktree differs.

Create:
- specs/minecraft_companion.md
- src/gamma/integrations/minecraft/__init__.py
- src/gamma/integrations/minecraft/protocol.py
- tests/test_minecraft_protocol.py
- tests/fixtures/minecraft_protocol/v1/handshake.json
- tests/fixtures/minecraft_protocol/v1/command-follow-owner.json
- tests/fixtures/minecraft_protocol/v1/command-ack.json
- tests/fixtures/minecraft_protocol/v1/state-snapshot.json
- tests/fixtures/minecraft_protocol/v1/terminal-result.json
- tests/fixtures/minecraft_protocol/v1/error.json

Edit only if needed:
- specs/README.md
- specs/roadmap.md
- tests/test_spec_consistency.py

Requirements:
- Use Pydantic v2 models.
- Define the common version-1 envelope and models for hello, welcome,
  heartbeat, sidecar status, Minecraft status, command, command ack,
  progress, state snapshot, terminal result, protocol error, cancellation,
  emergency stop, and shutdown.
- Define stable terminal outcomes: completed, cancelled, failed, rejected,
  timed_out.
- Include message_id, connection_id, occurred/sent timestamps, command_id,
  trace_id, and deadline fields where appropriate.
- Reject unknown protocol versions, invalid command names, malformed
  terminal outcomes, missing command IDs, and unbounded/unknown command args.
- Model only these commands: join, leave, follow_owner, wait_here, come_here,
  look_at_owner, report_status, stop, emergency_stop.
- Do not expose Mineflayer methods or arbitrary JavaScript.
- Do not add a generic action/game framework.
- Do not add database models.
- Do not add packages or change pyproject.toml.
- Do not edit .env, config/app.toml, config/app.local.toml,
  config/voices.local.toml, or
  specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md.
- Preserve the Shana API/Dashboard ownership boundary.
- Do not perform unrelated cleanup.
- Do not commit or push unless separately requested.

Tests must cover:
- every valid fixture
- protocol version mismatch
- unknown message type
- invalid command
- invalid outcome
- missing correlation/command ID
- stale or malformed deadline representation
- extra dangerous command arguments
- emergency-stop envelope validation

Validation:
1. .venv/bin/python -m pytest tests/test_minecraft_protocol.py -q
2. .venv/bin/python -m pytest tests/test_spec_consistency.py -q
3. .venv/bin/python -m pytest -q
4. git diff --check
5. git status --short

Stopping conditions:
- Stop before creating any WebSocket endpoint or sidecar runtime.
- Stop if the protocol requires a new public port.
- Stop if implementation would require editing the locked deployment spec.
- Stop if tests reveal an existing unrelated failure; report it without
  changing unrelated code.
- Finish with a concise list of files changed, tests run, and results.
```

## 27. Final Git State At End Of Inspection

Final read-only verification before creation of this planning document:

| Check | Final inspection state |
| --- | --- |
| Branch | `refactor/persistent-shana-core` |
| HEAD | `8297ee2005cdef589285f315b366d375bf87f4b9` |
| Subject | `refactor: retire desktop tray subsystem` |
| Upstream | `origin/refactor/persistent-shana-core` |
| Ahead/behind | 17 ahead, 0 behind |
| Worktree | Clean |
| Staged paths | None |
| Unstaged paths | None |
| Untracked paths | None |
| `git diff --check` | Passed |
| Commits created | None |
| Packages installed | None |
| Services started/stopped/restarted | None |
| Configuration changed | None |
| Protected spec changed | No |
| Files modified by inspection | None |

This document was subsequently added at the user's explicit request. No implementation, configuration, service, package, or protected-deployment change is included in it.
