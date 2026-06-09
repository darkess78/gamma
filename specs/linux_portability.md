# Linux Portability Checklist

Goal: run the same Gamma repo and code on Linux with Linux-local config, without forking the application code.

## Rules

- Shared runtime code must not assume Windows-only paths such as `AppData`, `.exe`, or `pythonw.exe`.
- Machine-specific settings belong in `config/app.local.toml`, `config/voices.local.toml`, or `.env`.
- Shared repo defaults belong in `config/*.example.toml` and optional machine-agnostic `config/*.toml`.
- Platform-specific launcher wrappers are allowed, but shared services should resolve interpreters and paths dynamically.

## Current shape

- App config merges `app.example.toml -> app.toml -> app.local.toml`.
- Voice config merges `voices.example.toml -> voices.toml -> voices.local.toml`.
- Dashboard provider/profile edits write to the local override files instead of tracked config.
- Shared Python resolution prefers `SHANA_PYTHON`, `sys.executable`, and repo virtualenv paths before falling back to old Windows locations.
- Qwen TTS startup now works as a managed local sidecar on both Windows and Linux.
- Dataset GUI now defaults to the platform-local app data directory instead of hardcoded Windows `AppData`.

## Linux-specific prerequisites

- Python virtualenv for the Linux host
- `ffmpeg` and `ffprobe`
- PortAudio dev packages if using `sounddevice`
- ALSA tools if using `arecord` and `aplay`
- Tk packages if using the dataset GUI

## Deferred work

- Runtime verification on a Linux machine
- Any Linux-only bugs found during real mic, dashboard, or sidecar testing
