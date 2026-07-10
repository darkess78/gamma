# Minecraft Companion

Status: Target contract; protocol models only

Last verified: 2026-07-10

## Purpose And Scope

This specification is the canonical contract for a future Minecraft Java
Edition companion integration. Version 1 is companion-first: Shana may
eventually direct one bounded companion to join, leave, follow its configured
owner, wait, come closer, look at its owner, report status, stop, or enter an
emergency stop.

Only the protocol models and shared contract fixtures exist in the repository
at this stage. This document does not claim that a sidecar, control channel,
Minecraft connection, command coordinator, authorization path, or user
interface has been implemented.

The future feature will be disabled by default. Private offline-mode server
testing comes before Microsoft authentication or any non-development use.

## Ownership Boundaries

### Shana owns

- intent, authorization, policy, personality, memory, and dialogue decisions
- command, trace, deadline, cancellation, and terminal-result coordination
- the future control-channel server on Shana's existing application boundary
- decisions about speech, subtitles, Monitor output, or silence

### The future TypeScript sidecar owns

- Minecraft protocol and authentication behavior
- Mineflayer and pathfinding details
- movement and the immediate safety state machine
- authoritative observed Minecraft state
- immediate clearing of goals and controls on control-channel loss

The sidecar connects outbound to a future Shana-owned WebSocket. It does not
open a third public port. It never receives arbitrary JavaScript, Mineflayer
method names, raw pathfinder goals, arbitrary coordinates, generic actions, or
LLM-generated code.

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
clear the pathfinder goal and every movement control immediately. Active work
is terminalized with `SIDECAR_DISCONNECTED`. Reconnection never resumes it.

## Commands

A `command` carries a deadline later than `sent_at` and no more than 900
seconds after it. An equal, earlier, or farther deadline is invalid. The
eventual coordinator may impose tighter command-specific and wall-clock
staleness limits before dispatch. Only these command names and arguments exist
in v1:

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

Follow is a renewable bounded lease, never permanent unbounded movement.
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
`completed` and `cancelled` must not contain one. The eventual session state
machine must ignore or flag any second terminal result for the same accepted
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
and accepts only an optional safe reason. It must eventually bypass an
ordinary command queue, synchronously clear pathfinding and all movement
controls before acknowledgment, terminalize interrupted work once, and latch
the companion in `STOPPED`.

During the MVP, recovery from the latch requires leave and a fresh join. There
is no `clear_emergency_stop` message. Repeating emergency stop is valid and
idempotent; unknown fields or action data remain invalid.

## Shutdown

On graceful Shana shutdown, Shana will send `shutdown` with trace correlation.
The v1 payload requires `leave_minecraft: true` and permits only an optional
safe reason. The future sidecar must clear movement first, leave Minecraft,
and close cleanly. An absent shutdown message is treated as control-channel
loss and therefore still clears movement immediately.

## Reconnection, Duplicates, And Stale Commands

Reconnection starts a new hello/welcome handshake and a new `connection_id`.
It sends a fresh snapshot and does not replay or restore any command.

Within one connection, a duplicate `command_id` must not execute again. The
future sidecar returns its cached acknowledgment and, when available, cached
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
- bounded owner display name and configured UUID
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
| `IDLE` | Connected and alive without a movement goal. |
| `FOLLOWING` | Executing a bounded renewable owner-follow lease. |
| `WAITING` | Holding a safe position with movement cleared. |
| `RETURNING` | Executing bounded `come_here` movement. |
| `FLEEING` | A short sidecar-owned immediate safety reflex, not a user command. |
| `DEAD` | Bot death observed; movement impossible and prior work terminal. |
| `STOPPED` | Emergency-stop latch active; movement commands rejected. |

On sidecar restart, state begins non-moving as `DISCONNECTED` or `IDLE` based
only on the current Minecraft connection. Movement goals, active commands,
wait anchors, and command restoration are prohibited. Death/respawn also
returns to non-moving `IDLE`; it does not resume pre-death work.

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

## Explicitly Deferred Work

This protocol slice does not implement or authorize:

- a sidecar, Mineflayer, WebSocket route, coordinator, configuration, process
  supervision, Dashboard UI, natural-language command handling, or server
  connectivity
- generic game/action/agent frameworks or arbitrary code execution
- resource gathering, mining, farming, block breaking or placing, containers,
  inventories, crafting, building, or equipment optimization
- PvP, advanced combat, user-directed combat, or dimension travel
- visual gameplay, Prismarine Viewer, OBS-specific work, or public servers
- automatic task restoration, multi-owner permissions, or viewer control

Later runtime slices require their own implementation, safety evidence, and
tests. This document remains a target contract until those slices exist.
