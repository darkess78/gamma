# Minecraft Companion

Status: Current protocol and bounded offline companion contract

Last verified: 2026-07-11

## Purpose And Scope

This specification is the canonical contract for Gamma's Minecraft Java
Edition companion integration. Version 1 is companion-first: Shana can direct
one bounded companion to join, leave, follow its configured owner, wait, come
closer, look at its owner, report status, stop, or enter an emergency stop.

The protocol models, in-process coordinator, disabled-by-default Shana control
WebSocket, independently started TypeScript sidecar, narrow Mineflayer adapter,
offline-development join/leave runtime, and bounded direct-steering executor
exist. Dashboard controls, process supervision, natural-language command
routing, Microsoft authentication, and online owner UUID authorization have
not been implemented.

The companion feature is disabled by default. Private offline-mode server
testing comes before Microsoft authentication or any non-development use.

## Ownership Boundaries

### Shana owns

- intent, authorization, policy, personality, memory, and dialogue decisions
- command, trace, deadline, cancellation, and terminal-result coordination
- the authenticated local control-channel server on Shana's existing application boundary
- decisions about speech, subtitles, Monitor output, or silence

### The TypeScript sidecar owns

- Minecraft protocol and authentication behavior
- the narrow Mineflayer adapter and bounded direct-steering details
- movement and the immediate safety state machine
- authoritative observed Minecraft state
- immediate clearing of direct targets, physics-tick listeners, and controls on
  control-channel loss

The sidecar connects outbound to the Shana-owned WebSocket. It does not
open a third public port. It never receives arbitrary JavaScript, Mineflayer
method names, raw movement primitives, arbitrary coordinates, generic actions,
or LLM-generated code.

### Dashboard owns

- operator session authentication
- future display of Shana-reported status
- future proxying of supported operator requests to Shana
- future process controls through the supervisor

The Dashboard does not construct the Minecraft coordinator, connect directly
to the sidecar, or own companion policy and state. No Dashboard behavior is
part of this protocol slice.

## Protocol Version 1

Every message is a JSON object validated with unknown fields forbidden. The
top-level `type` field and a command payload's `name` field are discriminators.

Common envelope fields:

| Field | Contract |
| --- | --- |
| `protocol` | Always `gamma.minecraft`. |
| `version` | Always integer `1`. No implicit version negotiation after welcome. |
| `type` | One closed message-type value defined below. |
| `message_id` | Unique bounded identifier for this transmission. |
| `connection_id` | Required after welcome; absent from hello; optional on an error raised before a session exists. |
| `sent_at` | Timezone-aware ISO 8601 UTC timestamp. Naive or non-UTC timestamps are invalid. |
| `sequence` | Non-negative, monotonically increasing within the sending side of a control session. |
| `trace_id` | Required for commands, acknowledgments, progress, terminal results, cancellation, emergency stop, and shutdown. |
| `command_id` | Required for commands and all command-related messages. |
| `payload` | A message-specific, bounded object. |

Identifiers are at most 128 characters and contain only letters, digits,
periods, underscores, colons, and hyphens. Safe human-readable reasons are at
most 160 characters. Safe details are at most 256 printable characters.

Closed message types are:

```text
hello
welcome
heartbeat
sidecar_status
minecraft_status
command
command_ack
command_progress
state_snapshot
terminal_result
cancel_command
emergency_stop
shutdown
protocol_error
```

## Connection Handshake

The sidecar begins a control session with `hello`. It reports only protocol
compatibility facts: supported version `1`, a bounded instance identifier,
build/runtime library versions, the `companion_v1` capability, and a
non-moving `DISCONNECTED` or `IDLE` state. A hello has no `connection_id`.

Shana accepts a compatible hello with `welcome`. Welcome selects version 1,
assigns the new `connection_id`, and supplies bounded heartbeat interval,
liveness timeout, message-size, and duplicate-result-cache limits. Minecraft
chat output is false in v1.

No common version produces a `PROTOCOL_MISMATCH` protocol error and channel
closure. Messages from a previous connection never become valid merely
because their identifiers are otherwise well formed.

## Heartbeat And Liveness

`heartbeat` proves that the control task is responsive and reports only the
current companion state and optionally the last received sequence. Welcome's
heartbeat interval and liveness timeout define the session values; the target
defaults are a five-second heartbeat and stale status after fifteen seconds.

`sidecar_status` and `minecraft_status` are separate messages. Sidecar status
reports control-process connection state, companion state, bounded uptime, and
an optional stable failure. Minecraft status reports only Minecraft connection
state, companion state, negotiated version, and current known dimension.

Liveness timeout or unexpected control-channel loss requires the sidecar to
clear the direct movement target, remove its physics-tick listener, and clear
every movement control immediately. Active work is terminalized with
`SIDECAR_DISCONNECTED`. Reconnection never resumes it.

