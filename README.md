# Gamma

Gamma is a Python-based assistant backend scaffold focused on conversational responses, selective memory, and a voice pipeline.

Current persona target: **Shana**.

## What it includes
- FastAPI backend with conversation and vision endpoints
- persona prompt assembly from editable files and config
- selective SQLite-backed memory
- pluggable LLM, STT, and TTS backends
- voice roundtrip, live voice, and controller CLIs
- dashboard and supervisor tooling

## Current backend options

### LLM
- `mock` - safe development fallback
- `openai` - hosted model path
- `local` / `ollama` - Ollama-compatible local model path

### STT
- `stub` - transcript file path for safe/local testing
- `faster-whisper` / `local` - local transcription
- `openai` - hosted transcription via the OpenAI SDK

### TTS
- `stub` - local placeholder WAV output for end-to-end testing
- `piper` - local offline TTS for stable low-latency speech
- `local` / `gpt-sovits` - local HTTP-backed GPT-SoVITS path
- `qwen-tts` - local HTTP-backed Qwen TTS path
- `openai` - hosted TTS via the OpenAI SDK
- optional RVC post-process can be layered on top of Piper for slower converted-voice tests
- named voice profiles load from the layered `config/voices*.toml` files and can be selected from the dashboard

## Provider matrix

You can choose providers independently for each subsystem:

| System | Hosted | Local |
| --- | --- | --- |
| LLM | `openai` | `local` or `ollama` |
| STT | `openai` | `local` or `faster-whisper` |
| TTS | `openai` | `piper`, `local`, `gpt-sovits`, or `qwen-tts` |

`local` does not mean the same backend everywhere:
- LLM `local` = Ollama-compatible chat model
- STT `local` = `faster-whisper`
- TTS `local` = GPT-SoVITS
- TTS `piper` = local offline ONNX voice synthesis

Examples:

```env
# Ollama LLM + OpenAI STT + OpenAI TTS
SHANA_LLM_PROVIDER=ollama
SHANA_STT_PROVIDER=openai
SHANA_TTS_PROVIDER=openai
```

```env
# OpenAI LLM + local STT + Piper TTS
SHANA_LLM_PROVIDER=openai
SHANA_STT_PROVIDER=local
SHANA_TTS_PROVIDER=piper
SHANA_PIPER_EXE=piper
SHANA_PIPER_MODEL_PATH=./data/piper/en_US-lessac-medium.onnx
SHANA_PIPER_CONFIG_PATH=./data/piper/en_US-lessac-medium.onnx.json
```

```env
# Fully local development
SHANA_LLM_PROVIDER=ollama
SHANA_STT_PROVIDER=local
SHANA_TTS_PROVIDER=stub
```

For low-latency local speech, prefer Piper as the default TTS path and keep RVC disabled during normal conversation. If you want a slower converted voice test, select a preset RVC-backed profile in the dashboard or set `SHANA_TTS_PROFILE=henya_rvc` before running a TTS smoke test.

Smoke-test output modes:

```bash
python -m gamma.run_tts_test "test phrase"
python -m gamma.run_tts_test --compact "test phrase"
python -m gamma.run_tts_test --json "test phrase"
```

RVC layering:
- RVC is an optional post-process on top of generated WAV output; it is not a standalone TTS provider
- the intended local low-latency stack is `Piper -> optional RVC`
- keep `SHANA_RVC_ENABLED=false` for normal realtime conversation
- use an RVC-backed voice profile such as `henya_rvc` when you want the slower converted path
- Gamma now auto-discovers an RVC checkout in common sibling locations such as `../RVC/Retrieval-based-Voice-Conversion-WebUI-main`
- Gamma also auto-discovers the RVC Python interpreter from an adjacent `.venv` when present
- `SHANA_RVC_MODEL_NAME` is still required; `SHANA_RVC_INDEX_PATH` is optional when Gamma can find a matching `.index`

