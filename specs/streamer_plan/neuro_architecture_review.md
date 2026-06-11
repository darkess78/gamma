# Neuro-Inspired Architecture Review

## Purpose
This note captures the immediate engineering takeaways from the Neuro-sama research report and compares them to Gamma's current structure.

The goal is not to clone Neuro-sama. The goal is to avoid architectural choices that would force a major rewrite once Gamma grows from an assistant foundation into a live AI performer.

## Current Read
Gamma already has useful foundations:

- swappable LLM, STT, TTS, memory, and tool boundaries
- a conversation service with persona, memory, tools, vision, and safety
- layered speech filtering
- live voice cancellation and chunked reply delivery
- dashboard and runtime status surfaces
- early streamer planning docs

The highest rewrite risk is not model quality. It is allowing realtime routing, action planning, public input handling, output events, and safety policy to accumulate inside the existing conversation path.

## Fix Before It Hardens

### 1. Add a Stream Brain
Gamma needs a normalized event model and a turn policy layer before real Twitch, donation, game, or moderator inputs are connected.

The stream brain should decide whether an incoming event should be handled as:

- reply
- acknowledge
- ignore
- defer
- tool action
- moderation escalation

This keeps public input routing separate from response generation.

### 2. Split Generation From Action Planning
Tool inference and tool execution should not keep growing inside `ConversationService`.

Before adding powerful integrations, define:

- action plan schema
- risk tiers
- approval requirements
- audit log entries
- execution result schema

This lets Gamma act safely without turning the core model into an unrestricted executor.

### 3. Add Stream Output Events
Assistant replies should produce stream-facing events, not only text/audio responses.

Expected output events:

- subtitle line
- speech started
- speech chunk ready
- speech ended
- emotion changed
- avatar motion
- OBS command
- overlay update

This prevents avatar, OBS, subtitle, and dashboard behavior from becoming ad hoc fields on assistant responses.

### 4. Wrap Live Voice Runtime
The current subprocess and JSON-file live voice job path is practical for a prototype, but it should sit behind an interface before it becomes the permanent realtime runtime.

Add a runtime boundary such as `LiveTurnRuntime` so Gamma can later move from file polling to streaming events without rewriting voice clients.

### 5. Add Replay And Evaluation
Before public chat exposure, Gamma needs replayable traces and adversarial tests.

Minimum target:

- persist normalized input events
- persist turn decisions
- persist action attempts and outcomes
- persist safety decisions
- replay traces against current policy/model config
- add red-team cases for prompt injection, harassment bait, unsafe speech, and tool misuse

## Defer For Now
These should wait until the control planes above exist:

- autonomous game control
- broad Twitch/EventSub ingestion
- donation/redeem automation
- custom model training
- complex external plugin ecosystem

## Recommended Build Order

1. normalized event schema
2. stream brain and turn policy
3. output event schema
4. action planning and risk tiers
5. audit logging and replay traces
6. live voice runtime interface
7. Twitch/moderator ingestion
8. OBS/avatar adapters
9. one bounded turn-based integration

## Bottom Line
Gamma does not need a major rewrite right now.

It does need a few control-plane boundaries before new live-streaming features land. The next important step is to add event routing, priority handling, performer outputs, action safety, and replay/evaluation around the existing conversation core.
