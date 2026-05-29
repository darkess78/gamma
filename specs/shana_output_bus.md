# Shana Output Bus And Performer Architecture

## Purpose

This spec records the intended long-term shape for Shana's audio, subtitle, avatar, dashboard, Discord, and stream output routing.

The goal is to support a three-PC setup without making the dashboard, Discord, or a specific VTuber program the center of the architecture.

## Target Topology

```text
Gaming PC
  dashboard control center
  optional mic input
  dashboard monitor audio
  game/application activity

Shana PC
  core brain
  STT
  LLM
  TTS
  memory
  identity
  stream brain
  output bus

Stream PC
  OBS
  VTuber runtime
  stream-facing audio
  subtitles/overlays
  performer client
```

The Shana PC owns processing and turn state. The Stream PC owns presentation. The Gaming PC owns operator control and optional owner input.

## Core Principle

Shana speech and presentation should be published as output events.

Dashboard playback, stream playback, Discord voice output, subtitles, and VTuber motion should be subscribers/adapters to those events. They should not own the spoken turn lifecycle.

Current direction:

```text
anything triggers Shana
  -> Shana PC opens/updates a spoken turn
  -> Shana PC generates text/audio/events
  -> output bus publishes events
  -> clients render/play/forward those events
```

Avoid the older mental model:

```text
dashboard asks question
  -> dashboard receives audio
  -> dashboard is the only playback owner
```

## Inputs, Outputs, And Monitors

Keep these concepts separate.

### Conversation Inputs

Inputs are sources that Shana can hear or react to.

Examples:
- dashboard mic
- Discord voice
- Twitch chat
- Twitch EventSub events
- owner dashboard commands
- game events
- stream/system events

### Performer Outputs

Performer outputs are stream-facing presentation outputs.

Examples:
- Shana speech audio
- subtitles
- speaking state
- emotion/expression changes
- avatar motions
- OBS overlays or scene commands later

### Monitor Outputs

Monitor outputs are for the owner/operator to hear or observe Shana.

Examples:
- dashboard monitor page on the Gaming PC
- local headphone monitor
- optional Discord voice output

Monitor output should be independently mutable from stream output.

## Output Bus

The output bus is the boundary between Shana's brain and external presentation clients.

Expected subscribers:
- Stream PC performer client
- dashboard monitor page
- dashboard control center status panels
- optional Discord voice adapter
- logs/replay/evaluation

Initial transport:
- WebSocket from Shana PC to clients

Possible endpoint shape:

```text
ws://SHANA_PC:8000/v1/performer/events
```

The exact endpoint can change, but the direction should stay: clients connect to the Shana PC and receive ordered output events.

## Event Shape

Gamma should emit generic performer events rather than runtime-specific commands.

Example event types:
- `turn_started`
- `turn_state_changed`
- `speech_chunk_ready`
- `speech_started`
- `speech_ended`
- `subtitle_update`
- `subtitle_clear`
- `expression_set`
- `motion_trigger`
- `mouth_level`
- `output_cleared`

Example speech chunk payload:

```json
{
  "type": "speech_chunk_ready",
  "turn_id": "abc123",
  "chunk_index": 1,
  "text": "Give me a second.",
  "audio_url": "http://shana-pc:8000/v1/audio/artifacts/abc123/1.wav",
  "content_type": "audio/wav",
  "interruptible": true,
  "protect_ms": 0,
  "is_final": false
}
```

Output events must not require clients to read local Shana PC file paths.

## Audio Strategy

Current TTS output is WAV-oriented, and the live voice path already handles chunked audio. The future output bus should preserve that capability while making audio network-safe.

Recommended progression:

1. MVP: Shana creates WAV artifacts and publishes fetchable artifact URLs.
2. Next: speech chunks are published as they become ready.
3. Later: lower-latency streaming can be added if WAV artifact fetches are not fast enough.

Avoid using local filesystem paths in performer-facing payloads. Use one of:
- artifact URL
- artifact ID plus fetch endpoint
- audio bytes/chunks over WebSocket if needed

## Stream PC Performer Client

The Stream PC should run the actual VTuber/runtime/OBS presentation layer.

First client target:
- browser page captured by OBS

Likely pages:

```text
/performer
  stream-facing browser client for audio, subtitles, and performer events

/overlay/subtitles
  optional clean subtitle-only OBS source
```

The performer client should:
- connect to the Shana PC output bus
- play Shana audio for OBS capture
- render subtitles or expose a subtitle overlay
- forward generic avatar events to the selected VTuber adapter
- clear subtitles/audio on global stop events

## VTuber Runtime

Initial likely target:
- VTube Studio

