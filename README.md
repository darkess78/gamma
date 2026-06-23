# Gamma

Gamma is the runtime for **Shana**, a persistent assistant with text and live
voice conversation, a stable persona, selective memory, and an optional
streamer control plane. The everyday interaction room is **Monitor**, which is
designed to remain open in a dedicated tab or window for persistent output.

## Runtime shape

Gamma intentionally runs two applications:

| Application | Default port | Owns |
| --- | ---: | --- |
| Shana API | 8000 | conversation, memory, voice inference, vision, stream decisions, performer state |
| Dashboard | 8001 | browser authentication, Monitor and operator clients, process controls, machine status, local configuration |

Browser clients call the dashboard, and the dashboard calls Shana through her
HTTP API. The dashboard does not construct Shana's conversation, memory, or
voice services.

The authoritative deployment and proxy contract is
[`specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`](specs/LOCKED_GAMMA_NETWORK_DEPLOYMENT.md).
Do not change deployment routing based only on this overview.

## Install

Gamma requires Python 3.11 or newer. Use the repository virtual environment.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

The base install contains the two web applications, local Ollama/Piper/Qwen
HTTP integration, memory, stream logic, and dashboard. Install only the
optional capabilities used on the machine:

| Extra | Install | Adds |
| --- | --- | --- |
| Hosted providers | `.venv/bin/python -m pip install -e '.[hosted]'` | OpenAI LLM, STT, and TTS adapter |
| Local microphone/STT | `.venv/bin/python -m pip install -e '.[local-voice]'` | Faster-Whisper and `sounddevice` |
| Desktop tray | `.venv/bin/python -m pip install -e '.[desktop]'` | Pillow and pystray |
| Discord ingestion | `.venv/bin/python -m pip install -e '.[discord]'` | discord.py worker |
| Audio understanding | `.venv/bin/python -m pip install -e '.[audio-understanding]'` | Hugging Face model support; see the domain spec for PyTorch setup |

Extras can be combined:

```bash
.venv/bin/python -m pip install -e '.[dev,local-voice,hosted]'
```

Local media and microphone paths may also require `ffmpeg`, PortAudio, or ALSA
packages supplied by the operating system.

## Configure

Portable defaults are tracked; machine paths and secrets are not. App
configuration precedence is:

```text
config/app.example.toml
config/app.toml
config/app.local.toml
.env
process environment
```

Voice-profile precedence is:

```text
config/voices.example.toml
config/voices.presets.toml
config/voices.toml
config/voices.local.toml
```

Use `config/app.local.toml`, `config/voices.local.toml`, and `.env` for
machine-specific paths, GPU placement, credentials, and secrets. They are
ignored by Git.

Provider names can be selected independently:

| Domain | Hosted | Local |
| --- | --- | --- |
| LLM | `openai` | `ollama` / `local`, or `mock` for tests |
| STT | `openai` | `faster-whisper` / `local`, or `stub` |
| TTS | `openai` | `piper`, `qwen-tts`, or `stub` |

RVC is an optional post-process on generated WAV audio, not a TTS provider.
Keep it disabled for the normal low-latency conversation path.

## Run

Start the managed applications:

```bash
.venv/bin/python -m gamma.supervisor.cli start all
.venv/bin/python -m gamma.supervisor.cli status all
```

Then open:

- `http://127.0.0.1:8001/dashboard/monitor` for persistent text and output playback
- `http://127.0.0.1:8001/dashboard/live` for microphone/live-voice tuning
- `http://127.0.0.1:8001/dashboard` for operations and configuration
- `http://127.0.0.1:8000/health` for Shana health

Lifecycle commands are scoped and safe:

```bash
.venv/bin/python -m gamma.supervisor.cli restart shana
.venv/bin/python -m gamma.supervisor.cli restart dashboard
.venv/bin/python -m gamma.supervisor.cli stop all
```

Browser microphone access requires HTTPS or localhost. Public URLs and bind
addresses are separate settings; never use `0.0.0.0` as a public hostname.

## Interaction surfaces

`/dashboard/monitor` is the persistent owner-facing room for performer output,
speech playback, and local text input. Keep it open in a dedicated tab/window
while using other dashboard pages. `/dashboard/live` remains the microphone
and live-voice diagnostic surface. Legacy Talk URLs redirect to Monitor.

The wider dashboard retains:

- Presence lifecycle modes: sleep, wake, go live, and break
- provider and process controls
- memory and known-person management
- Twitch, EventSub, Discord, stream replay, traces, and safety views
- performer monitor and subtitle output views
- local configuration, logs, timings, and resource status

The raw Shana API can use optional bearer authentication. The dashboard has
separate session authentication and should be authenticated before network
exposure.

## Useful checks

```bash
.venv/bin/python -m gamma.run_llm_test
.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
.venv/bin/python -m gamma.run_tts_test "Gamma TTS smoke test"
.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac --skip-tts
```

Direct text API check:

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond \
  -H 'Content-Type: application/json' \
  -d '{"user_text":"Hello, Shana.","session_id":"demo","synthesize_speech":false}'
```

## Development

Source uses a `src/` package layout. Imports must resolve to `src/gamma`, not
the stale untracked top-level `gamma/` bytecode directory.

```bash
.venv/bin/python -c 'import gamma; print(gamma.__file__)'
.venv/bin/python -m pytest -q
```

For dashboard JavaScript changes, run `node --check` on every changed `.js` or
`.mjs` file. Before committing, run `git diff --check`.

Key directories:

- `src/gamma/` — Shana, dashboard, and shared runtime code
- `tests/` — unit and contract tests; integration tests are opt-in
- `config/` — layered portable configuration
- `scripts/` — launch and service helpers
- `deploy/` — Nginx and systemd templates
- `specs/` — product, architecture, current behavior, and domain contracts
- `data/` — ignored runtime state, logs, generated audio, models, and databases

Start with [`specs/README.md`](specs/README.md), then read
[`specs/current_implementations.md`](specs/current_implementations.md) and the
relevant domain spec. Future work is ordered in
[`specs/roadmap.md`](specs/roadmap.md).
