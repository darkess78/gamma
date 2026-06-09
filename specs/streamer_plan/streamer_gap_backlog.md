# Streamer Gap Backlog

## Purpose
This spec bridges:
- the current Gamma repo
- the early-stage target in `specs/streamer_plan/streamer_roadmap.md`
- the mature target in `specs/streamer_plan/streamer_roadmap_current.md`

The goal is to turn the high-level architecture into an actionable backlog.

## Reading Order
- `specs/streamer_plan/streamer_roadmap.md`
- `specs/streamer_plan/streamer_roadmap_current.md`
- `specs/streamer_plan/streamer_gap_backlog.md`

## At a Glance

```text
Current repo:
assistant foundation

Next target:
stream brain

Later target:
stream performer + safe action layer + ops layer
```

## Current -> Early -> Mature

| Capability area | Current repo | Early-stage target | Mature target |
|---|---|---|---|
| Conversation core | Present | Keep | Keep |
| Persona and memory | Present | Keep | Keep |
| Voice turn-taking | Present but rough | Improve latency and interruption | Continuous reliable live speech |
| Event routing | Missing | Add unified event router | Expand to all stream inputs |
| Priority handling | Missing | Add attention ranking | Mature scoring and policy routing |
| Stream outputs | Missing | Add subtitles and simple output events | Full OBS/avatar/overlay control |
| Tool execution | Basic only | Add safe action planning | Add tiered permissions and approvals |
| Moderator controls | Missing | Add operator channel | Add live moderation workflows |
| Game integrations | Missing | None yet or one simple turn-based adapter | Multiple bounded plugin integrations |
| Replay/evaluation | Missing | Add event logging | Add regression and replay harness |

## Workstreams

### 1. Stream Brain
This is the highest-value missing layer.

What exists now:
- `gamma/conversation/service.py`
- `gamma/persona/loader.py`
- `gamma/memory/service.py`

What is missing:
- one schema for all incoming events
- routing and ranking across input sources
- turn policy separate from raw reply generation

Target outcome:
- Gamma decides what to respond to, not just how to respond

Recommended backlog:
- define a normalized event model
- define event sources:
  - mic transcript
  - owner command
  - chat message
  - moderator action
  - donation / redeem
  - game state update
- add priority scoring
- add turn decision outcomes:
  - reply
  - acknowledge
  - ignore
  - defer
  - tool action
  - moderation escalation

### 2. Voice Performance
This is the most visible quality surface.

What exists now:
- STT/TTS adapters
- browser voice roundtrip
- live voice job transport
- partial interruption groundwork

What is missing:
- truly low first-audio latency
- stronger incremental response path
- cleaner coordination between response generation and playback

Target outcome:
- fast, interruptible, stream-usable spoken turns

Recommended backlog:
- tighten timing instrumentation
- default to faster live response path where appropriate
- reduce one-shot full-reply waits
- improve fallback behavior when TTS is slow

### 3. Stream Output Layer
This is where the system starts feeling like a performer.

What exists now:
- almost nothing beyond voice playback and dashboard display
- `gamma/avatar_events/` currently looks like a model shell, not a runtime

What is missing:
- subtitle event output
- expression state output
- speaking-state output
- OBS output/control

Target outcome:
- reply generation produces both speech and presentational events

Recommended backlog:
- define output event schema
- emit subtitle lines and speaking-state events
- add avatar expression mapping from reply emotion and turn state
- add OBS adapter for text/overlay/scene control

### 4. Action Safety Layer
This is the minimum needed before adding real stream actions or game control.

What exists now:
- read-only/basic tools
- speech filtering
- persona boundary rules

What is missing:
- explicit risk tiers
- approval workflow for medium/high-risk actions
- action audit trail

Target outcome:
- Gamma can act safely without turning the core model into an unrestricted executor

Recommended backlog:
- classify tools into read-only, low-risk, medium-risk, high-risk
- separate action planning from action execution
- require approvals for nontrivial actions
- log every action attempt and final disposition

### 5. Stream Inputs and Moderation
This is essential before public exposure.

What exists now:
- no real Twitch/EventSub pipeline
- no ranked chat path
- no mod console workflow

What is missing:
- stream chat ingestion
- donation/redeem ingestion
- moderator signal ingestion
- pre-prompt input screening

Target outcome:
- public inputs become structured, screened events rather than raw prompt text

Recommended backlog:
- add ingestion adapters
- add chat summarization/ranking
- add mod priority override channel
- add pre-prompt screening layer

### 6. Tool and Game Integrations
Do this only after the stream brain exists.

What exists now:
- `gamma/integrations/` is effectively empty

What is missing:
- formal integration contracts
- turn-based adapters
- bounded runtime permissions

Target outcome:
- Gamma can interact with external systems safely and predictably

Recommended backlog:
- define integration interface contracts
- start with one turn-based integration
- keep state serialization structured and compact
- do not attempt high-APM control early

### 7. Operations and Evaluation
This is what turns iteration into engineering instead of improvisation.

What exists now:
- some runtime status and timing surfaces
- dashboard status views

What is missing:
- replayable event traces
- eval suite for policy and latency regressions
- canned adversarial test sets

Target outcome:
- model, prompt, and policy changes can be measured before they hit a live audience

Recommended backlog:
- persist normalized event traces
- add deterministic replay runner
- define pass/fail eval cases
- add red-team prompt/event suites

## Prioritized Build Order

### Phase A
- unified event schema
- event router
- priority scoring
- turn policy outcomes

### Phase B
- subtitle and speaking-state events
- expression mapping
- better incremental voice behavior

### Phase C
- tool risk tiers
- action approval flow
- audit logging

### Phase D
- chat and moderator ingestion
- donation/redeem ingestion
- pre-prompt screening

### Phase E
- OBS integration
- avatar bridge
- one bounded turn-based integration

### Phase F
- replay harness
- regression evaluations
- adversarial moderation test suite

## Definition of Done

### Early-stage target is reached when:
- Gamma can rank multiple incoming events
- Gamma can decide whether to reply or ignore
- Gamma can emit stream-facing output events
- Gamma voice replies are fast enough to feel live

### Mature target is reached when:
- Gamma has a stable stream brain
- Gamma has a performer layer
- Gamma has action-risk controls
- Gamma has replay/eval operations
- Gamma can support bounded live integrations safely

## Bottom Line
The next important step is not "make the model smarter."

The next important step is to add the missing control planes:
- event routing
- priority handling
- performer outputs
- action safety
- replay and evaluation