Design rule:
- Gamma emits generic performer events.
- A VTube Studio adapter translates those events into VTube Studio API calls.

Do not put VTube Studio-specific logic into `ConversationService`, `StreamBrain`, or the core voice pipeline.

Generic examples:

```text
expression_set: happy
motion_trigger: wave
speaking_started
speaking_ended
mouth_level: 0.64
```

VTube Studio-specific IDs, hotkeys, parameters, and auth should live in an adapter/client layer.

## Dashboard Role

The dashboard should become a control center, not the owner of Shana's final output path.

Expected dashboard responsibilities:
- start/stop services
- show health/status
- configure providers
- test STT/TTS/LLM/voice
- send owner commands
- run live voice tests
- show stream traces/output logs
- provide a monitor/listen-only page

Suggested page split:

```text
/dashboard
  glanceable streaming overview

/dashboard/live
  mic testing and live voice testing

/dashboard/monitor
  listen-only Shana output monitor

/dashboard/status
  service status, process controls, provider health, machine metrics, logs

/dashboard/stream
  combined Stream and Twitch operations

/dashboard/memory
  memory stats, latest memories, known people, safer cleanup controls

/dashboard/settings
  central settings hub

/performer
  Stream PC OBS/browser source

/overlay/subtitles
  optional subtitle-only OBS/browser source
```

Current status: implemented as route-backed dashboard pages over the existing shared dashboard shell. `/dashboard/twitch` redirects to `/dashboard/stream`, and `/monitor` redirects to `/dashboard/monitor`.

Public routing expectation: `https://gamma.neety.me/dashboard/*`, dashboard `/api/*`, and dashboard `/static/*` should be reverse-proxied to the dashboard process. The Shana API process keeps fallback redirects for `/dashboard` and valid `/dashboard/<page>` paths to `settings.dashboard_base_url` so misrouted dashboard page requests do not return JSON 404. Dashboard navbar and overview links are rendered with the configured public dashboard base URL.

The dashboard navbar is the top control surface. It includes compact page links, visually separate output links for Performer/Subtitles, a `Stop Output` control, status chips, a status dropdown, and mobile menu behavior. `Stop Output` clears current speech/subtitles for `dashboard_monitor`, `stream_public`, and `discord_call` without stopping Shana or ingestion workers.

Latest validation for this state: dashboard JavaScript syntax check, dashboard/API route tests (41 passed, 50 subtests), stream output/brain tests, and full pytest suite (`218 passed`).

## Dashboard Monitor

The dashboard monitor should subscribe to Shana output without needing to start a user voice turn first.

It should support:
- hearing all Shana speech that the operator is allowed to monitor
- seeing subtitles
- seeing current turn state
- seeing emotion/expression metadata
- monitor-only mute

This lets the owner hear Shana when speech was triggered by Twitch, Discord, proactive idle behavior, or stream events.

## Discord Role

Discord should be an optional communication adapter, not the main stream output route.

Discord can provide:
- voice input from one or more people
- optional Shana voice output into a call
- speaker identity from Discord user IDs

Discord should not be required for OBS, VTuber output, or the Stream PC performer path.

If Discord is down, Shana should still be able to speak on stream through the output bus and Stream PC performer client.

## Identity Model

Every input platform should map speakers into a shared identity shape.

Suggested shape:

```text
source
platform_id
display_name
roles
```

Examples:

```text
source = "discord"
platform_id = Discord user ID
display_name = Discord display name

source = "twitch"
platform_id = Twitch user ID
display_name = Twitch display name

source = "dashboard"
platform_id = owner/local user identity
display_name = Owner
```

Discord user IDs should tie into Shana's known people and memory/profile systems. Shana should not treat all Discord speakers as the owner or as the same person.

## Safety And Audience Policies

Use lightweight output target policies rather than a large privacy-mode system.

Examples:
- `dashboard_monitor`
- `stream_public`
- `discord_call`

Different targets may need different filtering rules.

Example:
- dashboard monitor can be looser during private testing
- stream output must remain Twitch/TOS-safe
- Discord output should respect Discord/channel policy

Casual swearing may be allowed on some stream setups, while hard rule breaks remain blocked.

Implementation rule:
- do not hardcode one global output safety assumption into the output bus.

## Stop And Mute Semantics

Separate global controls from target-specific controls.

### Global Stop Shana

Should:
- cancel active speech/turn work where possible
- stop current playback everywhere
- clear subtitles everywhere
- prevent stale audio/subtitles from reappearing
- leave ingestion workers running unless separately stopped

### Target Mute

Should affect only one subscriber/output target.

Examples:
- mute dashboard monitor only
- mute stream output only
- mute Discord output only

Mute should not cancel Shana's turn globally unless explicitly requested.

