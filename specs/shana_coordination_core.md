# Shana coordination core

Status: first milestone in development on `rebuild/shana-foundation`, 2026-11-13.
Not connected to production Shana, Monitor or any public channel.

This is the small core described in the [handoff](shana_coordination_core_handoff.md):
one owner of what Shana is doing, starting with private owner text and one
capability, offline coding practice.

## How it works

`src/gamma/coordination/core.py` runs one loop:

1. Take in what happened: an owner message, a worker result, time passing.
2. Ask the model for one decision (`decide.py`): what to say (or nothing), and at
   most one action: start, pause, resume or stop practice.
3. Code checks the action is allowed and applies it.
4. `tick()` folds practice results back into the activity record, once each.

The model sees the situation as JSON: recent conversation, focus, mood, her own
short note, and each activity with per-task results read from the run's
checkpoint. Completion claims can only come from that record.

## Behaviour covered by tests

`tests/test_coordination_core.py`:

- Conversation stays available while practice runs.
- Switching pauses the run; resuming tests the saved edit instead of regenerating it.
- While asleep, messages are queued without model calls and workers keep going.
  On waking she gets the real outcomes once, then queued messages are answered.
- After a crash, a new core resumes the run from its last checkpoint.
- Only one core can own the state; a replaced core cannot commit effects.
- A reply drafted before a newer message arrived is dropped, not acted on.
- Unknown exercises and made-up activity ids are refused by code.
- Quiet review happens only after a quiet period and cannot start a second run.
- If the GPU is in use (any model resident in Ollama), practice waits instead of failing.
- One test runs the real sandboxed checks and hidden-case evaluation (Linux only).

## Running it

```bash
GAMMA_COORDINATION_DEV=1 python -m gamma.coordination.app --port 8010
```

Loopback only, state under `data/coordination-dev`, no authentication, no speech,
no writes to Shana's memory. Routes: `POST /api/coordination/input`, `/sleep`,
`/wake`, `GET /api/coordination/status`.

## Not done yet

- Monitor does not talk to this core yet; it still uses the stream decision path.
- The persona prompt is built without owner-private memory.
- Mood, focus and notes are single fields the model sets; nothing settles mood over time.
- No real-model conversation testing yet, and no judgement yet on whether it feels like Shana.
