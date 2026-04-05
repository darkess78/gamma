# Riko-Clone Phase 1 Checklist

Last updated: 2026-03-11

## Phase 1 goal

Build the first usable voice loop:
- human speech in
- transcription
- LLM reply generation
- text-to-speech
- audio reply out

This phase is complete when you can speak to the assistant and get a spoken reply back reliably enough to keep testing.

---

## Scope

### In scope
- local/backend conversation loop
- one assistant endpoint or runner
- one LLM provider
- one STT implementation
- one TTS implementation
- basic persona injection
- minimal logging

### Out of scope
- deep memory
- avatar frontend
- tools
- integrations
- advanced interruption handling
- emotion system

---

## Existing scaffold

Current repo base:
- `riko-clone/`
- `riko_clone/main.py`
- `riko_clone/api/routes.py`
- `riko_clone/conversation/service.py`
- `riko_clone/voice/stt.py`
- `riko_clone/voice/tts.py`
- `riko_clone/persona/core.md`
- `riko_clone/persona/style.json`
- `riko_clone/persona/boundaries.md`

---

## Deliverables

By the end of Phase 1, the repo should have:

### 1. A conversation request schema
Add something like:
- `riko_clone/schemas/conversation.py`

Suggested models:
- `ConversationRequest`
- `ConversationTurn`
- `SpeechInputRequest` if using file-path based local testing first

Example shape:
```python
class ConversationRequest(BaseModel):
    user_text: str
    session_id: str | None = None
```

---

### 2. A persona loader
Add:
- `riko_clone/persona/loader.py`

Responsibilities:
- read `core.md`
- read `style.json`
- read `boundaries.md`
- build a system prompt block

Suggested function:
```python
def build_system_prompt() -> str:
    ...
```

---

### 3. A real conversation service
Upgrade:
- `riko_clone/conversation/service.py`

Responsibilities:
- accept user text
- assemble prompt
- call the LLM adapter
- convert output into `AssistantResponse`

Suggested methods:
```python
class ConversationService:
    def respond(self, user_text: str, session_id: str | None = None) -> AssistantResponse:
        ...
```

For now, it can ignore memory and just use persona + recent user text.

---

### 4. One LLM adapter
Add:
- `riko_clone/llm/__init__.py`
- `riko_clone/llm/base.py`
- `riko_clone/llm/openai_adapter.py` or generic hosted adapter

Responsibilities:
- hide provider-specific code
- expose one clean method like:
```python
def generate_reply(system_prompt: str, user_text: str) -> str:
    ...
```

For Phase 1:
- keep it dead simple
- one provider only
- no tool calling yet

---

### 5. STT implementation path
Upgrade:
- `riko_clone/voice/stt.py`

Choose one of these approaches:

#### Option A — easiest for local testing
- take an existing audio file path
- transcribe it

#### Option B — better usability
- record from mic and transcribe

For Phase 1, Option A is fine if it gets the pipeline working faster.

Suggested interface:
```python
class STTService:
    def transcribe_audio(self, source: str) -> str:
        ...
```

---

### 6. TTS implementation path
Upgrade:
- `riko_clone/voice/tts.py`

Suggested interface:
```python
class TTSService:
    def synthesize(self, text: str, emotion: str | None = None) -> bytes:
        ...
```

Phase 1 requirement:
- convert text to playable audio output somehow

Could be:
- saved WAV file
- returned bytes
- external API response

---

### 7. A local loop runner
Add:
- `riko_clone/run_local_voice_loop.py`

Responsibilities:
- capture or load speech input
- transcribe it
- send text to conversation service
- synthesize reply audio
- play or save output
- print useful logs for debugging

This is probably the fastest path to “it actually works.”

---

### 8. API endpoint for text conversation
Upgrade:
- `riko_clone/api/routes.py`

Add:
- `POST /v1/conversation/respond`

Behavior:
- accept text request
- return `AssistantResponse`

This makes it easier to later plug in a frontend.

---

### 9. API endpoint for speech pipeline testing
Optional but useful:
- `POST /v1/conversation/respond-from-audio`

Behavior:
- accept a local path or uploaded audio
- transcribe
- generate reply
- return text + maybe path to synthesized audio

---

### 10. Minimal config/env support
Upgrade:
- `riko_clone/config.py`

Add fields for:
- API keys
- STT model choice
- TTS endpoint/config
- output audio directory
- persona file paths

Suggested additions:
```python
openai_api_key: str | None = None
stt_model_name: str = "base.en"
audio_output_dir: Path = Path("./data/audio")
```

---

## Recommended implementation order inside Phase 1

### Step 1
Build persona loader.

### Step 2
Build text-only LLM reply path.

### Step 3
Add `POST /v1/conversation/respond`.

### Step 4
Replace stub TTS with a real implementation or placeholder file output.

### Step 5
Replace stub STT with file-based transcription.

### Step 6
Add a local runner that executes the whole loop.

### Step 7
Test with repeated short prompts until the loop feels stable.

---

## Definition of done

Phase 1 is done when all of these are true:

- you can submit speech or transcribed text
- the assistant replies in a stable persona
- the reply can be synthesized into audio
- the loop can be run repeatedly without falling apart
- logs are clear enough to debug latency/failures

---

## Nice-to-have extras if easy

- save transcript + response pairs to a JSONL log
- include latency timings for:
  - STT
  - LLM
  - TTS
- support a CLI flag for text-only mode
- support a quick “demo” command

---

## First test script

Use these basic test prompts first:
- "Hey, who are you?"
- "What should I call you?"
- "Do you remember anything yet?"
- "Tell me a short joke."
- "Explain what you can do right now."

Goal:
- verify transcription quality
- verify persona consistency
- verify reply audio is intelligible

---

## What to avoid in Phase 1

Do not get distracted by:
- vector memory
- multiple models
- tool calling
- avatar rendering
- image generation
- emotion pipelines
- fancy orchestration

The only job of Phase 1 is:
**make the voice conversation loop real.**

---

## Suggested next file additions

Create next:
- `riko_clone/schemas/conversation.py`
- `riko_clone/persona/loader.py`
- `riko_clone/llm/base.py`
- `riko_clone/llm/openai_adapter.py`
- `riko_clone/run_local_voice_loop.py`

These are the next most important files.

---

## Bottom line

Phase 1 is successful when the project stops being a scaffold and becomes a thing you can actually talk to.
