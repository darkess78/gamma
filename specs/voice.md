# Voice

Status: Current
Last verified: 2026-06-22

## Ownership

Shana owns STT, audio understanding, conversation inference, TTS, and live-job
state. Browser and CLI clients own capture/playback policy but call Shana APIs
for inference.

## STT

- Faster-Whisper is the primary local adapter.
- OpenAI is the hosted adapter.
- Stub transcription supports safe tests.
- Provider imports and model loading remain lazy.
- Audio is normalized and temporary uploads are deleted after processing.

## TTS

- Piper provides local in-process speech.
- Qwen TTS uses a managed local HTTP sidecar.
- OpenAI provides hosted speech.
- Voice profiles load from layered `config/voices*.toml` files.
- RVC is an optional Piper post-process, not a standalone provider.
- Conversation responses expose text, content type, timing, and local artifact metadata.

## Controllers

- file roundtrip and smoke-test CLIs
- turn-based and always-listening microphone controller modes
- browser live voice described in `live_voice.md`

Provider choice must remain isolated behind adapter/service interfaces. New
voice research may not change conversation or dashboard ownership boundaries.
