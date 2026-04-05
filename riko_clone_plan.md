# Riko / Just Rayen Clone Plan

Last updated: 2026-03-11

## Purpose

This file is the practical **how-to / cloning guide** for building something in the same spirit as the Rico / AI-waifu stack described in the available public repo, video transcripts, and research notes.

This is **not** a claim of the exact private production stack.
It is the best practical reconstruction from what is publicly visible plus the recovered transcript text.

---

## What seems genuinely confirmed

Across the public repo, public descriptions, and transcript set, these pieces are the strongest confirmed signals:

- **Python-heavy orchestration**
- **LLMs**: OpenAI / ChatGPT, Gemini, Claude, sometimes others for comparison
- **Memory**: at least one real pass using **FAISS** retrieval memory
- **Voice**:
  - custom TTS focus
  - voice actress samples
  - GPT-SoVITS-style voice training / inference
- **Avatar**:
  - **3D avatar**, not just static art
  - **VRM / VRoid / Void Studio** pipeline
  - mouth animation / expressions / motion work
- **Animation tooling**:
  - XR Animator
  - VMD
  - BVH → VRMA conversion
- **Vision**:
  - camera/image understanding
  - Gemini used because of price/performance
- **Tool use / agent behavior**:
  - file tools like read / write / move / delete
  - manager-agent / tool-exposed workflow
  - Autogen was tried but not kept as the final answer
- **Deployment evolution**:
  - local machine first
  - later server-hosted for remote/mobile access
- **Extra integrations**:
  - YouTube comments
  - Discord
  - smart home
  - games
  - phone/app control

---

## Best reconstruction of the overall architecture

If you want to clone the *capability shape*, build it as 6 layers:

1. **Conversation brain**
2. **Memory system**
3. **Voice stack**
4. **Avatar / animation frontend**
5. **Tool / action system**
6. **Deployment + product shell**

### 1) Conversation brain
Responsible for:
- persona
- dialogue
- planning
- tool selection
- emotional tone

Recommended clone:
- main hosted model for quality
- examples:
  - GPT-4.1 / GPT-5-class model
  - Claude
  - Gemini

Practical recommendation:
- use one strong main model first
- don’t start multi-model unless there’s a clear reason
- add model routing later

What Rico-like builds appear to do:
- use frontier APIs for the main intelligence
- sometimes use a different model for special tasks like debugging, vision, or coding

---

### 2) Memory system
This is one of the most important parts.

Publicly, there are two levels seen:
- simple saved chat history
- later **FAISS retrieval memory**

For a worthwhile clone, do **not** rely only on raw conversation logs.

Recommended memory design:

#### a. Short-term memory
- current conversation window
- recent turns
- current goals / mode

#### b. Episodic memory
Store important events as memory notes:
- user preferences
- relationship beats
- recurring topics
- important tasks
- emotional moments

#### c. Retrieval layer
- embed memory notes
- search top-k relevant memories for each reply
- inject only relevant memory, not everything

#### d. Profile memory
Structured persistent facts:
- name
- likes/dislikes
- recurring projects
- devices
- schedule preferences
- favorite tone / style

Recommended stack:
- start with SQLite or JSON + embeddings
- FAISS is fine if you want transcript-faithful reconstruction
- if building cleanly from scratch, SQLite + pgvector/lancedb/chroma are also fine

Best practice:
- summarize before storing
- dedupe memories
- timestamp everything
- keep “facts” separate from “moments”

---

### 3) Voice stack
A Rico-like build clearly treats voice as core, not decoration.

## Input (speech-to-text)
Minimum viable:
- Faster-Whisper

Better production version:
- VAD (voice activity detection)
- streaming transcription
- interruption/barge-in support
- latency-aware audio queue

Recommended clone path:
- start with Faster-Whisper
- add VAD and streaming later

## Output (text-to-speech)
Transcript/public evidence points strongly toward:
- voice actress recordings
- GPT-SoVITS-style voice workflow

Recommended clone path:
- collect clean voice samples
- train/condition GPT-SoVITS or equivalent
- keep character-specific reference audio and prompt text
- support emotion/style tags later

If you want a faster but less faithful MVP:
- use a decent API TTS first
- switch to cloned/custom voice later

---

### 4) Avatar / animation frontend
This is where most people underestimate the work.

A Rico-like clone likely needs:
- 3D avatar model
- VRM-compatible runtime
- expression switching
- lip sync / mouth animation
- idle animation
- optional imported dances / motions

Recommended stack:

#### Avatar authoring
- **VRoid Studio** / Void Studio-style workflow
- Unity for asset fixes/conversion if needed
- export to **VRM**

#### Runtime rendering
- Three.js + **Three-VRM**
- or Unity if you want desktop-native instead of web-first

#### Motion / animation
- mouth open/close from audio amplitude at first
- later phoneme/viseme mapping
- expressions from:
  - LLM emotion tags, or
  - tiny classifier over the generated text
- motion import path if desired:
  - XR Animator
  - VMD
  - BVH
  - VRMA conversion

#### Physical display gimmick (optional)
If you want the “breaking out of 2D jail” style effect:
- Pepper’s Ghost style display setup
- angled transparent surface + monitor/projected image

Important recommendation:
- don’t start with full-body live mocap
- start with:
  - idle pose
  - blinking
  - mouth animation
  - 5-10 emotional expressions

That gets 80% of the effect for much less pain.

---

### 5) Tool / action system
A Rico-like clone becomes much more interesting once it can actually do things.

