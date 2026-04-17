# Voice Interruptibility Policy

## Goal

Let Gamma briefly hold the floor in narrow cases without turning interruption behavior into an inconsistent LLM-driven decision.

## Decision

Interruption policy is deterministic code, not free-form model behavior.

Default behavior:
- live voice replies are interruptible
- browser barge-in stops playback and cancels the active turn

Exception:
- the first reply chunk may be briefly protected when it matches a narrow urgent pattern
- protection is time-limited
- later chunks remain interruptible

## Why not let the LLM decide freely

- it would be inconsistent turn to turn
- it would feel stubborn when the assistant keeps talking over the user
- interruption handling is playback/control policy, not just language generation
- debugging user complaints would become much harder

## Current deterministic rule

Chunk 1 gets a brief protected window only if it starts with an urgent prefix such as:
- `wait`
- `stop`
- `don't`
- `do not`
- `hold on`
- `careful`
- `warning`
- `no,`
- `no.`

Constraints:
- protection only applies to chunk 1
- protection window is short: `700 ms`
- long chunks are not protected
- all later chunks are interruptible

## Data shape

Each reply chunk carries:
- `interruptible`
- `protect_ms`

Browser live voice uses those fields to decide whether barge-in is allowed at that moment.

## UX intent

Good protected examples:
- `Wait, don't click that.`
- `Stop, that's the wrong file.`

Bad protected examples:
- normal conversation
- long explanations
- personality-driven refusal to be interrupted

## Future extension

If needed later, the model can suggest a bounded priority hint such as:
- `normal`
- `briefly_protected`

But deterministic code should still enforce hard limits:
- first chunk only
- short time window only
- system rules override model hints
