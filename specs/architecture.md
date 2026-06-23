# Gamma Architecture

Status: Current
Last verified: 2026-06-22

## Runtime Topology

Gamma intentionally runs two FastAPI applications:

| Process | Port | Ownership |
| --- | --- | --- |
| Shana API | 8000 | conversation, memory, voice inference, vision, stream decisions, performer state |
| Dashboard | 8001 | authentication, browser clients, supervisor controls, machine status, local configuration |

The protected deployment and proxy contract is
`LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`.

## Dependency Direction

```text
browser client
  -> dashboard API/client
  -> Shana HTTP API
  -> conversation, memory, voice, stream, and performer services
```

The dashboard must not construct Shana-owned services or open Shana-owned
state stores directly. Shared Pydantic schemas and configuration types may be
imported by both processes.

`/dashboard/monitor` is the persistent interaction/output room and submits
local text through the supported dashboard-to-Shana stream API boundary.
`/dashboard/live` remains the microphone and live-voice diagnostic client.
Monitor is expected to remain open in a dedicated tab/window because ordinary
page navigation destroys browser audio and WebSocket state.

## Core Domains

- `conversation`: prompt assembly, response generation, tools, safety, memory
- `llm`: swappable model adapters and deterministic routing
- `memory`: profile facts, episodic memory, known people, identity links
- `voice`: STT, TTS, audio understanding, live jobs, interruption
- `stream`: normalized public inputs, turn policy, safety, replay
- `performer`: ordered output events and presentation adapters
- `integrations`: Twitch, Discord, and future bounded external adapters
- `dashboard`: authenticated operator and interaction clients
- `supervisor`: local process lifecycle

## Preserved Interfaces

- provider adapters remain swappable
- public route contracts remain compatible during internal refactors
- bind addresses and public URLs remain separate
- performer outputs remain generic rather than VTube Studio/OBS-specific
- public actions and speech pass explicit safety and operator-control gates

## Streamer Architecture

The Neuro-inspired extension is a layered system, not an LLM connected
directly to chat:

```text
inputs -> normalization/ranking -> turn policy -> conversation
       -> safety -> speech/actions -> performer outputs -> replay/evaluation
```

New agentic tools and game integrations remain deferred until the core loop,
moderation, replay, and human override are stable.
