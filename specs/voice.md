# Voice Spec

## STT
Must be replaceable by adapter.
Default Phase 1 implementation: **Faster-Whisper**.

Current runtime behavior:
- local STT is in-process with Shana
- it is not managed as a separate background service
- the main operator actions today are smoke tests and controller validation, not start/stop lifecycle control

Phase 1 order:
1. file-based transcription
2. microphone capture later
3. controller-managed capture modes after the file path is stable

## TTS
Must be replaceable by adapter.
Current supported directions:
- `stub`
- `openai`
- `local` / `gpt-sovits`

Planned next custom voice direction:
- `gpt-sovits`

Current runtime behavior:
- local TTS is a managed GPT-SoVITS sidecar
- starting Shana should bring up GPT-SoVITS when local TTS is configured
- stopping Shana should tear GPT-SoVITS down
- hosted TTS providers should remain adapter-compatible and not require sidecar lifecycle management

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

## Live browser voice

Current live browser path:
- browser mic capture enters through the standalone dashboard WebSocket
- the dashboard owns silence detection, playback, and barge-in policy
- Shana owns live inference work through `/v1/voice/*`

Current runtime behavior:
- each finalized live turn starts as a dedicated Shana worker subprocess
- the worker owns `STT -> conversation -> TTS` for that turn
- active live turns are tracked in an in-memory Shana live-job registry keyed by `turn_id`
- each job records worker pid, status, timestamps, cancel reason, cancel latency, and output metadata
- cancellation kills the active live-turn worker process tree instead of only discarding a stale response

Live job states:
- `queued`
- `running`
- `speaking`
- `completed`
- `cancelled`
- `failed`

Operator semantics:
- `interrupted` means new user speech triggered cancellation of the active live turn
- `cancelled` means the Shana live-turn worker was terminated or marked cancelled
- `discarded` means a stale turn result arrived after interruption and was ignored

Design rule:
the dashboard may supervise live voice sessions, but long-running turn inference should remain owned by Shana rather than by the dashboard process.

Cross-platform expectation:
- Windows and Linux must both terminate active live-turn workers without leaving orphan child processes behind
- partial transcript requests remain lightweight snapshot work and are not individually hard-cancelled in v1

## Language support
Voice stack should not assume English-only forever.
Language-related settings belong in config, not hardcoded business logic.