## Commands

A `command` carries a deadline later than `sent_at` and no more than 900
seconds after it. An equal, earlier, or farther deadline is invalid. The
coordinator may impose tighter command-specific and wall-clock staleness limits
before dispatch. Only these command names and arguments exist in v1:

| Name | Bounded arguments |
| --- | --- |
| `join` | Optional `connection_profile_id`, a fixed policy-known identifier of at most 64 characters. No host, port, token, or account data. |
| `leave` | Optional safe `reason`. |
| `follow_owner` | `follow_distance` from 2 through 6 blocks, default 3; renewable `lease_duration_seconds` from 5 through 900, default 300. |
| `wait_here` | Optional safe `reason`. |
| `come_here` | `arrival_distance` from 2 through 4 blocks, default 3. |
| `look_at_owner` | `duration_seconds` from 0.1 through 10, default 2. |
| `report_status` | Fixed `detail_level`: `basic` or `standard`. |
| `stop` | Optional safe `reason`. |
| `emergency_stop` | Optional safe `reason`. |

Follow is a renewable bounded lease, never permanent unbounded movement. The
sidecar does not renew it automatically; renewal requires a new Gamma-owned
command.
Coordinates, entity lists, JavaScript, server commands, method names, and
generic action payloads are invalid command arguments.

## Acknowledgment, Progress, And Terminal Results

Every well-formed command receives one `command_ack`. An acknowledgment is
either accepted without a failure or rejected with one stable failure object.
Acceptance means the sidecar owns execution; it does not mean completion.

An accepted command may emit throttled `command_progress` messages. Progress
is optional and limited to the closed phases `started`, `moving`, `waiting`,
and `retrying`, bounded elapsed time, and optional safe detail. It is not
per-tick telemetry.

Every accepted command must produce exactly one `terminal_result`, correlated
with the same `connection_id`, `trace_id`, and `command_id`. The only terminal
outcomes are:

```text
completed
cancelled
failed
rejected
timed_out
```

`failed`, `rejected`, and `timed_out` require a stable failure object.
`completed` and `cancelled` must not contain one. The session state machine
must ignore or flag any second terminal result for the same accepted
command rather than publishing it as another outcome.

## Cancellation

`cancel_command` identifies the target through its envelope `command_id` and
may include a bounded reason. Cancellation is idempotent. If accepted work is
still active, the sidecar clears relevant movement before returning its one
terminal `cancelled` result. Cancellation of already-terminal work returns the
cached correlation result and never re-executes the command.

## Emergency Stop

`emergency_stop` is a distinct highest-priority envelope as well as an allowed
bounded command name. It requires connection, trace, and command correlation
and accepts only an optional safe reason. It must bypass an ordinary command
queue, synchronously latch locally, clear the direct target, remove the
movement listener, and clear all movement controls before acknowledgment. It
terminalizes interrupted work once and latches the companion in `STOPPED`.

During the MVP, recovery from the latch requires a successfully completed
leave that fully disconnects Minecraft, followed by a fresh successfully
completed join. A Gamma reconnect, control-session replacement, runtime reset,
new hello, ordinary stop, or ordinary command does not clear it. There is no
`clear_emergency_stop` message. Repeating emergency stop is valid and
idempotent; unknown fields or action data remain invalid.

## Shutdown

On graceful Shana shutdown, Shana will send `shutdown` with trace correlation.
The v1 payload requires `leave_minecraft: true` and permits only an optional
safe reason. The sidecar must clear movement first, leave Minecraft,
and close cleanly. An absent shutdown message is treated as control-channel
loss and therefore still clears movement immediately.

## Reconnection, Duplicates, And Stale Commands

Reconnection starts a new hello/welcome handshake and a new `connection_id`.
It sends a fresh snapshot and does not replay or restore any command.

Within one connection, a duplicate `command_id` must not execute again. The
sidecar returns its cached acknowledgment and, when available, cached
terminal result. The cache is bounded by welcome's capacity and TTL.

A command is stale when its deadline is not later than its send time, its
deadline has passed before acceptance, its connection does not match the
active session, or it belongs to a previous control session. Stale commands
are rejected without movement. A retry is a new command with a new
`command_id`; a duplicate is not a retry.

## State Snapshots

`state_snapshot` is emitted after handshake and on material state change, with
a low bounded cadence while moving. It may be correlated to a command; when a
`command_id` is present, `trace_id` is also required.

The snapshot may contain only:

- sidecar and Minecraft connection states
- companion state
- owner presence
- bounded owner display name and optional configured UUID; the current offline
  username-only runtime does not populate online UUID authorization
- fixed known dimension
- integer-rounded position
- health from 0 through 20 and hunger from 0 through 20
- paired active command identifier and name
- last terminal outcome
- last stable failure code

