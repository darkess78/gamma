# Gamma

Gamma is a Python-based assistant backend scaffold focused on conversational responses, selective memory, and a voice pipeline.

Current persona target: **Shana**.

## What it includes
- FastAPI backend with a conversation endpoint
- image-aware conversation endpoint for multimodal vision
- persona prompt assembly from editable files and config
- selective SQLite-backed memory
- pluggable LLM backends
- pluggable TTS backends
- file-based STT test path
- voice roundtrip and controller CLIs

## Current backend options

### LLM
- `mock` - safe development fallback
- `openai` - hosted model path
- `local` / `ollama` - Ollama-compatible local model path

### STT
- `stub` - sidecar transcript file for safe/local testing
- `faster-whisper` / `local` - local transcription
- `openai` - hosted transcription via the OpenAI SDK

### TTS
- `stub` - local placeholder WAV output for end-to-end testing
- `piper` - local offline TTS for stable low-latency speech
- `local` / `gpt-sovits` - local HTTP-backed TTS via GPT-SoVITS
- `openai` - hosted TTS via the OpenAI SDK
- `gpt-sovits` - HTTP-backed custom voice integration target
- optional RVC post-process can be layered on top of Piper for slower converted-voice tests
- named voice profiles can be defined in `config/voices.toml` and selected from the dashboard

## Provider matrix

You can choose providers independently for each subsystem:

| System | Hosted | Local |
| --- | --- | --- |
| LLM | `openai` | `local` or `ollama` |
| STT | `openai` | `local` or `faster-whisper` |
| TTS | `openai` | `piper`, `local`, or `gpt-sovits` |

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

For low-latency local speech, prefer Piper as the default TTS path and keep RVC disabled during normal conversation. If you want a slower converted voice test, use:

```bash
python -m gamma.run_tts_test_henya "test phrase"
```

Smoke-test output modes:

```bash
python -m gamma.run_tts_test "test phrase"
python -m gamma.run_tts_test --compact "test phrase"
python -m gamma.run_tts_test --json "test phrase"
python -m gamma.run_tts_test_henya --compact "test phrase"
```

RVC layering:
- RVC is an optional post-process on top of generated WAV output; it is not a standalone TTS provider
- the intended local low-latency stack is `Piper -> optional RVC`
- keep `SHANA_RVC_ENABLED=false` for normal realtime conversation
- use `python -m gamma.run_tts_test_henya ...` when you want the slower Henya-converted path
- Gamma now auto-discovers an RVC checkout in common sibling locations such as `../RVC/Retrieval-based-Voice-Conversion-WebUI-main`
- Gamma also auto-discovers the RVC Python interpreter from an adjacent `.venv` when present
- `SHANA_RVC_MODEL_NAME` is still required; `SHANA_RVC_INDEX_PATH` is optional when Gamma can find a matching `.index`
- current Henya helper defaults:
  - model: `HenyaTheGeniusV2.pth`
  - f0 method: `rmvpe`
  - pitch: `12`
  - formant: `0.15`
  - index rate: `0.15`
  - rms mix rate: `0.2`
  - protect: `0.33`
- the offline Gamma integration uses the RVC file-conversion path, not the realtime GUI. That means only a subset of realtime GUI controls are available in Gamma.
- for Linux RVC bootstrapping, use `./scripts/install_rvc_linux.sh` after cloning the RVC repo into one of the expected locations
- named TTS voice profiles live in `config/voices.toml` and fall back to `config/voices.example.toml`

Dashboard behavior:
- the TTS profile dropdown lets you choose a named voice profile, not just a raw provider
- the TTS dropdown persists the selected provider to `config/app.toml`
- `Test TTS` uses the selected provider immediately
- normal Shana conversation responses still use the provider loaded by the running Shana process, so restart Shana after changing the dropdown if you want conversations to switch too
- dashboard TTS start/stop controls only apply to GPT-SoVITS
- the Providers panel shows whether RVC post-process is enabled for the running stack and which RVC model is selected

For local vision with Ollama, use a multimodal model and enable it explicitly:

```env
SHANA_LLM_PROVIDER=ollama
SHANA_LOCAL_LLM_MODEL=llama3.2-vision
SHANA_LOCAL_LLM_SUPPORTS_VISION=true
SHANA_LOCAL_LLM_VISION_MODEL=llama3.2-vision
```

Gamma now probes Ollama model capabilities through `POST /api/show` and expects the selected local vision model to report the `vision` capability.

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

Practical presets:

```env
# Balanced hosted setup
SHANA_LLM_MODEL=gpt-5.4-mini
SHANA_STT_MODEL=gpt-4o-mini-transcribe
SHANA_TTS_MODEL=gpt-4o-mini-tts
```

```env
# Higher-quality hosted voice stack
SHANA_LLM_MODEL=gpt-5.4
SHANA_STT_MODEL=gpt-4o-transcribe
SHANA_TTS_MODEL=tts-1-hd
```

```env
# Low-latency hosted setup
SHANA_LLM_MODEL=gpt-4.1
SHANA_STT_MODEL=gpt-4o-mini-transcribe
SHANA_TTS_MODEL=tts-1
```

OpenAI's current model catalog and audio guides:
- [Models](https://platform.openai.com/docs/models)
- [Speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text)
- [Text-to-speech guide](https://developers.openai.com/api/docs/guides/text-to-speech)

## What works today
- `POST /v1/conversation/respond`
- `POST /v1/conversation/respond-with-image`
- `POST /v1/vision/analyze`
- `GET /v1/memory/stats`
- persona prompt construction
- profile + episodic memory persistence
- session-scoped episodic memory retrieval
- optional TTS on conversation responses
- STT file testing
- voice roundtrip testing
- turn-based voice controller scaffold

## Platform notes
- Core backend code is path-portable and should run on Linux, macOS, or Windows with the right Python dependencies.
- The local LLM path assumes an Ollama-compatible endpoint, defaulting to `http://127.0.0.1:11434`.
- The mic voice loop is still environment-dependent:
  - Linux uses `arecord` for capture and tries `aplay` / `ffplay` / `play` for playback.
  - Windows now uses a PowerShell-based fallback path for microphone capture and playback.
  - If mic capture is flaky on a machine, the most reliable portable path is still the file-based STT / voice roundtrip flow.

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

<<<<<<< HEAD
From Bash or WSL:
=======
### Linux / macOS
>>>>>>> 39b8b22cba6d0a06adfad04104ad275be3874a82

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

<<<<<<< HEAD
From Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

This repo-local `.venv` is platform-specific. A `.venv` created from WSL/Bash is Linux-only and is not runnable from native Windows, and a Windows-created `.venv` is not runnable from WSL. Create it from the shell you plan to use and do not copy `.venv` between machines or operating systems. Then copy `.env.example` to `.env` and edit the provider settings you want.

After you activate `.venv`, run `python -m ...` and `python -m pip ...` normally. If you do not want to activate it, use `.venv/bin/python` on Bash or `.venv\Scripts\python.exe` on Windows for the same commands.

## Configuration precedence

Gamma reads checked-in TOML defaults first, then applies `.env` and process environment overrides.

Order of precedence:
1. process environment variables
2. values in `.env`
3. `config/app.toml` if present
4. `config/app.example.toml`
5. `config/models.toml`
6. `config/memory.toml`
7. hard-coded defaults in code

`config/models.toml` supplies provider/model defaults, `config/memory.toml` supplies memory defaults, and `config/persona.toml` plus the persona YAML files feed prompt construction. Create `config/app.toml` if you want file-based local overrides without putting them in `.env`.
=======
### Windows (PowerShell)

```powershell
cd gamma
copy .env.example .env
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

Then edit `.env` for the provider setup you want.
>>>>>>> 39b8b22cba6d0a06adfad04104ad275be3874a82

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
python -m uvicorn gamma.main:app --reload
```

<<<<<<< HEAD
## Dashboard and Background Services

Gamma now has two service layers:
- `shana` - the assistant API on port `8000`
- `dashboard` - the independent control dashboard on port `8001`

The preferred control path is via the shared Python launchers:

```bash
python scripts/open_gamma.py
python scripts/start_gamma_tray.py
python scripts/stop_services.py
```

Platform wrappers:

Linux:
```bash
chmod +x scripts/*.sh
./scripts/open_gamma_linux.sh
./scripts/start_gamma_tray_linux.sh
./scripts/stop_services_linux.sh
```

Windows:
```powershell
python .\scripts\open_gamma.py
python .\scripts\start_gamma_tray.py
python .\scripts\stop_services.py
```

Optional Windows Explorer helpers:
- `scripts/open_gamma_windows.cmd`
- `scripts/start_gamma_tray_windows.cmd`

Notes:
- the dashboard polls local service state and machine metrics
- supervisor-managed services run without Uvicorn access logs
- the tray app must be launched from the user’s actual desktop session to appear in the system tray
- on Linux, `pystray` tray support depends on the desktop environment / system tray implementation
- local STT is in-process with Shana and does not run as a separate background service today
- Piper TTS runs in-process when `SHANA_TTS_PROVIDER=piper`; there is no separate TTS daemon to start or stop
- local TTS is a managed GPT-SoVITS sidecar only when `SHANA_TTS_PROVIDER=local`, so starting or stopping Shana starts or stops that sidecar too
- Ollama remains external; Gamma health-checks it but does not manage its lifecycle yet

Linux GPT-SoVITS helpers:

```bash
chmod +x scripts/*.sh
./scripts/start_gpt_sovits_linux.sh
./scripts/stop_gpt_sovits_linux.sh
```

The Linux GPT-SoVITS scripts are driven by environment variables when needed:
- `GPT_SOVITS_ROOT` for the GPT-SoVITS package directory
- `GPT_SOVITS_PYTHON` for the Python executable to use
- `GPT_SOVITS_PORT` to override the default port `9881`

By default they log to:
- `data/runtime/gpt_sovits.stdout.log`
- `data/runtime/gpt_sovits.stderr.log`

## Linux Notes

Recommended Linux setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
chmod +x scripts/*.sh
python scripts/open_gamma.py
```

One-shot Linux host setup:

```bash
chmod +x scripts/install_gamma_linux.sh
./scripts/install_gamma_linux.sh
```

Optional Linux RVC setup:

```bash
chmod +x scripts/install_rvc_linux.sh
./scripts/install_rvc_linux.sh
```

The Linux RVC installer:
- searches common checkout locations such as `../RVC/Retrieval-based-Voice-Conversion-WebUI-main`
- creates or reuses an adjacent `.venv`
- installs the RVC requirements file, preferring `requirements-py311.txt`
- applies Gamma's offline CLI patch so `SHANA_RVC_FORMANT` works in the file-conversion path
- accepts `RVC_ROOT`, `RVC_VENV_DIR`, and `PYTHON_BIN` overrides when your layout is different

For service management, the repo now uses the same Python supervisor on both Windows and Linux:
- `python scripts/open_gamma.py`
- `python scripts/start_gamma_tray.py`
- `python scripts/stop_services.py`

The shell scripts in `scripts/*_linux.sh` are convenience wrappers around that shared path.

Linux caveats:
- tray support depends on your desktop environment exposing a system tray
- local audio behavior still depends on Linux audio tools and devices
- GPT-SoVITS Linux startup assumes a working package checkout/runtime outside this repo or under `data/GPT-SoVITS`
- the Linux launcher path is now implemented, but this repo has only been runtime-verified from Windows in this session

Linux desktop entry templates are included in:
- `desktop/gamma-dashboard.desktop`
- `desktop/gamma-tray.desktop`

Copy and adjust those into `~/.local/share/applications/` if you want launcher integration.

Optional Linux systemd templates are included in:
- `deploy/systemd/gamma-shana.service`
- `deploy/systemd/gamma-dashboard.service`

They assume the repo lives at `~/gamma` and the virtualenv lives at `~/gamma/.venv`. Adjust the paths before installing them.

## Dashboard Auth

Dashboard auth is optional for local testing and should be enabled before you expose the dashboard through a reverse proxy.

Relevant settings:

```env
SHANA_DASHBOARD_AUTH_ENABLED=true
SHANA_DASHBOARD_AUTH_USERNAME=admin
SHANA_DASHBOARD_AUTH_PASSWORD=change-me
SHANA_DASHBOARD_SESSION_SECRET=replace-with-a-long-random-secret
SHANA_DASHBOARD_COOKIE_SECURE=true
```

Notes:
- leave auth disabled for local Windows testing if you want the current open behavior
- set `SHANA_DASHBOARD_COOKIE_SECURE=true` when serving the dashboard over HTTPS
- the built-in login protects the dashboard app and its `/api/*` endpoints
- for public exposure, still put the dashboard behind HTTPS on your reverse proxy

## API Auth

The raw Shana API on port `8000` can also be protected with an optional bearer token.

Relevant settings:

```env
SHANA_API_AUTH_ENABLED=true
SHANA_API_BEARER_TOKEN=replace-with-a-long-random-token
```

Notes:
- when enabled, requests to `/v1/*` require `Authorization: Bearer <token>`
- `/health` remains open for simple service checks
- the dashboard automatically uses this token for its internal Shana status probe when configured in the same `.env`

=======
### Windows (PowerShell)

```powershell
cd gamma
.\.venv\Scripts\uvicorn gamma.main:app --reload
```

>>>>>>> 39b8b22cba6d0a06adfad04104ad275be3874a82
## Test the conversation endpoint

### curl

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond \
  -H 'Content-Type: application/json' \
  -d '{"user_text":"Remember that I like jasmine tea.","session_id":"demo","synthesize_speech":false}'
```

<<<<<<< HEAD
Test the image-aware conversation endpoint:

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond-with-image \
  -F "user_text=What is happening in this image?" \
  -F "session_id=vision-demo" \
  -F "synthesize_speech=false" \
  -F "image_file=@test_image.png"
```

Notes:
- image understanding currently requires `SHANA_LLM_PROVIDER=openai`
- supported upload types are `image/jpeg`, `image/png`, `image/webp`, and `image/gif`
- uploads are cached under `data/images/`
- local vision is also supported when `SHANA_LLM_PROVIDER=local` or `ollama`, `SHANA_LOCAL_LLM_SUPPORTS_VISION=true`, and the configured Ollama model accepts images

Structured vision analysis endpoint:

```bash
curl -X POST http://127.0.0.1:8000/v1/vision/analyze \
  -F "user_text=Read the screen and tell me the important parts." \
  -F "image_file=@test_image.png"
```

The structured response includes:
- image type classification
- scene summary
- OCR-style visible text extraction
- key text blocks for screenshots and documents
- interface elements for UI-heavy images
- document structure hints for headings/lists/tables
- likely user actions inferred from the screen or document
- notable objects
- spatial notes
- suggested follow-up questions
- overall confidence

=======
### Windows PowerShell

```powershell
Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8000/v1/conversation/respond `
  -ContentType 'application/json' `
  -Body '{"user_text":"Remember that I like jasmine tea.","session_id":"demo","synthesize_speech":false}'
```

>>>>>>> 39b8b22cba6d0a06adfad04104ad275be3874a82
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

<<<<<<< HEAD
Use `--no-tts` for the first microphone validation pass so you only test recording, transcription, and text response generation. `turn-based` waits for Enter before every capture. `always-listening` now starts automatically, waits for speech, and ends each utterance after trailing silence instead of using a fixed capture window. You can tune it with:
- `--silence-stop` for how much trailing silence ends an utterance
- `--max-seconds` for the longest single utterance
- `--speech-threshold` for how loud input must be before it is treated as speech
- `--speech-start` for how long speech must persist before a turn begins

Dashboard/service-control notes:
- `Start Shana` and `Stop Shana` control the assistant API plus the local GPT-SoVITS sidecar together
- `Test STT` validates the in-process STT path; there is no separate STT daemon to start or stop
- `Start TTS` and `Stop TTS` only apply to GPT-SoVITS
- when the active provider is Piper, OpenAI, or stub, the dashboard disables TTS start/stop and leaves `Test TTS` available

## Live Browser Voice

The dashboard browser voice path now has two modes:
- turn-based upload through `POST /api/voice/roundtrip`
- live half-duplex voice through `WebSocket /api/voice/live`

Live browser architecture:
- the browser streams mic PCM chunks to the dashboard WebSocket
- the dashboard owns silence detection, barge-in policy, and local playback
- Shana owns live turn execution through `/v1/voice/*`
- each finalized live turn runs in its own worker subprocess on the Shana side
- interrupt or barge-in cancels that worker process instead of only ignoring its result

Relevant live endpoints on Shana:
- `POST /v1/voice/transcribe`
- `POST /v1/voice/live/start`
- `GET /v1/voice/live/{turn_id}`
- `POST /v1/voice/live/{turn_id}/cancel`
- `POST /v1/voice/roundtrip`

Current live behavior:
- partial transcripts are best-effort snapshot updates, not true token-streaming ASR
- the dashboard live panel shows active turn id, worker pid, cancel reason, and cancel latency when available
- completed, cancelled, and failed live jobs are tracked by turn id
- hard-cancel currently applies to the dashboard live browser path only
- CLI voice modes still use their existing non-worker path

Operator wording:
- `interrupted` means the browser detected new speech and requested cancellation
- `cancelled` means the active live-turn worker was terminated or marked cancelled
- `discarded` means an old turn finished after interruption and its result was ignored

The current implementation is still phrase-based, not true streaming word-by-word transcription. On Windows, the mic controller records through `sounddevice` and plays WAV replies through `winsound`. On Linux it still prefers `arecord`/`aplay`, with `sounddevice` as a fallback when those binaries are unavailable.
=======
### Windows (PowerShell)

```powershell
cd gamma
.\.venv\Scripts\python -m gamma.run_stt_test test_audio\jfk.flac
.\.venv\Scripts\python -m gamma.run_voice_roundtrip test_audio\jfk.flac --skip-tts
.\.venv\Scripts\python -m gamma.run_tts_test "Gamma TTS smoke test"
.\.venv\Scripts\python -m gamma.run_voice_mode --mode turn-based --seconds 5
```
>>>>>>> 39b8b22cba6d0a06adfad04104ad275be3874a82

## GammaTTSDataPrep

A standalone GUI tool for preparing TTS training datasets from anime source media.

**Entry point:** `gamma/run_tts_dataset_gui.py`
**Spec:** `packaging/tts_dataset_gui.spec`
**Build script:** `packaging/build.bat`
**Built exe:** `dist/GammaTTSDataPrep/GammaTTSDataPrep.exe`

### Building the exe

Windows — double-click `packaging/build.bat` or run it from a terminal:

```bat
packaging\build.bat
```

Linux / macOS:

```bash
chmod +x packaging/build.sh
packaging/build.sh
```

Both scripts run PyInstaller against the spec using the repo `.venv` and print the output path when done. The full distribution lands in `dist/GammaTTSDataPrep/`.

- Windows: run `dist/GammaTTSDataPrep/GammaTTSDataPrep.exe`
- Linux: run `dist/GammaTTSDataPrep/GammaTTSDataPrep`

### Features

**Pipeline tab** — stage source media locally, run faster-whisper segmentation, and extract candidate speech clips into a reviewable dataset.

**Review tab** — listen to extracted clips, label them (Shana / Not Shana / Reject / noise tiers), rank by speaker similarity, find duplicates, trim clips, and export labeled subsets for training.

**Transcribe tab** — paste or browse to any media file (MKV, MP4, WAV, MP3, etc.) and get a full Whisper transcript without going through the full dataset pipeline. Supports multi-track containers (picks the right audio stream via language tag), optional timestamps, and copy/save of the result.

### Dependencies

Requires `ffmpeg` and `ffprobe` on `PATH` for video file handling. The `demucs` package is optional — only needed if you enable the **Separate Vocals** option in the Pipeline tab.

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
