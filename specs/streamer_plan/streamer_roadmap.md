# Streamer Roadmap

## Purpose
This spec translates the high-level architecture pattern from the Neuro-sama reverse-engineering report into a practical roadmap for Gamma.

The goal is not to clone Neuro exactly. The goal is to build the same class of system:
- low-latency conversational core
- strong persona and safety boundaries
- event routing and prioritization
- stream-facing outputs
- modular integrations for tools and games

## Source Material
- Reference report: `specs/streamer_plan/Neuro-sama Reverse-Engineering Report.pdf`

## Visual Overview

```text
Current shape:
assistant with memory + voice + some safety

Target early-stage shape:
assistant + event router + ranking + stream outputs

Later shape:
assistant + stream performer + safe tool/game control
```

```text
Core runtime pattern

Inputs
  -> Event Router
  -> Context Builder
  -> Persona + Policy
  -> Core LLM
  -> Safety Layers
  -> Speech / Actions / Stream Outputs
```

## Repo-to-Architecture Map

| Architecture block | Gamma status | Current repo surface |
|---|---|---|
| Voice input and reply loop | Present | `gamma/voice/`, `gamma/run_live_voice_worker.py` |
| Conversation orchestration | Present | `gamma/conversation/service.py` |
| Persona and relationship layer | Present | `gamma/persona/`, `config/persona.yaml` |
| Runtime memory | Present | `gamma/memory/service.py` |
| Output speech filtering | Present | `gamma/safety/`, `gamma/persona/boundaries.md` |
| Basic tool routing | Partial | `gamma/tools/` |
| Vision as structured context | Partial | `gamma/vision/` |
| Event router across multiple input sources | Partial | `gamma/stream/` has normalized events and a stream brain; Twitch/EventSub ingestion still missing |
| Ranked chat and stream-event ingestion | Missing | No Twitch/EventSub/donation pipeline yet |
| Avatar runtime and expression bridge | Very early | `gamma/avatar_events/` models only |
| OBS / overlay / subtitle control | Partial | Stream output events/logging exist; synced subtitle/overlay adapters still missing |
| Game/plugin bridge | Missing | `gamma/integrations/` is effectively empty |
| Offline replay / eval harness | Partial | Stream traces/replay scaffolding exist; deterministic Twitch replay still missing |

## Stage Map

### Stage 1: Brain
Goal: reliable conversational core with persona, memory, and safety.

Status:
- mostly present

Already here:
- conversation service
- persona prompt builder
- memory persistence and retrieval
- speech safety filter
- basic tool-aware prompting

Success criteria:
- consistent replies under replayed prompts
- safe failure behavior
- timing visibility for each turn

### Stage 2: Speech Loop
Goal: turn-taking voice interaction with interruption support and stable latency.

Status:
- present but still rough

Already here:
- STT and TTS adapters
- browser voice roundtrip
- live voice websocket flow
- chunk interruptibility groundwork

Needs improvement:
- lower first-audio latency
- better incremental speech response
- tighter timing instrumentation

Success criteria:
- fast half-duplex conversation
- interruptible playback
- stable first-audio timing

### Stage 3: Stream Brain
Goal: decide what deserves attention and what action to take.

Status:
- partially present

Must add:
- Twitch/EventSub ingestion for chat, follows, raids, redeems, bits/donations, and subs/resubs
- stronger priority ranking for events
- richer turn decision layer:
  - reply
  - ignore
  - acknowledge briefly
  - defer
  - take a safe tool/stream action

Success criteria:
- the system chooses what to respond to instead of blindly answering everything
- raw stream noise does not enter the prompt directly

### Stage 4: Stream Performer
Goal: make the system feel like a deliberate on-stream presence rather than a backend assistant.

Status:
- mostly missing

Must add:
- subtitle event output
- avatar expression events
- lip-sync / speaking-state bridge
- OBS scene/text/overlay control

Success criteria:
- stream output looks intentional
- response and presentation stay synchronized

### Stage 5: Safe Actions and Games
Goal: add controlled agency after the stream loop is stable.

Status:
- mostly missing

Must add:
- tool risk tiers
- allowlisted stream actions
- human approval path for high-risk actions
- turn-based or low-APM game integrations first

Success criteria:
- safe read-only tools first
- bounded stream-control actions second
- game automation only after policy and observability are solid

## Heat Map

```text
FOUNDATION
[++++] Conversation core
[++++] Persona system
[++++] Memory
[+++ ] Speech pipeline
[+++ ] Output safety
[++  ] Vision

STREAMER LAYER
[+   ] Interruptibility
[+   ] Tool orchestration
[    ] Chat ranking
[    ] Event ingestion
[    ] OBS control
[    ] Avatar runtime
[    ] Game API layer
[    ] Replay / eval harness
```

## Near-Term Roadmap

### Have Now
- assistant conversation loop
- persistent persona and memory
- voice input and output
- live voice transport
- basic safety and tool hooks
- normalized stream event schema
- initial stream brain
- stream output events and trace/replay scaffolding

### Next Milestone
- implement the Twitch module spec in `specs/streamer_plan/twitch_stream_module.md`
- message ranking and fusion for public chat
- synced subtitles and stop-speech controls
- stronger explicit turn-decision policy

### Later Milestone
- avatar runtime
- OBS integration
- donation and moderator event ingestion
- turn-based game/plugin adapters
- replay-based regression testing

## Design Rules
- Do not chase an exact Neuro clone.
- Do not feed raw chat directly into the main prompt.
- Do not add high-risk tools before approval tiers and logging exist.
- Do not add real-time game control before turn-based integrations work well.
- Prefer modular adapters over hardcoded provider-specific orchestration.

## Bottom Line
Gamma already has the beginnings of the brain and part of the speech loop.

The biggest missing layer is not the model. It is orchestration:
- input fusion
- priority handling
- stream output control
- action safety boundaries