It must not contain authentication tokens, Microsoft cache data, a control
token, raw chat, signs, books, server MOTD, item names, arbitrary entities,
Mineflayer objects, or per-tick telemetry.

## Companion States

| State | Meaning |
| --- | --- |
| `DISCONNECTED` | No active Minecraft session; no movement. |
| `IDLE` | Connected and alive without an active movement target. |
| `FOLLOWING` | Executing a bounded renewable owner-follow lease. |
| `WAITING` | Holding a safe position with movement cleared. |
| `RETURNING` | Executing bounded `come_here` movement. |
| `DEAD` | Bot death observed; movement impossible and prior work terminal. |
| `STOPPED` | Emergency-stop latch active; movement commands rejected. |

These seven states are the complete implemented sidecar operational state set;
there is no eating, defending, or user-commanded fleeing state. A runtime or
control-session reset
never restores movement and does not clear an emergency latch. Direct targets,
active commands, wait anchors, and command restoration are prohibited after a
restart. Death enters `DEAD`; respawn returns to non-moving `IDLE` and never
resumes pre-death work.

## Implemented Direct-Steering Safety Envelope

No pathfinder dependency is installed, and the sidecar does not implement a
general-purpose pathfinder. The companion executor receives only a narrow
movement adapter, never the raw Mineflayer bot or `bot.entity`. Position values
exposed by the adapter are immutable copies. Direct movement is driven only by
Mineflayer physics ticks; listener registration has explicit idempotent cleanup
and there is at most one active movement listener.

The only permitted movement operations are looking toward the observed owner,
walking forward, stopping, and clearing all controls. Before each forward
activation, the adapter conservatively checks a short candidate step. The bot
and owner must be in the Overworld, the candidate must be finite and loaded,
feet and head space must be passable, and support below must be solid at the
same walkable level. The step must not require jumping or stepping into a drop.
Water, lava, fire, soul fire, cactus, campfires, magma blocks, powder snow,
portals, end gateways, sweet berry bushes, cobwebs, unknown blocks, and any
other terrain that cannot be confidently classified are unsafe.

This first movement implementation therefore supports clear, flat, loaded,
direct Overworld terrain only. An obstacle, unsupported drop, liquid, hazard,
portal, unloaded or unknown terrain, or dimension mismatch clears controls and
causes a bounded retry or stable terminal failure. The companion never jumps,
sprints, navigates around an obstacle, digs, places blocks, activates blocks,
opens doors or containers, equips items, attacks, chats, intentionally swims,
enters portals, teleports, assigns entity position or velocity, or changes
dimensions.

`follow_owner` re-observes the owner on every movement tick. A missing owner
clears controls and the current target on the first missing-owner tick and
keeps the companion stationary during the ten-second grace period. A returning
owner is revalidated before movement resumes. `wait_here` and `stop` preempt
ordinary movement by clearing controls before removing the listener and
terminalizing the interrupted command exactly once. `come_here` applies the
same direct-step checks and is limited to an owner initially within 32 blocks.
`look_at_owner` never enables movement controls.

No real-server movement smoke test has passed. Automated end-to-end coverage
uses a fake Gamma WebSocket server, the real control/runtime/dispatcher and
executor layers, and a fake Minecraft adapter.

## Manual Local Real-Server Smoke Harness

The repository includes an explicitly opted-in interactive smoke harness for
the real Mineflayer boundary. It creates a temporary fake Gamma WebSocket
controller on an ephemeral literal-loopback port and drives the real control
client, sidecar runtime, command dispatcher, companion executor, and
Mineflayer adapter. It does not depend on active Shana, does not connect to
ports 8000 or 8001, and is never invoked by default tests, normal sidecar
startup, or package installation.

The harness may run only when
`SHANA_MINECRAFT_RUN_LOCAL_SMOKE=1` is explicitly present and all of
`SHANA_MINECRAFT_SERVER_HOST`, `SHANA_MINECRAFT_SERVER_PORT`,
`SHANA_MINECRAFT_VERSION`, `SHANA_MINECRAFT_ACCOUNT_MODE`,
`SHANA_MINECRAFT_BOT_USERNAME`, and `SHANA_MINECRAFT_OWNER_USERNAME` are
present and valid. The Minecraft endpoint must be an already-listening literal
loopback address, the version must be exactly `1.21.11`, account mode must be
exactly `offline`, and the bot and owner must be distinct valid Minecraft
usernames. `localhost`, URLs, query strings, remote addresses, Microsoft
credentials, and user-supplied control tokens are not read or used. The
temporary control token is random, remains in memory, and is neither printed
nor written.

An open-port check does not claim or infer a server version and performs no
world operation. Before Mineflayer is created or connected, one explicit
terminal confirmation must cover the private disposable server, intentional
Java 1.21.11 offline configuration, logged-in human owner, shared Overworld,
safe flat clear loaded hazard-free terrain, and consent to bounded forward
walking. Server download, startup, configuration, file modification, and EULA
acceptance are outside the harness. The harness is unsuitable for public,
LAN, remote, or online-mode servers.

