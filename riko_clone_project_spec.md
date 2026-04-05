# Riko-Like Assistant Project Spec

Last updated: 2026-03-11

## Goal

Build a Rico-inspired assistant with:
- persistent personality
- real memory
- custom voice
- 3D avatar embodiment
- safe tool use
- room to grow into integrations like Discord, YouTube, phone, and smart-home control

This spec is for **implementation**, not just research.

---

## Product definition

### Core promise
A user can talk to a persistent character assistant that:
- responds in a distinctive voice/personality
- remembers important things over time
- visually reacts through a 3D avatar
- can safely perform bounded assistant tasks

### Non-goals for v1
Do **not** try to ship these first:
- fully autonomous social posting
- broad destructive file control
- perfect full-body mocap
- self-modifying agents
- dozens of integrations
- multi-user SaaS productization

---

## Success criteria

### V1 success
- speech in works reliably
- replies are in-character and fast enough to feel conversational
- memory recall works on real previous interactions
- avatar lip sync + basic expressions work
- assistant can use a few safe tools

### V2 success
- remote/backend deployment works
- memory quality is noticeably better over weeks
- at least 2 meaningful integrations exist
- interruption/barge-in feels natural

---

## Target architecture

## 1. Backend API
Responsibility:
- session management
- message handling
- memory retrieval/writeback
- tool orchestration
- emotion/expression tagging
- TTS/STT coordination

Recommended stack:
- Python
- FastAPI
- WebSocket support
- background worker queue for slower jobs

Suggested modules:
- `api/`
- `conversation/`
- `memory/`
- `tools/`
- `voice/`
- `avatar_events/`
- `integrations/`

---

## 2. LLM orchestration layer
Responsibility:
- prompt assembly
- memory injection
- tool decision making
- response generation
- structured output for avatar state/emotion

Recommended design:
- one main assistant model first
- optional helper model later for cheap background tasks

Required outputs from the model:
- `spoken_text`
- `internal_summary` (optional, not user-facing)
- `emotion_tag`
- `tool_calls` (when needed)
- `memory_write_candidates`

Example structured response shape:
```json
{
  "spoken_text": "Hey, I remember you said you hate early alarms.",
  "emotion_tag": "teasing_warm",
  "tool_calls": [],
  "memory_write_candidates": [
    {
      "type": "preference",
      "text": "User dislikes early alarms."
    }
  ]
}
```

---

## 3. Memory system

### Memory requirements
Need 3 layers:

#### A. Conversation context
- last N messages
- current task/mode
- current session metadata

#### B. Structured user profile
- name
- pronouns
- preferences
- recurring projects
- devices/platforms
- relationship notes if desired

#### C. Episodic memory store
- summarized past events
- important moments
- tasks completed
- promises/commitments
- recurring themes

### Memory operations
- `remember_event()`
- `remember_preference()`
- `search_memories(query)`
- `get_user_profile()`
- `consolidate_memories()`

### Recommended implementation
V1:
- SQLite/Postgres
- embedding-backed retrieval
- simple scheduled memory consolidation

Schema sketch:

#### `profile_facts`
- id
- user_id
- category
- fact_text
- confidence
- source
- created_at
- updated_at

#### `episodic_memories`
- id
- user_id
- summary
- embedding
- importance
- tags
- created_at
- source_session

#### `conversation_sessions`
- id
- user_id
- started_at
- ended_at
- summary

### Retrieval policy
For each response:
1. build memory query from latest user turn + current task
2. retrieve top relevant profile facts
3. retrieve top relevant episodic memories
4. inject compactly into prompt

### Memory write policy
Write memory only when the event is:
- preference-bearing
- personally meaningful
- recurring
- task-relevant later
- emotionally salient

Do **not** store every turn.

---

## 4. Speech system

## STT requirements
V1:
- Faster-Whisper
- push-to-talk or VAD

V2:
- streaming ASR
- interruption support
- partial transcript updates

Recommended interface:
- `transcribe_audio(file_or_stream) -> text`

## TTS requirements
V1:
- character voice synthesis with stable style
- cache repeated lines if useful

V2:
- emotion-conditioned generation
- faster streaming audio chunks

Recommended interface:
- `synthesize(text, emotion=None) -> audio`

Notes:
- GPT-SoVITS-style path is likely best if the goal is fidelity to the Rico-like experience
- clean voice data matters more than clever orchestration here

---

## 5. Avatar frontend

### Responsibilities
- render VRM avatar
- lip sync from audio energy/visemes
- blink/idle movement
- expression switching from backend emotion tags
- optional gesture/motion playback

### Recommended stack
- web frontend
- Three.js
- Three-VRM
- WebSocket connection to backend

### Minimum frontend events
- `assistant_speaking_started`
- `assistant_speaking_finished`
- `emotion_changed`
- `motion_requested`
- `thinking_state_changed`

### Emotion set for v1
Keep it small:
- neutral
- happy
- teasing
- concerned
- excited
- embarrassed
- annoyed

