# Voice

Status: Current
Last verified: 2026-08-30

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
- `speech_text` is the only conversation field eligible for TTS and spoken
  subtitles. `display_text` may be non-empty for a text-only response while
  `speech_text` remains empty; silent and deferred responses produce neither.
- `spoken_text` remains populated for compatible clients but is not a speech
  authorization boundary.

## Controllers

- file roundtrip and smoke-test CLIs
- turn-based and always-listening microphone controller modes
- `/dashboard/monitor` for persistent local text and output playback
- `/dashboard/live` for browser microphone and live-voice diagnostics
- browser live voice described in `live_voice.md`

Provider choice must remain isolated behind adapter/service interfaces. New
voice research may not change conversation or dashboard ownership boundaries.

Delivery resolution is deterministic: direct voice defaults to speech, direct
text defaults to text-only, Presence Wake requires an audio-ready listener for
speech, and public/ambient speech remains subject to Presence, output policy,
mute, safety, target, and budget gates. A tool result is never synthesized
directly; only the finalized `speech_text` may reach a TTS provider.
