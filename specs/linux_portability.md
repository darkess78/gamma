# Linux Portability Checklist

Status: Current
Last verified: 2026-07-14

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
- The production Linux host enables lingering `gamma-shana.service` and
  `gamma-dashboard.service` user units under `default.target`, so both apps
  start at boot before login. The units set
  `GAMMA_LIFECYCLE_MANAGER=systemd-user`; dashboard Shana controls then call
  `systemctl --user`, while non-systemd launches retain supervisor behavior.
- The boot-owned dashboard is treated as always on: the operator page does not
  expose a dashboard self-stop button, and combined runtime stops preserve it.
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
