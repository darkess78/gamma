# Simple Incremental Voice Blueprint

## Goal
Improve perceived latency without rewriting Gamma into a full streaming voice system.

This version is intentionally simpler than the full incremental blueprint.
It is meant to fit the current architecture:
- browser dashboard capture
- Shana live-turn worker
- `STT -> ConversationService -> TTS`
- browser playback

## Core idea
Keep one normal reply generation call.
After the full reply text is available:
- split it into 1-2 natural spoken chunks
- synthesize chunk 1 first
- start playback of chunk 1 immediately
- synthesize chunk 2 while chunk 1 is being played

This does not reduce LLM time.
It only reduces the delay between reply text completion and first audible output.

That is why this version is simpler but less powerful.

## What it helps
- lowers post-LLM silence
- makes playback begin earlier for multi-sentence replies
- gives a clean first step toward queue-based voice playback
- keeps most existing code intact

## What it does not help
- it does not fix slow LLM generation
- it does not provide true sentence streaming
- it does not let Shana speak before the full reply text exists

If LLM is the main bottleneck, this version alone will not solve the whole problem.

## Recommended use
Use this version first if:
- the goal is a fast MVP iteration
- you want lower implementation risk
- you want to validate chunked playback before building a full orchestrator

## Minimal design

## 1. Reply stays single-shot
Keep:
- one transcript
- one `ConversationService.respond(...)`
- one final reply text

No planner.
No next-sentence LLM calls.

## 2. Chunk the reply after generation
Split the final reply text into natural chunks.

Rules:
- prefer sentence boundaries
- cap to 2 chunks at first
- merge ultra-short fragments
- avoid splitting greetings from the main point

Good:
- `Hi. I'm here.`
- chunk 1: `Hi.`
- chunk 2: `I'm here.`

Bad:
- chunk 1: `Hi`
- chunk 2: `. I'm`

## 3. Queue TTS per chunk
Once reply text is split:
- synthesize chunk 1 immediately
- return chunk 1 audio as soon as ready
- synthesize chunk 2 in the background

The queue can be tiny:
- current chunk
- next chunk

No deep buffering needed for v1.

## 4. Browser plays chunks in order
The dashboard or live voice session should:
- begin playback when chunk 1 arrives
- request or receive chunk 2 before chunk 1 ends
- play chunk 2 if not interrupted

## 5. Interruptions discard remaining chunks
If the user speaks while chunk 1 is playing:
- stop browser playback
- discard chunk 2 if queued
- cancel any in-flight TTS task for the remaining chunk if practical

This is enough for basic barge-in safety.

## Suggested flow
1. STT finishes.
2. `ConversationService.respond(...)` returns full reply text.
3. Reply text is split into up to 2 chunks.
4. TTS starts on chunk 1.
5. As soon as chunk 1 audio is ready, playback begins.
6. TTS starts on chunk 2 while chunk 1 plays.
7. If no interruption occurs, chunk 2 plays next.

## Best place to implement it
Do not jam this into `TTSService`.
That service should stay text-to-audio focused.

Better fit:
- add a small voice reply chunker / queue layer above `ConversationService`
- use it inside live voice and browser voice roundtrip paths

Suggested new modules:
- `gamma/voice/reply_chunking.py`
- `gamma/voice/reply_playback_queue.py`

## Suggested data shape
Reply chunk:
- `turn_id`
- `chunk_index`
- `text`
- `audio_path`
- `content_type`
- `tts_ms`
- `status`

## Chunking rules
- maximum 2 chunks for v1
- maximum 1 sentence per chunk when practical
- if the reply is already short, do not split it
- if chunk 1 would be too short to sound natural, merge it with chunk 2

## UI / websocket rules
For live voice:
- send `reply_chunk_ready` events instead of only one final `turn_result`
- include chunk ordering info
- keep one final `turn_result` event for turn completion metadata

For browser roundtrip:
- either keep the existing single payload and add chunk metadata later
- or introduce a separate live-only chunked path first

Recommended first move:
- implement chunking only in live voice
- leave `/api/voice/roundtrip` as a single response path until live voice is stable

## Metrics to record
- `reply_generation_ms`
- `chunk_count`
- `chunk_1_tts_ms`
- `chunk_2_tts_ms`
- `time_to_first_chunk_audio_ms`
- `total_playback_ms`

## Risks
- little or no gain if the reply is only one short sentence
- no improvement to LLM latency
- chunk boundary quality can still sound awkward
- more moving parts than one WAV, but fewer than full streaming

## Recommended Gamma rollout
1. add reply text chunker
2. add chunk queue for live voice only
3. add browser playback support for ordered chunks
4. add interruption cleanup for queued chunks
5. measure whether reduced playback delay is noticeable

## Decision
This is the right intermediate step if you want to learn quickly with limited implementation risk.

If it feels meaningfully better, move to the full incremental blueprint next.
If it does not, skip further chunking work and focus on reducing LLM latency directly.
