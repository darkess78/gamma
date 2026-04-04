# Gamma

Gamma is a Python-based assistant backend scaffold focused on conversational responses, selective memory, and a voice pipeline.

Current persona target: **Shana**.

## What it includes
- FastAPI backend with a conversation endpoint
- persona prompt assembly from editable files and config
- selective SQLite-backed memory
- pluggable LLM backends
- pluggable TTS backends
- file-based STT test path
- voice roundtrip and controller CLIs

## Current backend options

### LLM
- `mock` — safe development fallback
- `openai` — hosted model path
- `local` — Ollama-compatible local model path

### STT
- `stub` — sidecar transcript file for safe/local testing
- `faster-whisper` — file-based transcription

### TTS
- `stub` — local placeholder WAV output for end-to-end testing
- `openai` — hosted TTS via the OpenAI SDK
- `gpt-sovits` — HTTP-backed custom voice integration target

## What works today
- `POST /v1/conversation/respond`
- `GET /v1/memory/stats`
- persona prompt construction
- profile + episodic memory persistence
- session-scoped episodic memory retrieval
- optional TTS on conversation responses
- STT file testing
- voice roundtrip testing
- turn-based voice controller scaffold

## Project layout
- `gamma/main.py` — FastAPI app entrypoint
- `gamma/api/routes.py` — API routes
- `gamma/conversation/service.py` — main conversation pipeline
- `gamma/memory/service.py` — SQLite-backed memory service
- `gamma/llm/` — model adapters
- `gamma/voice/` — STT, TTS, and controller logic
- `config/` — runtime/persona/memory configuration
- `specs/` — project notes and architecture docs

## Quick start

```bash
cd gamma
cp .env.example .env
python -m venv .venv
./.venv/bin/pip install -e .
```

Then edit `.env` for the provider setup you want.

## Example environment

```env
RIKO_LLM_PROVIDER=mock
RIKO_STT_PROVIDER=stub
RIKO_TTS_PROVIDER=stub
RIKO_MEMORY_ENABLED=true
RIKO_MEMORY_WRITE_MODE=selective
```

## Run the API

```bash
cd gamma
./.venv/bin/uvicorn gamma.main:app --reload
```

## Test the conversation endpoint

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond \
  -H 'Content-Type: application/json' \
  -d '{"user_text":"Remember that I like jasmine tea.","session_id":"demo","synthesize_speech":false}'
```

## Voice / STT / TTS test commands

```bash
cd gamma
./.venv/bin/python -m gamma.run_stt_test test_audio/jfk.flac
./.venv/bin/python -m gamma.run_voice_roundtrip test_audio/jfk.flac --skip-tts
./.venv/bin/python -m gamma.run_tts_test "Gamma TTS smoke test"
./.venv/bin/python -m gamma.run_voice_mode --mode turn-based --seconds 5
```

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