Dashboard behavior:
- the TTS profile dropdown lets you choose a named voice profile, not just a raw provider
- named TTS voice profiles load from `config/voices.example.toml`, then `config/voices.presets.toml`, then `config/voices.toml`, then `config/voices.local.toml`
- the TTS dropdown persists machine-local selections to `config/app.local.toml`
- `Test TTS` uses the selected provider immediately
- conversation and live voice flows use the provider loaded by the running Shana process, so restart Shana after changing provider or profile if you want the active service to switch too
- dashboard TTS start/stop controls only apply to managed local sidecars such as GPT-SoVITS or Qwen TTS

For local vision with Ollama, use a multimodal model and enable it explicitly:

```env
SHANA_LLM_PROVIDER=ollama
SHANA_LOCAL_LLM_MODEL=llama3.2-vision
SHANA_LOCAL_LLM_SUPPORTS_VISION=true
SHANA_LOCAL_LLM_VISION_MODEL=llama3.2-vision
```

## OpenAI model guide

OpenAI models are configured independently in this repo:
- `SHANA_LLM_MODEL` controls the chatbot model
- `SHANA_STT_MODEL` controls speech-to-text
- `SHANA_TTS_MODEL` controls speech output

Recommended starting points:

| System | Model | Good fit |
| --- | --- | --- |
| LLM | `gpt-5.4-mini` | Best default for cost, speed, and quality balance |
| LLM | `gpt-5.4` | Best for the hardest multi-step reasoning or agentic tasks |
| LLM | `gpt-5.4-nano` | High-throughput simple classification, extraction, and routing |
| LLM | `gpt-4.1` | Strong non-reasoning model with long context and low-latency tool use |
| STT | `gpt-4o-mini-transcribe` | Best default for accurate hosted transcription at lower cost |
| STT | `gpt-4o-transcribe` | Higher-accuracy hosted transcription |
| STT | `gpt-4o-transcribe-diarize` | Hosted transcription when speaker labeling matters |
| STT | `whisper-1` | Older compatibility option |
| TTS | `gpt-4o-mini-tts` | Best default hosted TTS model |
| TTS | `tts-1` | Fast speech generation |
| TTS | `tts-1-hd` | Higher-quality speech generation |

## What works today
- `POST /v1/conversation/respond`
- `POST /v1/conversation/respond-with-image`
- `POST /v1/vision/analyze`
- `GET /v1/memory/stats`
- optional TTS on conversation responses
- STT file testing
- voice roundtrip testing
- dashboard/browser live voice

## Platform notes
- The goal is one repo and one codebase with machine-local config.
- Shared code should run on Linux, macOS, or Windows with the right Python and system dependencies.
- Local service wrappers are intentionally split by platform where needed, but shared runtime paths should not assume Windows-only paths.
- The mic voice loop is still environment-dependent:
  - Linux prefers `arecord` for capture and `aplay` for playback, with `sounddevice` fallback.
  - Windows uses `sounddevice`/`winsound`.
  - Browser voice through the dashboard is the most portable interactive path.

## Project layout
- `gamma/main.py` - FastAPI app entrypoint
- `gamma/api/routes.py` - API routes
- `gamma/conversation/service.py` - main conversation pipeline
- `gamma/memory/service.py` - SQLite-backed memory service
- `gamma/llm/` - model adapters
- `gamma/voice/` - STT, TTS, and controller logic
- `config/` - runtime, persona, and memory configuration
- `specs/` - project notes and architecture docs

## Quick start

### Linux / macOS

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

### Windows (PowerShell)

