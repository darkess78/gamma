# Live Voice Incremental Plan

## Goal

Move from the current live voice flow:

- `STT -> full LLM reply -> split into chunks -> TTS chunk 1 -> TTS chunk 2`

to a true incremental live reply flow:

- `STT -> sentence 1 generation -> TTS sentence 1 -> playback`
- while sentence 1 plays:
  - generate sentence 2
  - synthesize sentence 2
  - queue sentence 2

The target is lower time-to-first-audio, not lower total compute cost.

## Current state

Current simple live mode:
- full transcript first
- full reply text first
- split into up to 2 chunks after the full reply exists
- chunk 2 TTS can overlap chunk 1 playback

This improves post-LLM silence, but it does not improve the time spent waiting for the first reply text.

## What the full version needs

### 1. Assistant turn state

Add explicit backend state for one assistant live turn.

Suggested turn fields:
- `turn_id`
- `session_id`
- `user_text`
- `assistant_reply_so_far`
- `sentence_index`
- `planner_state`
- `generation_status`
- `tts_status`
- `playback_status`
- `cancel_requested`
- `interrupted`

### 2. Planner pass

Create a compact internal reply plan before sentence generation.

Planner output should include:
- answer intent
- tone
- key points
- estimated sentence count
- stop condition

This is not user-facing text.

### 3. Sentence generator

Create a dedicated prompt/schema for “generate the next sentence only”.

Inputs:
- conversation context
- user text
- planner output
- assistant text already spoken
- sentence index

Outputs:
- `sentence_text`
- `is_final`

### 4. Sentence-level TTS queue

Each generated sentence becomes a TTS job.

Suggested queue item:
- `turn_id`
- `sentence_index`
- `text`
- `audio_path`
- `audio_content_type`
- `tts_ms`
- `status`

### 5. Backend event model

Instead of only polling one result blob, the live path should emit sentence-level events:
- `sentence_generated`
- `reply_chunk_ready`
- `turn_completed`
- `turn_cancelled`
- `turn_failed`

The backend should remain the source of truth for ordering and cancellation.

### 6. Interruption rules

Interruption must cancel:
- active generation
- active TTS if possible
- queued future sentences
- browser playback

It should preserve already-spoken text only as conversation history, not as an unfinished reply to resume blindly.

## Rollout phases

### Phase 1: state and prompts

Add:
- turn-state model
- planner prompt
- next-sentence prompt/schema

No browser changes yet.

Deliverable:
- a backend-only sentence generator that can continue cleanly from `assistant_reply_so_far`

### Phase 2: backend incremental worker

Replace the current live worker flow with:
- transcript
- planner
- sentence 1 generation
- TTS sentence 1
- write partial result
- continue with sentence 2 while playback can happen

Deliverable:
- live worker writes sentence-level progress instead of only one final result

### Phase 3: websocket event upgrade

Update live websocket handling to forward sentence-level progress immediately.

Deliverable:
- browser receives sentence/chunk events in order with no fake “full reply first” dependency

### Phase 4: browser playback queue

Use the existing chunk queue as the playback base, but now fed from true incremental sentence events.

Deliverable:
- browser starts talking after sentence 1 is ready
- later sentences queue while earlier ones play

### Phase 5: interrupt hardening

Add robust cancel propagation across:
- planner
- sentence generation
- TTS
- playback

Deliverable:
- no stale old-turn audio after interruption

## Tradeoffs

### Better
- lower time-to-first-audio
- better voice responsiveness
- long replies feel less blocked

### Worse
- more LLM calls per turn
- more orchestration complexity
- likely higher resource use
- more failure modes

## Resource and latency expectation

This version is usually better for perceived latency, but not for total resource usage.

Expected:
- time-to-first-audio improves
- total LLM work may increase
- concurrency pressure increases

So this is a UX optimization, not a compute-efficiency optimization.

## Recommendation

Build it behind a live voice response mode toggle:
- `simple_chunked`
- `incremental_experimental`

That lets the current mode remain the fallback while the true incremental path is tuned.
