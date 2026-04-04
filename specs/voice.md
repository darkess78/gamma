# Voice Spec

## STT
Must be replaceable by adapter.
Default Phase 1 implementation: **Faster-Whisper**.

Phase 1 order:
1. file-based transcription
2. microphone capture later
3. controller-managed capture modes after the file path is stable

## TTS
Must be replaceable by adapter.
Current supported directions:
- `stub`
- `openai`

Planned next custom voice direction:
- `gpt-sovits`

Design rule:
TTS provider integration should stay adapter-based so Shana's final voice can move away from generic hosted TTS without rewriting the conversation layer.

## Voice mode controller
A controller layer should own interaction policy above raw STT/TTS adapters.
That keeps the working speech path intact while allowing multiple conversational modes.

Current controller modes:
- `turn-based`
  - validated runtime path
  - explicit record/respond turns
  - half-duplex
- `always-listening`
  - policy and re-arm behavior exist
  - should evolve toward VAD / wake-and-hold handling
  - currently allowed to execute through the same per-turn capture backend while plumbing matures
- `streaming`
  - target architecture for partial audio in / partial transcript out
  - should support lower-latency incremental response handling
  - not yet a true runtime path
- `interruptible`
  - target architecture for barge-in / playback interruption
  - should eventually allow user speech to cancel or duck assistant playback
  - not yet a true runtime path

Design rule:
mode selection belongs at the controller layer, not inside isolated mic-loop scripts.

## Language support
Voice stack should not assume English-only forever.
Language-related settings belong in config, not hardcoded business logic.