```powershell
Copy-Item .env.example .env
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

The repo-local `.venv` is platform-specific. Create it from the OS and shell you plan to use; do not copy `.venv` between Windows and Linux.

## Host Binding and LAN Access

Gamma now separates bind addresses from client-facing addresses:
- `SHANA_BIND_HOST` and `SHANA_DASHBOARD_BIND_HOST` control which interface Uvicorn listens on
- `SHANA_PUBLIC_HOST` and `SHANA_DASHBOARD_PUBLIC_HOST` control the URLs shown in the dashboard, tray, and helper pages

Default local-only setup:

```env
SHANA_BIND_HOST=127.0.0.1
SHANA_PUBLIC_HOST=127.0.0.1
SHANA_DASHBOARD_BIND_HOST=127.0.0.1
SHANA_DASHBOARD_PUBLIC_HOST=127.0.0.1
```

LAN setup on Linux:

```env
SHANA_BIND_HOST=0.0.0.0
SHANA_PUBLIC_HOST=192.168.1.50
SHANA_DASHBOARD_BIND_HOST=0.0.0.0
SHANA_DASHBOARD_PUBLIC_HOST=192.168.1.50
```

Use this machine's actual LAN IP for the `*_PUBLIC_HOST` values. Do not set them to `0.0.0.0`.

## Configuration model

Gamma now uses layered file config so you can keep the same repo and code on multiple machines with different local settings.

App config loads in this order:
1. `config/app.example.toml`
2. `config/app.toml`
3. `config/app.local.toml`

Voice profile config loads in this order:
1. `config/voices.example.toml`
2. `config/voices.presets.toml`
3. `config/voices.toml`
4. `config/voices.local.toml`

Then `.env` and process environment variables override file-based values.

Use the files like this:
- `config/*.example.toml`: shareable defaults and examples kept in git
- `config/voices.presets.toml`: repo presets for local testing voices that are useful but not canonical defaults
- `config/app.toml` and `config/voices.toml`: shareable repo defaults when they are machine-agnostic
- `config/app.local.toml` and `config/voices.local.toml`: machine-local overrides ignored by git
- `.env`: secrets and environment overrides

`config/models.toml` supplies provider/model defaults, `config/memory.toml` supplies memory defaults, and `config/persona.yaml` is the editable structured persona source used during prompt construction.

## Example environment

```env
SHANA_LLM_PROVIDER=mock
SHANA_STT_PROVIDER=stub
SHANA_TTS_PROVIDER=stub
SHANA_MEMORY_ENABLED=true
SHANA_MEMORY_WRITE_MODE=selective
```

For a local Ollama run, switch to:

```env
SHANA_LLM_PROVIDER=ollama
SHANA_LOCAL_LLM_ENDPOINT=http://127.0.0.1:11434
SHANA_LOCAL_LLM_MODEL=llama3.2:3b
SHANA_STT_PROVIDER=faster-whisper
SHANA_TTS_PROVIDER=stub
```

For a dual-GPU Linux machine, a sensible split is:

```env
SHANA_LLM_PROVIDER=ollama
SHANA_LOCAL_LLM_ENDPOINT=http://127.0.0.1:11434
SHANA_LOCAL_LLM_MODEL=gpt-oss:20b
SHANA_STT_PROVIDER=local
SHANA_STT_MODEL=base.en
SHANA_STT_DEVICE=cuda
SHANA_STT_DEVICE_INDEX=1
SHANA_STT_COMPUTE_TYPE=float16
```

This keeps the main Ollama model on the primary GPU and moves faster-whisper STT to GPU index `1`.

For an OpenAI-backed voice stack, use separate models for chat, speech-to-text, and speech output:

```env
OPENAI_API_KEY=your_key_here
SHANA_LLM_PROVIDER=openai
SHANA_LLM_MODEL=gpt-5.4-mini
SHANA_STT_PROVIDER=openai
SHANA_STT_MODEL=gpt-4o-mini-transcribe
SHANA_TTS_PROVIDER=openai
SHANA_TTS_MODEL=gpt-4o-mini-tts
SHANA_TTS_VOICE=alloy
```

## Run the API

### Linux / macOS

```bash
python -m uvicorn gamma.main:app --reload --host "${SHANA_BIND_HOST:-127.0.0.1}" --port "${SHANA_PORT:-8000}"
```

### Windows (PowerShell)

```powershell
python -m uvicorn gamma.main:app --reload
```

## Dashboard and Background Services

Gamma has two long-running services:
- `shana` on port `8000`
- `dashboard` on port `8001`

Use the shared Python launchers on both Linux and Windows:

```bash
python scripts/open_gamma.py
python scripts/start_gamma_tray.py
python scripts/stop_services.py
```

Platform wrappers also exist:
- Cross-platform Python launchers: `scripts/open_gamma.py`, `scripts/start_shana.py`, `scripts/start_dashboard.py`, `scripts/start_gamma_tray.py`, `scripts/stop_services.py`
- Linux convenience wrappers: `scripts/*_linux.sh`
- Windows convenience wrappers: `scripts/*_windows.cmd`

Notes:
- the dashboard polls local service state and machine metrics
- supervisor-managed services run without Uvicorn access logs
- shared background process launch now resolves the active interpreter from `SHANA_PYTHON`, the current process, repo virtualenvs, and platform-native fallbacks on both Windows and Linux
- local STT runs in-process with Shana
- Piper runs in-process and has no managed sidecar
- GPT-SoVITS and Qwen TTS can be managed as local sidecars when configured with local endpoints
- Ollama remains external; Gamma health-checks it but does not manage its lifecycle
- tray support on Linux depends on the desktop environment exposing a usable system tray and a graphical session with `DISPLAY` or `WAYLAND_DISPLAY`

For LAN access on Linux:
- set `SHANA_BIND_HOST=0.0.0.0` and `SHANA_DASHBOARD_BIND_HOST=0.0.0.0`
- set both `*_PUBLIC_HOST` values to the machine's LAN IP
- enable dashboard auth and API auth before exposing ports beyond localhost
- open firewall ports `8000` and `8001` only on trusted networks

## Linux host notes

Useful Linux helpers:

```bash
chmod +x scripts/*.sh
./scripts/install_gamma_linux.sh
./scripts/install_rvc_linux.sh
```

For local audio and media tooling on Linux, expect to install system packages in addition to Python deps:
- `ffmpeg` and `ffprobe`
- PortAudio development headers if you use `sounddevice`
- ALSA tools if you want `arecord` and `aplay`
- Tk packages if you want the dataset GUI

Templates for Linux integration:
- `desktop/gamma-dashboard.desktop`
- `desktop/gamma-tray.desktop`
- `deploy/systemd/gamma-shana.service`
- `deploy/systemd/gamma-dashboard.service`

## Dashboard Auth

Dashboard auth is optional for local testing and should be enabled before exposing the dashboard.

```env
SHANA_DASHBOARD_AUTH_ENABLED=true
SHANA_DASHBOARD_AUTH_USERNAME=admin
SHANA_DASHBOARD_AUTH_PASSWORD=change-me
SHANA_DASHBOARD_SESSION_SECRET=replace-with-a-long-random-secret
SHANA_DASHBOARD_COOKIE_SECURE=true
```

If you are serving plain HTTP on a home LAN, leave `SHANA_DASHBOARD_COOKIE_SECURE=false` until you terminate TLS in front of Gamma. Secure cookies are not sent over plain HTTP.

## API Auth

The raw Shana API on port `8000` can also be protected with an optional bearer token.

```env
SHANA_API_AUTH_ENABLED=true
SHANA_API_BEARER_TOKEN=replace-with-a-long-random-token
```

## Test the conversation endpoint

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond \
  -H 'Content-Type: application/json' \
  -d '{"user_text":"Remember that I like jasmine tea.","session_id":"demo","synthesize_speech":false}'
```

Test the image-aware conversation endpoint:

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond-with-image \
  -F "user_text=What is happening in this image?" \
  -F "session_id=vision-demo" \
  -F "synthesize_speech=false" \
  -F "image_file=@test_image.png"
```

Structured vision analysis endpoint:

```bash
curl -X POST http://127.0.0.1:8000/v1/vision/analyze \
  -F "user_text=Read the screen and tell me the important parts." \
  -F "image_file=@test_image.png"
```

From another PC on the same LAN, replace `127.0.0.1` with the Gamma machine's LAN IP, for example `http://192.168.1.50:8001/` for the dashboard and `http://192.168.1.50:8000/` for the API.

## Voice / STT / TTS test commands

### Linux / macOS

```bash
python -m gamma.run_stt_test test_audio/jfk.flac
python -m gamma.run_voice_roundtrip test_audio/jfk.flac --skip-tts
python -m gamma.run_tts_test "Gamma TTS smoke test"
python -m gamma.run_voice_mode --list-devices
python -m gamma.run_voice_mode --no-tts --mode turn-based --seconds 5
python -m gamma.run_voice_mode --mode turn-based --seconds 5
python -m gamma.run_voice_mode --mode always-listening --seconds 5
python -m gamma.run_voice_mode --mode always-listening --silence-stop 1.2 --max-seconds 20 --speech-threshold 0.015
```

### Windows (PowerShell)

```powershell
python -m gamma.run_stt_test test_audio\jfk.flac
python -m gamma.run_voice_roundtrip test_audio\jfk.flac --skip-tts
python -m gamma.run_tts_test "Gamma TTS smoke test"
python -m gamma.run_voice_mode --mode turn-based --seconds 5
```

Use `--no-tts` for the first microphone validation pass so you only test recording, transcription, and text response generation.

Dashboard/service-control notes:
- `Start Shana` and `Stop Shana` control the assistant API and any managed local TTS sidecar
- `Test STT` validates the in-process STT path
- `Start TTS` and `Stop TTS` only apply to managed sidecars
- when the active provider is Piper, OpenAI, or stub, the dashboard disables TTS start/stop and leaves `Test TTS` available

## Live Browser Voice

The dashboard browser voice path has two modes:
- turn-based upload through `POST /api/voice/roundtrip`
- live half-duplex voice through `WebSocket /api/voice/live`

Current live behavior:
- partial transcripts are best-effort snapshot updates, not true token-streaming ASR
- the dashboard live panel shows active turn id, worker pid, cancel reason, and cancel latency when available
- completed, cancelled, and failed live jobs are tracked by turn id
- hard-cancel currently applies to the dashboard live browser path only
- CLI voice modes still use their existing non-worker path
- the browser capture path currently uses `ScriptProcessorNode`; browsers warn that it is deprecated, so this should be migrated to `AudioWorkletNode` in a follow-up cleanup pass

The current implementation is still phrase-based, not true streaming word-by-word transcription. On Windows, the mic controller records through `sounddevice` and plays WAV replies through `winsound`. On Linux it prefers `arecord`/`aplay`, with `sounddevice` as a fallback when those binaries are unavailable.

## GammaTTSDataPrep

A standalone GUI tool for preparing TTS training datasets from anime source media.

**Entry point:** `gamma/run_tts_dataset_gui.py`  
**Spec:** `packaging/tts_dataset_gui.spec`  
**Build script:** `packaging/build.bat`

### Building

Windows:

```bat
packaging\build.bat
```

Linux / macOS:

```bash
chmod +x packaging/build.sh
./packaging/build.sh
```

Both scripts run PyInstaller against the spec using the repo `.venv` and print the output path when done. The full distribution lands in `dist/GammaTTSDataPrep/`.

- Windows binary: `dist/GammaTTSDataPrep/GammaTTSDataPrep.exe`
- Linux binary: `dist/GammaTTSDataPrep/GammaTTSDataPrep`

### Features

**Pipeline tab** - stage source media locally, run faster-whisper segmentation, and extract candidate speech clips into a reviewable dataset.

**Review tab** - listen to extracted clips, label them, rank by speaker similarity, find duplicates, trim clips, and export labeled subsets for training.

**Transcribe tab** - paste or browse to any media file and get a full Whisper transcript without going through the full dataset pipeline.

### Dependencies

Requires `ffmpeg` and `ffprobe` on `PATH` for video file handling. The `demucs` package is optional and only needed if you enable the **Separate Vocals** option in the Pipeline tab.

## Repository hygiene

The repo excludes local or heavyweight assets such as:
- `.env`
- `.venv/`
- `data/`
- `imagegen/`
- local databases
- generated audio artifacts

## Status

Gamma is still a scaffold/prototype. The architecture is in place, but parts of the live voice path and memory quality are still under active iteration.