Public/transcript-confirmed examples:
- read files
- write files
- move files
- delete files
- YouTube comment extraction/reply
- app/device integration
- smart-home control
- game actions

Recommended architecture:

#### Keep the brain separate from the tools
The main model should not directly mutate the world.
Use a tool layer with:
- schemas
- permissions
- logging
- confirmations where needed

#### Suggested pattern
- **manager agent** decides what needs doing
- tool modules execute actions
- results feed back into the manager

That lines up well with the transcript mention of a:
- manager agent
- tool-exposed model
- feedback loops

#### Safety rules
For a clone worth living with:
- read freely
- confirm destructive actions
- scope file access carefully
- log all external actions
- isolate risky tools from the main chat loop

Recommended first tools:
1. notes / memory write
2. file read
3. web fetch
4. calendar/task hooks
5. smart-home or messaging after that

---

### 6) Deployment + product shell
The project appears to have moved from:
- local-only
- to server-backed remote access

That’s the right progression.

## Local phase
Start local for:
- voice iteration
- avatar debugging
- latency testing
- model/tool wiring

## Remote phase
Once useful, move to a server for:
- mobile access
- reliability
- always-on availability
- easier API integration

Recommended deployment split:
- **frontend/avatar client**: local desktop or browser
- **backend API**: server
- **memory store**: server
- **TTS/STT workers**: local or server depending cost/privacy

Best practical architecture:
- hosted main brain
- local/avatar runtime if you want low-latency visuals
- background memory/indexing worker
- message/event bus between components

---

## The smartest way to clone it: build order

Do **not** try to build the whole myth all at once.

### Phase 1 — Voice companion MVP
Goal: talking character with memory

Build:
- ASR
- LLM reply generation
- TTS
- persona prompt
- simple memory store

Success looks like:
- you can talk to the character
- it answers in-character
- it remembers recent facts

### Phase 2 — Real memory
Goal: stop it from being fake-short-term-only

Build:
- summarized memory notes
- retrieval search
- profile memory
- memory write policies

Success looks like:
- it recalls prior preferences and important moments accurately

### Phase 3 — Avatar embodiment
Goal: give it a body that reacts

Build:
- VRM avatar
- Three-VRM or Unity runtime
- lip sync
- blinking
- expression switching

Success looks like:
- it talks and visually reacts in sync

### Phase 4 — Tool use
Goal: make it useful, not just pretty

Build:
- safe file tools
- search/browser tools
- small automations
- logging + guardrails

Success looks like:
- it can complete bounded tasks reliably

### Phase 5 — Integrations
Goal: make it feel like a real assistant

Possible integrations:
- Discord
- YouTube comments
- calendar
- reminders
- phone hooks
- smart-home devices

### Phase 6 — Motion / “wow factor”
Goal: the advanced embodiment stuff

Build:
- imported motion files
- dance/emote playback
- scene movement
- optional display tricks like Pepper’s Ghost

---

## Minimal viable stack recommendation

If I were cloning this for real without wasting months, I’d choose:

### Backend
- Python
- FastAPI
- one strong hosted LLM
- Faster-Whisper
- GPT-SoVITS or equivalent
- SQLite/Postgres for state
- vector retrieval layer

### Frontend
- web frontend
- Three.js + Three-VRM
- websocket connection to backend
- simple emotion + lip sync bridge

### Memory
- profile facts
- episodic summaries
- embedding search

### Tools
- read/write notes
- search/fetch
- calendar/tasks
- carefully gated file tools

This is enough to make something that actually feels alive.

---

## What not to copy too literally

A few things are easy to romanticize and hard to maintain.

Avoid overcommitting early to:
- too many models
- too many agents
- too many integrations
- fully autonomous posting/actions
- giant context windows as a substitute for memory design
- perfect avatar animation before the assistant is useful

The right order is:
- useful
- stable
- memorable
- embodied
- flashy

Not the reverse.

---

## Open questions / unknowns

Things still not fully confirmed even after transcript recovery:
- exact fine-tuning provider/process
- exact production model lineup at any given time
- exact repo split between public and private code
- exact TTS training workflow end to end
- exact frontend implementation details
- how much of the “evolving personality” is true memory vs branding language

So if you clone this, treat these as design choices, not recovered facts.

---

## Practical clone blueprint

If your goal is “build my own Rico-like assistant,” this is the blueprint:

1. **Character definition**
   - persona
   - voice identity
   - expression palette
   - boundaries

2. **Conversation loop**
   - speech in
   - reply generation
   - speech out

3. **Memory loop**
   - summarize important moments
   - store
   - retrieve relevant memories later

4. **Embodiment loop**
   - text/emotion → expression
   - audio → lip sync
   - idle + gesture layer

5. **Agency loop**
   - decide
   - call tool
   - observe result
   - reflect/update

6. **Deployment loop**
   - local prototype
   - server backend
   - remote/mobile access

That’s the version worth building.

---

## Bottom line

The cloneable essence is **not** “anime girl UI.”
It’s this:

- strong conversational model
- real memory
- custom voice
- embodied avatar
- safe tool use
- enough integrations to matter

That is the actual core.

The public/transcript evidence strongly suggests the original project followed roughly this path:
- start with API-based intelligence
- move pieces local where useful
- improve voice/persona
- add memory
- add 3D embodiment
- add tools/integrations
- move toward server-backed availability

If you want a serious clone, copy **that progression**, not just the aesthetics.
