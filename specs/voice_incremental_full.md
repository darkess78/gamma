# Full Incremental Voice Blueprint

## Goal
Reduce time-to-first-audio by letting Shana start speaking before the entire reply is complete.

Target experience:
- user finishes speaking
- Shana starts talking quickly
- later sentences are generated while earlier sentences are being spoken
- new user speech can interrupt playback and cancel queued work cleanly

This is the full version.
It assumes explicit turn orchestration, sentence-level generation, queued TTS work, and interruption-safe state management.

## Core idea
Do not treat voice reply generation as one blocking `STT -> full LLM reply -> full TTS -> playback` call.

Instead:
1. finalize the user turn
2. create a reply plan / reply state
3. generate sentence 1 under a strict latency budget
4. send sentence 1 to TTS immediately
5. while sentence 1 is playing, generate sentence 2
6. continue until the reply is complete or interrupted

## Why this is better
- lowers time-to-first-audio
- hides later LLM latency behind audio playback
- makes longer answers feel more conversational
- gives a clean place to implement barge-in and cancellation

## Why the naive version is bad
Naive version:
- ask the LLM for one sentence
- ask again for another sentence
- keep looping

Problems:
- repetition
- contradiction
- restarting the answer each call
- weak control over completion
- awkward TTS chunk boundaries

The system needs an orchestrator with explicit reply state, not a loose loop.

## Required components

## 1. Turn Orchestrator
Owns the full lifetime of one spoken assistant turn.

Responsibilities:
- receives finalized transcript
- creates a unique reply id / turn id
- stores reply state
- coordinates sentence generation, TTS queueing, playback status, and cancellation
- marks the turn completed, interrupted, failed, or abandoned

Suggested state:
- `turn_id`
- `session_id`
- `user_text`
- `assistant_reply_so_far`
- `planned_reply_outline`
- `next_sentence_index`
- `generation_status`
- `tts_status`
- `playback_status`
- `cancel_requested`
- `interrupted_by_user`

## 2. Reply Planner
Creates a short internal plan for the answer before sentence streaming begins.

Purpose:
- maintain coherence across multiple sentence-generation calls
- keep later chunks aligned with the original intent

Output should be lightweight:
- answer intent
- stance / tone
- likely number of sentences
- key points to cover
- stop condition

The planner should not produce final user-facing prose.
It should produce internal structure.

## 3. Sentence Generator
Generates the next sentence only.

Inputs:
- conversation context
- user utterance
- planner output
- `assistant_reply_so_far`
- current sentence index
- max sentence count / stop condition

Rules:
- continue from what has already been spoken
- do not repeat earlier sentences
- do not restart the answer
- prefer short spoken language
- emit exactly one sentence or an explicit end-of-reply marker

Required outputs:
- `sentence_text`
- `is_final`
- optional `handoff_note` for the next sentence

## 4. TTS Work Queue
Converts generated sentences into playable audio independently of playback.

Responsibilities:
- accept sentence jobs in order
- synthesize sentence audio
- attach timing and artifact metadata
- preserve output ordering
- allow queued jobs to be cancelled before playback

Queue item shape:
- `turn_id`
- `sentence_index`
- `text`
- `status`
- `audio_path`
- `timing_ms`

## 5. Playback Queue
Owns audible delivery to the user.

Responsibilities:
- play sentence 1 as soon as ready
- continue sentence-by-sentence in order
- emit playback started / ended events
- stop immediately on barge-in
- discard queued audio after interruption

## 6. Interrupt Controller
Handles new user speech while Shana is already speaking.

Responsibilities:
- detect barge-in
- stop playback
- cancel in-flight sentence generation if possible
- cancel queued TTS work
- mark the old turn interrupted
- open the new user turn

Design rule:
interruption must be explicit state, not inferred later from stale results.

