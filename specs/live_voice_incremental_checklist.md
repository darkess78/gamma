# Live Voice Incremental Checklist

## Goal

Translate the full incremental live voice plan into a concrete implementation sequence against the current Gamma codebase.

This checklist assumes:
- the current simple chunked live path remains available as fallback
- the new path ships behind a separate live response mode toggle

## Mode strategy

Add a live response mode setting with two values:
- `simple_chunked`
- `incremental_experimental`

Recommended default:
- keep `simple_chunked` as default until the experimental path is stable

Likely touch points:
- [gamma/dashboard/static/index.html](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/index.html)
- [gamma/dashboard/static/dashboard.js](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/dashboard.js)
- [gamma/voice/live.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/live.py)
- [gamma/run_live_voice_worker.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/run_live_voice_worker.py)

## Phase 1: backend turn state

### Add state models

Create:
- [gamma/voice/reply_state.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/reply_state.py)

Suggested contents:
- `AssistantTurnState`
- `SentenceState`
- enums or literals for:
  - `planned`
  - `generating`
  - `synthesizing`
  - `speaking`
  - `completed`
  - `interrupted`
  - `cancelled`
  - `failed`

### Wire state into the live worker

Modify:
- [gamma/run_live_voice_worker.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/run_live_voice_worker.py)

Add:
- explicit in-memory turn state object
- sentence index tracking
- `assistant_reply_so_far`

Deliverable:
- worker can represent one live assistant turn with sentence-level status

## Phase 2: planner pass

### Add planner module

Create:
- [gamma/voice/reply_planner.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/reply_planner.py)

Planner output should include:
- answer intent
- tone
- key points
- estimated sentence count
- stop condition

### Reuse existing conversation context

Planner should reuse:
- memory/persona/system prompt machinery from [gamma/conversation/service.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/conversation/service.py)

Avoid:
- duplicating the full conversation pipeline

Deliverable:
- backend can create a compact non-user-facing reply plan from transcript + context

## Phase 3: next-sentence generation

### Add sentence generator module

Create:
- [gamma/voice/sentence_generator.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/sentence_generator.py)

Requirements:
- one sentence per generation call
- include `assistant_reply_so_far`
- return structured data

Suggested output:
- `sentence_text`
- `is_final`

### Use structured prompting

Do not use heuristic plain-text markers.

Preferred:
- strict JSON object

Deliverable:
- backend can generate sentence 1, sentence 2, etc. without restarting the answer

## Phase 4: incremental TTS queue

### Add sentence-level TTS queue

Create:
- [gamma/voice/tts_queue.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/tts_queue.py)

Responsibilities:
- accept generated sentence jobs
- synthesize in order
- attach timing
- support cancellation

Reuse:
- [gamma/voice/tts.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/tts.py)

Do not:
- put orchestration logic into `TTSService`

Deliverable:
- sentence-level TTS jobs can run independently of browser playback

## Phase 5: worker output model upgrade

### Replace final-result-only worker behavior

Modify:
- [gamma/run_live_voice_worker.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/run_live_voice_worker.py)

Current:
- writes one growing result payload after full reply text exists

Target:
- writes sentence/chunk progress incrementally as each sentence is generated and synthesized

Suggested output shape:
- `turn_id`
- `status`
- `transcript`
- `assistant_reply_so_far`
- `sentences`
- `timing_ms`

Each sentence item should include:
- `sentence_index`
- `text`
- `audio_base64` or artifact path
- `audio_content_type`
- `timing_ms`
- `interruptible`
- `protect_ms`
- `is_final`

Deliverable:
- worker can expose sentence-level progression, not just one monolithic final answer

## Phase 6: websocket event upgrade

### Add explicit incremental events

Modify:
- [gamma/voice/live.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/live.py)

Add events such as:
- `planner_ready`
- `sentence_generated`
- `reply_chunk_ready`
- `turn_completed`
- `turn_cancelled`
- `turn_failed`

Rule:
- websocket events should reflect state transitions, not ad hoc text blobs

Deliverable:
- browser receives sentence-level events immediately

## Phase 7: dashboard playback integration

### Reuse the existing queue

Modify:
- [gamma/dashboard/static/dashboard.js](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/dashboard.js)

Current queue can be reused for:
- ordered sentence playback
- interruption cleanup

Need to add:
- mode awareness
- sentence-level metadata display
- first-audio vs total timing display for experimental mode

Deliverable:
- browser starts playing sentence 1 as soon as it exists

## Phase 8: interruption and cancellation hardening

### Cancellation path

Modify:
- [gamma/voice/live.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/live.py)
- [gamma/voice/live_jobs.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/voice/live_jobs.py)
- [gamma/run_live_voice_worker.py](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/run_live_voice_worker.py)
- [gamma/dashboard/static/dashboard.js](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/dashboard.js)

Requirements:
- stop playback immediately
- cancel queued TTS jobs
- stop future sentence generation
- mark turn interrupted/cancelled
- avoid stale old-turn audio appearing later

Deliverable:
- interruption is reliable even mid-generation

## Phase 9: response-mode UI and observability

### Add live response mode selector

Modify:
- [gamma/dashboard/static/index.html](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/index.html)
- [gamma/dashboard/static/dashboard.js](C:/Users/darke/Documents/Projects/ai terminal/gamma/gamma/dashboard/static/dashboard.js)

Suggested control:
- `Live response mode`
  - `Simple chunked`
  - `Incremental experimental`

### Add metrics

Expose:
- `planner_ms`
- `time_to_first_sentence_ms`
- `time_to_first_chunk_audio_ms`
- per-sentence generation ms
- per-sentence TTS ms

Deliverable:
- simple and experimental modes can be compared directly

## Phase 10: cleanup and rollout

### Keep fallback path

Do not remove:
- current simple chunked path until the experimental mode is validated

### Validation sequence

Test in this order:
1. one short reply
2. one three-sentence reply
3. one interruption during sentence 1
4. one interruption during sentence 2
5. one noisy-mic test
6. one high-load test

Deliverable:
- confidence that the experimental path beats simple chunking on perceived responsiveness

## Recommended first implementation slice

Do this before touching the browser:
1. `reply_state.py`
2. `reply_planner.py`
3. `sentence_generator.py`
4. worker-only incremental sentence generation

Why:
- it proves backend coherence before adding more moving UI parts

## Expected tradeoff

This path is expected to:
- improve time-to-first-audio
- increase orchestration complexity
- probably increase total LLM work

So the success metric should be:
- better voice responsiveness

not:
- lower compute cost
