# Platform

Status: Current
Last verified: 2026-11-14

Gamma runs on Gamma PC (Linux). All processing happens there: Shana's API on
:8000, the dashboard on :8001, model inference and voice sidecars. You control
Shana from a browser through the dashboard (`https://gamma.neety.me`).

Windows support was removed on 2026-11-14: the Windows launcher, `msvcrt` lock
branches, `CREATE_NO_WINDOW` process flags and Windows virtualenv paths. Locks
use `fcntl`; subprocesses start in their own session. Run the test suite on
Gamma PC or another Linux machine.

The TTS dataset preparation tool (`run_tts_dataset_gui`, `run_prepare_tts_dataset`
and `helper_projects/GammaTTSDataPrep`) is a separate desktop helper and still
packages for Windows; see [voice_dataset.md](voice_dataset.md).

## Configuration

- App config merges `app.example.toml -> app.toml -> app.local.toml`.
- Voice config merges `voices.example.toml -> voices.toml -> voices.local.toml`.
- Machine-specific settings belong in `config/app.local.toml`,
  `config/voices.local.toml` or `.env`; dashboard edits write to the local files.
- Python resolution prefers `SHANA_PYTHON`, `sys.executable`, then repo
  virtualenvs (`.venv/bin/python`).

## Service ownership on Gamma PC

- Lingering `gamma-shana.service` and `gamma-dashboard.service` user units start
  both apps at boot. They set `GAMMA_LIFECYCLE_MANAGER=systemd-user`, so dashboard
  Shana controls call `systemctl --user`; the dashboard never stops itself.
- The supervisor (`gamma.supervisor`) starts module workers (Twitch, Discord) and
  voice sidecars, and manages Shana directly only when not under systemd.

## Prerequisites

- Python virtualenv, `ffmpeg` and `ffprobe`
- `bwrap`, `prlimit` and `systemd-run` for sandboxed practice and coding checks
- PortAudio or ALSA tools only for local microphone capture

## Dashboard login

Reset the login on Gamma PC from the Gamma checkout:

```bash
.venv/bin/python -m gamma.dashboard.reset_password --restart
```

It asks for the new password with hidden input (twice, at least 10 characters),
stores only a salted scrypt hash as `dashboard_auth_password_hash` in
`config/app.local.toml`, removes any plain `dashboard_auth_password`, and rotates
`dashboard_session_secret`, which signs out every browser. `--username NAME`
changes the login name; `--restart` restarts `gamma-dashboard.service`.
A plain `dashboard_auth_password` still works until the first reset.
