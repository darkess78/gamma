# Gamma — LLM Handoff Prompt (Lite)

Quick orientation for the current repo state. For full context, see [specs/llm-handoff-prompt.md](/home/neety/.openclaw/workspace/gamma-main/specs/llm-handoff-prompt.md).

## What Gamma Is

Gamma is a local Shana assistant stack with:

- FastAPI assistant backend
- separate dashboard app
- browser live voice and voice roundtrip
- local memory
- pluggable STT / TTS / LLM backends
- optional dataset-prep tooling

Current practical focus is the live assistant/dashboard workflow, not the old dataset-prep-first roadmap.

## Important Files

- [gamma/dashboard/static/dashboard.js](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/dashboard.js)
  Browser voice UI, subtitles, mute buttons, memory-delete modal.

- [gamma/dashboard/static/index.html](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/static/index.html)
  Dashboard structure and inline button hooks.

- [gamma/dashboard/main.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/main.py)
  Dashboard API routes.

- [gamma/dashboard/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/dashboard/service.py)
  Status payloads, provider actions, memory actions.

- [gamma/memory/models.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/models.py)
  Memory row models, including `created_at`.

- [gamma/memory/service.py](/home/neety/.openclaw/workspace/gamma-main/gamma/memory/service.py)
  Memory persistence, recent listing, targeted deletion.

- [gamma/voice/stt.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/stt.py)
  Local STT path is faster-whisper.

- [gamma/voice/tts.py](/home/neety/.openclaw/workspace/gamma-main/gamma/voice/tts.py)
  TTS provider selection and synthesis.

- [gamma/config.py](/home/neety/.openclaw/workspace/gamma-main/gamma/config.py)
  Settings, including dashboard public-vs-bind URL handling.

## Current Operational Facts

- Dashboard runs internally on port `8001`
- HTTPS is expected to be terminated by a reverse proxy
- public dashboard URL is separate from bind port
- browser live voice currently works
- browser capture still uses deprecated `ScriptProcessorNode`
- dashboard now has:
  - mute mic
  - mute Shana
  - pop-out subtitles
  - latest memories
  - recent-memory selection delete

## Memory Notes

Current safe UI behavior:

- no one-click full memory wipe in the normal dashboard path
- recent memory cleanup should be previewed and selective
- recent memory is time-based now that rows carry `created_at`

## Platform Notes

Preserve both Linux and Windows compatibility.

Do not:

- hardcode Windows-only paths in shared runtime code
- merge proxy-facing public port with internal dashboard bind port
- regress the Linux-hosted / Windows-browser flow that currently works

## Validation

Use:

```bash
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m gamma.supervisor.cli restart dashboard
```

Useful smoke tests:

```bash
./.venv/bin/python -m gamma.run_llm_test "Dashboard LLM smoke test."
./.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
./.venv/bin/python -m gamma.run_tts_test "Dashboard TTS smoke test."
./.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac
```
