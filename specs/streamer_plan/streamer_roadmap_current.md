# Streamer Roadmap Current-State Target

## Purpose
This spec describes the likely shape of a mature, current-generation AI streamer system in the style of Neuro-sama.

This is not an assertion about the exact private production stack used by Neuro-sama.
It is an inferred target architecture based on:
- the reverse-engineering report in `specs/streamer_plan/Neuro-sama Reverse-Engineering Report.pdf`
- the known needs of low-latency live AI streaming
- the current gaps in Gamma

Use this document as a long-range target to compare against `specs/streamer_plan/streamer_roadmap.md`, which covers the earlier staged build.

## Framing

```text
Early-stage target:
assistant + event router + basic stream outputs

Current-state target:
assistant + stream brain + performer layer + safe action layer + operations layer
```

## Visual Overview

```text
Inputs
  mic
  ranked chat
  donations / redeems / moderator events
  game state / plugin state
  stream state / schedules / goals

    -> event router
    -> priority and policy layer
    -> short structured context builder
    -> core LLM
    -> moderation and action gates
    -> speech + avatar + OBS + tools + games
    -> logs / replay / evaluation / operator controls
```

## Current-State Architecture Shape

| Layer | Mature target behavior | Gamma today |
|---|---|---|
| Multi-source ingestion | Pulls from mic, chat, moderation, donations, game state, and stream state | Missing real Twitch/EventSub ingestion |
| Event router | Normalizes all events into one internal schema | Partial |
| Priority engine | Decides what gets attention now, later, or never | Partial |
| Turn policy | Chooses reply, ignore, acknowledge, act, or defer | Partial |
| Context builder | Produces short structured prompts instead of transcript stuffing | Partial |
| Persona and boundaries | Stable identity, safety posture, relationship handling | Present |
| Core LLM | Main conversational planner and responder | Present |
| Tool/action policy | Tiered permissions with allowlists and approvals | Very early |
| Output moderation | Filters speech and actions before execution | Partial |
| Speech layer | Low-latency STT/TTS with interruption and fallback behavior | Partial |
| Performer layer | Avatar state, expressions, subtitles, lip sync, scene cues | Mostly missing |
| Stream control | OBS overlays, scenes, captions, alerts, status widgets | Missing |
| Game/tool runtime | Turn-based or bounded-control plugins with explicit contracts | Missing |
| Replay/eval/ops | Replayed sessions, regression checks, human override, observability | Partial traces/replay scaffolding |

## Mature Runtime Pattern

```text
                +----------------------+
                |  Human operators     |
                |  mods / owner / dev  |
                +----------+-----------+
                           |
                           v
Inputs -> Event Router -> Priority Engine -> Turn Policy -> Context Builder -> Core LLM
                                                           |                |
                                                           v                v
                                                    Tool / Action Plan   Spoken Reply Draft
                                                           |                |
                                                           +-------+--------+
                                                                   v
                                                         Moderation / Risk Gates
                                                                   |
                                         +-------------------------+--------------------------+
                                         |                         |                          |
                                         v                         v                          v
                                      TTS audio              OBS / overlays             Game / tool actions
                                         |                         |                          |
                                         +-------------> Performer / stream output <----------+
                                                                   |
                                                                   v
                                                           Logging + replay + metrics
```

## What Makes a Mature AI Streamer Different

### 1. It does not just answer prompts
A mature streamer runtime chooses among:
- answer now
- acknowledge briefly
- ignore
- defer
- trigger a stream action
- trigger a safe tool action
- escalate to human moderation

Gamma today is still centered on "user says thing, assistant replies."

### 2. It has a real attention model
The stream loop must decide:
- which input matters most
- whether the event is on-brand
- whether it is safe to answer
- whether it deserves full attention or a short reaction

Without this, the system is only a chatbot connected to a microphone.

### 3. It has a performer layer
The mature target is not just text plus TTS.
It also coordinates:
- speaking state
- expression state
- subtitle timing
- stream overlays
- scene/state transitions

### 4. It has an operations layer
A serious streamer stack needs:
- replayable event logs
- turn traces
- moderation overrides
- kill switches
- regression tests after prompt/model changes

This is the difference between a demo and a sustainable live system.

## Gap View

```text
FOUNDATION
[++++] Persona
[++++] Memory
[++++] Core reply loop
[+++ ] Voice pipeline
[+++ ] Output safety
[++  ] Vision

STREAM BRAIN
[    ] Event router
[    ] Priority engine
[    ] Turn policy
[    ] Ranked chat ingestion
[    ] Donation/redeem ingestion
[    ] Moderator input channel

PERFORMER LAYER
[    ] Avatar driver
[    ] Subtitle timing/events
[    ] OBS control
[    ] Reactive overlays

ACTION LAYER
[+   ] Basic tool hooks
[    ] Tool risk tiers
[    ] Approval workflows
[    ] Game/plugin contracts

OPS LAYER
[    ] Replay harness
[    ] Regression evaluation
[    ] Human override tools
[    ] Safety dashboards for live operations
```

## Long-Range Build Order

### Have Now
- core assistant
- persona and memory
- STT/TTS and live voice transport
- basic safety filtering
- limited tool framework

### Next
- implement `specs/streamer_plan/twitch_stream_module.md`
- strengthen priority engine
- strengthen turn policy layer
- synchronize stream-facing output events

### After That
- OBS and subtitle control
- avatar/expression state bridge
- moderator and event ingestion
- tool risk tiers and approvals

### Mature Target
- replay-driven iteration
- bounded game integrations
- operational dashboards
- stable human override paths

## Design Rules
- Treat the mature target as a systems problem, not a model-only problem.
- Keep prompt context short and structured.
- Separate conversation, action planning, and action execution.
- Require risk classification before any non-read-only action.
- Prefer turn-based or low-APM integrations before real-time control.
- Build operator visibility before public launch scale.

## Bottom Line
The mature Neuro-like target is not "a better chatbot."

It is a layered live system with:
- attention routing
- safety gates
- performer outputs
- controlled actions
- operational tooling

Gamma already has a usable assistant foundation.
What it lacks is the stream brain, performer layer, and operations layer that define a current-generation AI streamer.
