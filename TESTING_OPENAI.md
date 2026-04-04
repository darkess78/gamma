# Testing Gamma Paths

## Setup

```bash
cd gamma
cp .env.example .env
# then edit .env and add your real OpenAI key if needed
```

## Example `.env` values for OpenAI text + TTS

```env
RIKO_LLM_PROVIDER=openai
RIKO_LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=YOUR_KEY_HERE
RIKO_STT_PROVIDER=faster-whisper
RIKO_STT_MODEL=base.en
RIKO_STT_DEVICE=cpu
RIKO_STT_COMPUTE_TYPE=int8
RIKO_TTS_PROVIDER=openai
RIKO_TTS_MODEL=gpt-4o-mini-tts
RIKO_TTS_VOICE=alloy
RIKO_TTS_FORMAT=wav
RIKO_MEMORY_ENABLED=true
RIKO_MEMORY_WRITE_MODE=selective
```

## Example `.env` values for local text testing

```env
RIKO_LLM_PROVIDER=local
RIKO_LOCAL_LLM_ENDPOINT=http://127.0.0.1:11434
RIKO_LOCAL_LLM_MODEL=gpt-oss:20b
RIKO_STT_PROVIDER=faster-whisper
RIKO_STT_MODEL=base.en
RIKO_STT_DEVICE=cpu
RIKO_STT_COMPUTE_TYPE=int8
RIKO_TTS_PROVIDER=stub
RIKO_MEMORY_ENABLED=true
```

## Quick LLM import test

```bash
cd gamma
./.venv/bin/python - <<'PY'
from gamma.conversation.service import ConversationService
service = ConversationService()
reply = service.respond('Hey, who are you?')
print(reply.model_dump())
print(service.memory_stats())
PY
```

## TTS smoke test

```bash
cd gamma
./.venv/bin/python -m gamma.run_tts_test "Gamma TTS smoke test"
```

## STT file test

```bash
cd gamma
./.venv/bin/python -m gamma.run_stt_test /path/to/audio.wav
```

## API test

```bash
cd gamma
./.venv/bin/uvicorn gamma.main:app --reload
```

Then:

```bash
curl -X POST http://127.0.0.1:8000/v1/conversation/respond \
  -H 'Content-Type: application/json' \
  -d '{"user_text":"Remember that I like jasmine tea.","synthesize_speech":true}'
```

And inspect memory stats:

```bash
curl http://127.0.0.1:8000/v1/memory/stats
```