## End-to-end flow
1. Browser or mic layer finalizes user speech.
2. STT returns transcript.
3. Turn orchestrator opens a new assistant turn.
4. Reply planner creates a compact internal plan.
5. Sentence generator produces sentence 1.
6. TTS queue starts sentence 1 synthesis immediately.
7. Playback starts sentence 1 as soon as audio is ready.
8. While sentence 1 is playing, sentence generator produces sentence 2.
9. TTS queue synthesizes sentence 2.
10. Repeat until `is_final=true`.
11. Turn closes when playback of the final sentence ends.

## Concurrency model
At minimum, one assistant turn should support three concurrent lanes:
- generation lane
- TTS lane
- playback lane

The orchestrator is the source of truth.
Everything else is a worker around it.

## State machine

Suggested assistant turn states:
- `planned`
- `generating`
- `synthesizing`
- `speaking`
- `completed`
- `interrupted`
- `cancelled`
- `failed`

Sentence-level states:
- `pending`
- `generating`
- `ready_for_tts`
- `synthesizing`
- `ready_for_playback`
- `playing`
- `played`
- `discarded`
- `failed`

## Context rules
Every next-sentence generation call must include:
- the original user message
- relevant conversation history
- a compact reply plan
- the exact assistant text already spoken

This is the key coherence rule.
Without `assistant_reply_so_far`, the model will drift.

## Prompting rules

The sentence generator prompt should enforce:
- one sentence only
- continue from prior spoken text
- spoken style, not essay style
- no bullet points
- no recap of prior sentence
- if the answer is complete, emit an explicit end marker

Good stop markers:
- structured JSON output with `is_final`
- a tool-style schema

Bad stop markers:
- heuristics like "empty string means done"

## Voice quality concerns
Sentence-by-sentence TTS may sound choppy.

Mitigations:
- keep sentence boundaries natural
- avoid over-short fragments
- use punctuation-aware splitting
- allow the generator to emit one compound sentence when needed
- consider short lookahead so sentence 2 is ready before sentence 1 finishes

## Latency budget
Target per turn:
- STT: under 1s
- sentence 1 LLM generation: under 1.5s
- sentence 1 TTS: under 1.5s
- time-to-first-audio: under 3s

Later sentences can be slower as long as playback stays ahead of the queue.

## Failure handling

If planner fails:
- fall back to direct one-sentence generation

If sentence generation fails mid-turn:
- stop generating
- speak what is already ready if still valid
- close with a short fallback only if not interrupted

If TTS fails for one sentence:
- optionally skip that sentence
- or regenerate a shorter sentence once

If playback fails:
- keep text state and surface the textual response in UI

## Metrics to record

Turn-level:
- `stt_ms`
- `planner_ms`
- `time_to_first_sentence_ms`
- `time_to_first_tts_ms`
- `time_to_first_audio_ms`
- `total_turn_ms`
- `interrupt_count`

Sentence-level:
- `sentence_generation_ms`
- `tts_ms`
- `queue_wait_ms`
- `playback_start_offset_ms`
- `playback_duration_ms`

## Recommended Gamma fit
Best place for this in Gamma:
- keep `STTService` and `TTSService` as adapters
- add a voice reply orchestrator layer above `ConversationService`
- keep browser live voice as the operator-facing entrypoint
- do not bury sentence queue logic inside the dashboard client

Suggested new modules:
- `gamma/voice/reply_orchestrator.py`
- `gamma/voice/reply_state.py`
- `gamma/voice/sentence_generator.py`
- `gamma/voice/tts_queue.py`
- `gamma/voice/playback_events.py`

## Integration rules
- dashboard owns capture and browser playback
- backend owns turn orchestration and cancellation truth
- interruption should cancel backend turn state before UI clears local playback
- live voice websocket events should represent turn state transitions, not raw ad hoc strings

## Recommended rollout order
1. add assistant turn state model
2. add sentence generator prompt + schema
3. add backend TTS queue
4. add sentence-by-sentence websocket events
5. add interrupt-safe cancellation
6. tune voice pacing and boundary quality

## Decision
Use this version only if low-latency voice is a real product priority.
It is the correct architecture for high-quality conversational voice, but it is materially more complex than the current single-turn pipeline.