## Shared Spoken Turn State

The existing live voice job state is a useful starting point, but spoken turn state should eventually become a shared backend concept rather than something owned only by the dashboard live voice path.

Status: initial shared state implemented.
- Performer bus publishes into a shared in-memory spoken-turn store.
- Recent spoken turns are exposed through performer status.
- Remaining work: make stream/live/Discord paths use this as the primary turn lifecycle owner instead of only deriving it from performer events.

Expected turn states:

```text
queued
generating
synthesizing
speaking
completed
interrupted
cancelled
failed
```

All output subscribers should observe the same turn state.

Turn state should include:
- `turn_id`
- source/input event metadata
- speaker identity
- generated text
- speech chunks
- audio artifacts
- subtitles
- emotion/expression metadata
- cancellation/interruption state
- timing data

## Non-Goals

Do not:
- make Discord the main transport
- make dashboard playback the final output architecture
- put VTube Studio-specific logic in `ConversationService`
- make Stream PC clients depend on Shana PC local file paths
- feed public chat or Discord users directly into raw prompts without identity and safety routing
- require OBS/VTuber output to run on the Shana PC

## Suggested Implementation Phases

### Phase 1: Output Bus Models And Event Stream

Define backend output bus models and expose a WebSocket event stream from the Shana PC.

Keep the current JSONL stream output logging as a replay/debug adapter.

Status: mostly implemented.
- Generic performer event models exist.
- `WebSocket /v1/performer/events` streams ordered events.
- `GET /v1/performer/events/recent` supports recent replay and `after_sequence`.
- Events get monotonic sequence numbers and replay gap reporting.
- Target policies currently include `stream_public`, `dashboard_monitor`, and `discord_call`.

### Phase 2: Dashboard Monitor

Add a dashboard monitor page that subscribes to the output bus and plays Shana speech without requiring the dashboard live voice session to initiate the turn.

Status: mostly implemented.
- `/dashboard/monitor` subscribes to the output bus as `dashboard_monitor`.
- It plays Shana audio, shows subtitles/state/expression, and displays actor/input context.
- Monitor output can be muted/cleared independently from stream output.
- It requires a user click to enable future audio playback and supports `dashboard`, `compact`, and `focus` CSS/local-storage themes.

### Phase 3: Stream PC Performer Page

Add a browser-based performer page for the Stream PC.

It should play Shana audio, render subtitles, and later become the bridge to VTuber commands.

Status: mostly implemented.
- `/performer` subscribes to `stream_public`.
- It plays network-safe audio payloads, renders subtitles, and shows event state.
- It is still a browser/OBS performer client, not the final VTuber runtime adapter.

### Phase 4: Move Live Voice Playback Onto The Output Bus

Refactor dashboard live voice so generated speech chunks publish to the shared output bus.

The dashboard live page should become one input client plus one optional monitor subscriber, not the exclusive owner of playback.

Status: mostly implemented.
- Dashboard live voice publishes monitor-targeted performer events.
- Live voice chunks include audio, subtitle, expression, actor/input context, and terminal clear/end events.

### Phase 5: VTube Studio Adapter

Add a VTube Studio adapter/client that translates generic performer events into VTube Studio API calls.

Status: adapter/client implemented; stream runner is optional.
- A VTube Studio adapter maps `expression_set` and `motion_trigger` to configured VTube Studio hotkey requests.
- A VTube Studio websocket client can connect to the configured endpoint, authenticate with a saved token, send API requests, and report connection/auth errors.
- An optional runner subscribes to `stream_public` performer events and forwards mapped actions to the client.
- It tracks speaking state from speech start/end/clear events.
- It reports status through performer status.
- Remaining work: complete the operator token approval/persistence workflow and verify the VTube Studio hotkey mappings against the final model.

### Phase 6: Discord Communication Adapter

Add Discord as an optional communication module:
- Discord voice input
- Discord speaker identity mapping
- optional Discord voice output subscriber

Discord output should subscribe to the same output bus as other clients.

Status: normalization and runtime boundary implemented.
- Discord message and voice utterance normalizers create stream input events with `source = "discord"`.
- Discord speaker IDs are passed through the existing identity resolver.
- `discord_call` is reserved as a separate output target.
- A dependency-light Discord runtime owns config/status, tracks normalized inputs, and handles isolated `discord_call` output events when enabled.
- Remaining work: add the real Discord bot/voice transport and connect it to the runtime boundary.

## Bottom Line

The long-term architecture should be:

```text
Shana PC owns brain, turn state, and output events.
Stream PC owns OBS, VTuber runtime, and stream presentation.
Gaming PC owns control, testing, and operator monitoring.
Discord is optional communication, not core routing.
```