The guided commands are join, bounded follow, wait, bounded come, look,
follow-plus-stop preemption, follow-plus-emergency-stop preemption and latch
rejection, leave, fresh join for emergency recovery, final leave, and Gamma
shutdown. It verifies controller-visible connection state, observed forward
movement, arrival distance, stationarity after movement stops, and no
displacement during the look. Each movement-producing segment requires Enter
at a safe checkpoint; `abort`, unexpected input, SIGINT, or SIGTERM invokes
emergency cleanup. Follow and come are each capped at 20 seconds, the entire
smoke is capped at five minutes, and there is no automatic whole-smoke retry or
reconnect. The harness does not relax the clear, flat, loaded, direct Overworld
envelope or authorize jumping, sprinting, chat, combat, inventory or block
interaction, portals, or dimension travel.

The in-memory evidence summary is bounded to command acknowledgments and
terminal outcomes, stable failure codes, maximum observed owner distance,
emergency-stop confirmation, clean leave, and clean shutdown, plus non-secret
run metadata. It excludes the control token, raw server content, chat, MOTD,
entity and socket objects, continuous coordinates, and stack traces, and it
writes no log by default. The harness's existence does not establish runtime
success; no real-server companion movement smoke has passed yet.

## Stable Failure And Rejection Codes

Every failure object contains a stable code, `retriable` boolean, and optional
bounded `safe_detail`. Version 1 defines:

```text
FEATURE_DISABLED
SIDECAR_UNAVAILABLE
PROTOCOL_MISMATCH
MINECRAFT_NOT_CONNECTED
OWNER_NOT_CONFIGURED
OWNER_NOT_PRESENT
UNAUTHORIZED_REQUESTER
INVALID_COMMAND
INVALID_STATE
COMMAND_ALREADY_ACTIVE
DESTINATION_UNAVAILABLE
PATH_NOT_FOUND
PATH_STALLED
OWNER_TOO_FAR_AWAY
DEADLINE_EXCEEDED
SAFETY_POLICY_BLOCKED
UNSUPPORTED_DIMENSION
SIDECAR_DISCONNECTED
MINECRAFT_SERVER_DISCONNECTED
BOT_DEAD
EMERGENCY_STOP_ACTIVE
INTERNAL_SIDECAR_ERROR
INTERNAL_GAMMA_ERROR
```

Retry decisions remain Shana-owned. A retriable flag never authorizes the
sidecar to repeat movement after a terminal result.

`protocol_error` uses the same stable code set plus bounded safe detail,
`retriable`, and an optional bounded offending message-type label. It cannot
carry exceptions, stack traces, secrets, raw server error text, chat, books,
signs, item names, or arbitrary nested details.

## Untrusted In-World Text

All Minecraft text is untrusted, including chat, signs, books, server MOTD,
scoreboards, item names, advancements, and disconnect text. It cannot grant
owner status, change authorization, enable the feature, alter policy, select
methods, or cause a command. Future user-facing language receives bounded
facts selected by Gamma, never raw in-world instructions. Minecraft chat
output is disabled for the initial implementation.

## Offline Owner Authorization

`SHANA_MINECRAFT_OWNER_USERNAME` is required before any owner movement command
is accepted. The configured value and observed player names must each match the
bounded Minecraft username form of 3 through 16 ASCII letters, digits, or
underscores. The single normalization rule is conversion to lowercase, then an
exact comparison; presence remains false unless exactly one currently observed
player matches. No arbitrary player is authorized, and chat or other world text
cannot configure or replace the owner.

This username comparison is an offline-development-only authorization aid. It
does not prove a Microsoft account or provide UUID-strength identity and is
unsuitable for online or public deployment. Microsoft authentication and
online owner UUID authorization are deferred.

## Explicitly Deferred Work

The current implementation does not implement or authorize:

- Microsoft authentication, online owner UUID authorization, process
  supervision, Dashboard UI, natural-language command handling, or Minecraft
  chat command parsing; the sidecar remains independently and explicitly
  started
- generic game/action/agent frameworks or arbitrary code execution
- resource gathering, mining, farming, block breaking or placing, containers,
  inventories, crafting, building, or equipment optimization
- PvP, advanced combat, user-directed combat, or dimension travel
- visual gameplay, Prismarine Viewer, OBS-specific work, or public servers
- automatic task restoration, multi-owner permissions, or viewer control
- movement beyond clear, flat, loaded, direct terrain or a claim of a passed
  real-server movement smoke test

Later runtime slices require their own implementation, safety evidence, and
tests. This document remains the canonical contract for those deferred
behaviors.