### Motion for v1
- idle animation loop
- blink
- head tilt variants
- mouth movement

Do not start with complex dance/mocap.

---

## 6. Tool system

### Principles
- tools are explicit
- tool calls are logged
- destructive tools require confirmation
- the assistant never gets raw unlimited shell by default

### V1 tool list
- memory write/read
- notes file write
- web fetch/search
- calendar/task hooks (if desired)
- safe local file read in scoped folders

### V2 tool list
- bounded file write/move
- messaging integrations
- Discord
- YouTube comment moderation/reply
- smart-home device actions

### Tool execution model
1. model proposes tool call
2. validator checks schema + permissions
3. tool runs
4. result is summarized back to model
5. assistant gives final user-facing response

---

## 7. Persona system

### Needs
A good clone should separate:
- immutable character identity
- style knobs
- user-specific relationship state

### Suggested files/config
- `persona/core.md`
- `persona/style.json`
- `persona/boundaries.md`
- `persona/relationship_state.json`

### Persona dimensions
- tone
- humor level
- teasing vs formal balance
- affection level
- verbosity
- confidence/opinionatedness

Important:
Do not hide all of this in one giant system prompt.

---

## 8. Integrations roadmap

### Priority order
1. local notes/tasks
2. Discord or chat platform
3. calendar/reminders
4. YouTube comments
5. smart-home
6. phone/app control

### Why this order
Because it moves from:
- safe + useful
- to more public/risky
- to more fragile/platform-specific

---

## 9. Deployment plan

### Dev environment
- local machine for avatar + voice iteration
- backend can run locally at first

### Production-like environment
Split into:
- frontend/avatar client
- backend API server
- DB + memory store
- optional GPU voice worker
- optional background worker

### Recommended hosting model
- main LLM hosted via API
- backend on a VPS/server
- avatar frontend local desktop or browser
- memory DB on backend
- TTS either local GPU or dedicated voice box/server

---

## 10. Data model sketch

### `assistant_response`
```json
{
  "spoken_text": "string",
  "emotion": "neutral|happy|teasing|concerned|excited|embarrassed|annoyed",
  "motions": ["optional_motion_ids"],
  "tool_calls": [],
  "memory_candidates": []
}
```

### `memory_candidate`
```json
{
  "type": "preference|fact|episode|task|boundary",
  "text": "string",
  "importance": 0.0,
  "tags": ["optional", "tags"]
}
```

### `tool_call`
```json
{
  "tool": "tool_name",
  "args": {}
}
```

---

## 11. Milestones

## Milestone 1: talking assistant shell
Deliverables:
- backend API
- LLM call
- STT
- TTS
- text chat + speech loop

## Milestone 2: memory that actually works
Deliverables:
- profile facts
- episodic summaries
- retrieval injection
- memory review job

## Milestone 3: avatar embodiment
Deliverables:
- VRM avatar loaded
- lip sync
- blink/idle
- expression switching

## Milestone 4: tools
Deliverables:
- safe tool registry
- logs
- read/search/note tools
- confirmation flow for risky actions

## Milestone 5: integrations
Deliverables:
- one messaging platform
- one productivity integration
- one external automation

---

## 12. Testing plan

### Functional tests
- can the assistant hear correctly?
- can it reply in persona?
- does it retrieve the right memories?
- do tools run safely and deterministically?
- does avatar emotion match response tone?

### Evaluation scenarios
Create repeatable test scripts for:
- memory recall after 1 day / 1 week
- interruption during speech
- repeated preference recall
- safe refusal on destructive action
- emotional consistency

### Red-team checks
- prompt injection through tools/web pages
- accidental destructive file ops
- hallucinated memory writes
- runaway tool loops

---

## 13. Biggest engineering risks

- memory becoming noisy junk
- latency making the character feel dead
- avatar work eating all the time before assistant usefulness exists
- tool access becoming unsafe
- overengineering multi-agent behavior too early
- trying to support too many platforms before the core loop feels good

---

## 14. Recommended implementation order for a small team or solo build

If doing this seriously, build in this exact order:

1. backend conversation API
2. STT + TTS loop
3. persona + structured outputs
4. memory retrieval + writeback
5. avatar frontend with lip sync + expression tags
6. safe tools
7. first useful integration
8. deployment hardening
9. only then advanced motion / wow-factor features

---

## 15. Definition of "good enough to use daily"

You know this project is actually crossing the line when:
- you prefer talking to it instead of typing to a generic chatbot
- it remembers real things without being creepy or wrong all the time
- the voice feels consistent
- the avatar reactions feel believable enough not to distract
- tools make it useful in daily life

That’s the target.

---

## Bottom line

The implementation thesis is simple:

**Build a strong assistant first, then embody it.**

Not:
- build a fancy anime shell first and hope intelligence catches up.

If this project follows the right order, the winning stack is:
- strong hosted model
- real memory
- custom voice
- VRM avatar frontend
- safe tool use
- incremental integrations

That is the version most likely to become something genuinely compelling.
