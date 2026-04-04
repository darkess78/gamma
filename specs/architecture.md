# Architecture Spec

## Top-level components
- backend API
- conversation orchestration
- voice input (STT)
- voice output (TTS)
- memory layer
- avatar event bridge
- tool system

## Rule
Adapters must be swappable.
Changing provider/model/language should mainly affect config + adapter code, not the whole system.

## Orchestration rule
The primary GPT brain owns conversation and orchestration.
Specialists like Codex or local worker models may be called later for narrow tasks, but they should remain subordinate helpers.

## Interfaces to preserve
- `llm/base.py`
- `voice/stt.py`
- `voice/tts.py`
- `memory/service.py`
- `tools/base.py`
