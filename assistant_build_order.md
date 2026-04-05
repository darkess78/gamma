# Assistant Build Order

Last updated: 2026-03-11

This is the practical roadmap for building the assistant in the right order.

## Guiding principle

Build the **core conversational loop** first.
Then add **consistency**.
Then add **memory**.
Then add **appearance/embodiment**.
Then add **tools and integrations**.

A pretty shell with weak memory and weak conversation will feel hollow.
A plain shell with strong voice, memory, and consistency already feels real.

---

## Phase 1 — Core voice loop

### Goal
Make it possible to talk to the assistant and have it talk back.

### Build
- speech-to-text
- LLM reply generation
- text-to-speech
- simple local conversation runner

### Recommended stack
- STT: Faster-Whisper
- LLM: one strong hosted model
- TTS: easiest working option first

### Definition of done
- microphone input works
- transcription is usable
- model responds reliably
- audio reply plays back
- whole loop feels conversational enough to test repeatedly

### Notes
Do not overcomplicate this with multi-agent logic yet.
Just make the loop work.

---

## Phase 2 — Character consistency

### Goal
Make the assistant feel like the same character from one reply to the next.

### Build
- `CHARACTER.md`
- `STYLE.md`
- `BOUNDARIES.md`
- optional `RELATIONSHIP_STATE.json`

### Character file should define
- name
- personality
- tone
- humor level
- how affectionate or teasing it should be
- what it should avoid doing
- how honest it should be when unsure

### Definition of done
- replies are mostly stylistically consistent
- assistant does not drift wildly between personalities
- tone feels intentional, not random

### Notes
This should happen early.
Consistency matters more than visuals at this stage.

---

## Phase 3 — Basic memory

### Goal
Make it remember enough to feel continuous.

### Build
- user profile facts
- important preference storage
- recent conversation summary
- simple retrieval of relevant memories

### Store things like
- name
- preferences
- recurring projects
- habits
- important prior conversations

### Definition of done
- it remembers real preferences
- it recalls recent context correctly
- it feels like the same assistant over time

### Notes
Do not store every single turn.
Store distilled useful memory.

---

## Phase 4 — Better voice identity

### Goal
Make the assistant sound like *itself*, not a generic TTS engine.

### Build
- improved TTS voice
- character-specific voice settings
- optional custom/cloned voice path

### Possible direction
- GPT-SoVITS-style setup
- clean reference samples
- emotional style tuning later

### Definition of done
- voice is recognizable
- playback quality is stable
- the assistant sounds like one coherent character

### Notes
If needed, start with generic TTS and upgrade later.
But this phase is where the character really starts landing.

---

## Phase 5 — Character creation / image generation

### Goal
Figure out what the character looks like.

### Build
- local image generation workflow
- reference art generation
- style exploration
- outfit / color palette / expression references

### Use image generation for
- visual ideation
- concept sheets
- reference boards
- character identity decisions

### Definition of done
- you have a stable visual design
- the character has a recognizable look
- you know whether you want 2D, 3D, or both

### Notes
This should support the assistant identity, not replace it.
The look matters, but the brain still matters more.

---

## Phase 6 — Embodiment

### Goal
Give the assistant a visible body/interface.

### Options
#### Option A: 2D first
- portrait
- expression swaps
- simple idle animation

#### Option B: 3D / VRM
- VRM avatar
- mouth animation
- blink
- expression control
- simple motion later

### Definition of done
- assistant visibly reacts while talking
- lip sync or mouth movement works
- a few emotional expressions are supported

### Notes
Start simple.
Idle + blink + mouth + 5 expressions is enough for a strong v1 embodiment layer.

---

## Phase 7 — Safe tool use

### Goal
Make it useful beyond conversation.

### Start with
- note writing
- memory updates
- file read in scoped folders
- web fetch/search
- maybe reminders/tasks

### Later
- file move/write
- messaging
- calendar
- smart-home
- other automations

### Definition of done
- assistant can complete bounded tasks
- tool use is logged
- destructive actions are gated

### Notes
Do not give it broad unsafe power too early.
Useful and safe beats impressive and risky.

---

## Phase 8 — Integrations

### Goal
Connect it to the places where it becomes part of daily life.

### Suggested order
1. notes/tasks
2. messaging/chat platform
3. calendar/reminders
4. Discord/YouTube-style integrations
5. smart-home/device control

### Definition of done
- at least one integration is genuinely useful in daily use

---

## Phase 9 — Advanced memory and emotional realism

### Goal
Make the assistant feel deeper, not just more functional.

### Build
- retrieval improvements
- summaries/consolidation jobs
- relationship state
- emotion tagging for replies
- more nuanced expression mapping

### Definition of done
- memory feels more human and less database-y
- emotional tone matches context better
- continuity is stronger over weeks, not just sessions

---

## Phase 10 — Advanced embodiment / wow factor

### Goal
Add the flashy stuff after the fundamentals are solid.

### Build
- better lip sync
- gesture/motion system
- imported dances/motions
- scene movement
- optional projection/display tricks

### Definition of done
- embodiment adds delight without making the system fragile

---

## The short version

If you want the ultra-compressed roadmap:

1. **speech in**
2. **character definition**
3. **LLM replies**
4. **speech out**
5. **memory**
6. **custom voice**
7. **character visuals**
8. **avatar/body**
9. **tools**
10. **integrations**
11. **advanced memory/emotion**
12. **advanced animation**

---

## First milestone I’d aim for

The first version worth using daily is:
- speech-to-text
- LLM reply
- text-to-speech
- CHARACTER.md
- small memory layer

That is the point where it stops being just an idea and starts being an assistant.

---

## Bottom line

The right order is:
- **brain first**
- **consistency second**
- **memory third**
- **body fourth**
- **agency after that**

That’s the path most likely to produce something actually good.
