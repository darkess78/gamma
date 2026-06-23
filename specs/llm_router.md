# LLM Routing

Status: Current with experimental extensions
Last verified: 2026-06-22

## Providers

- `mock` for safe tests
- `openai` for hosted generation and vision
- `local`/`ollama` for Ollama-compatible generation

Adapters implement the shared LLM interface and accept optional call context,
model override, endpoint override, and image inputs.

## Router Behavior

The router classifies calls by purpose rather than inspecting arbitrary prompt
content for agentic behavior. Current families include persona conversation,
heavy persona work, lightweight chat, metadata/tagging, voice helpers, tool
finalization, and vision.

Routing considers:

- configured profile and default provider/model
- fast/light input thresholds
- persona-sensitive fallback restrictions
- provider capability, especially vision
- provider failure backoff
- optional hosted escalation
- optional resource-routing endpoint advice

Every routed call records provider, model, purpose, fallback attempts, timing,
failure/backoff state, and optional placement metadata.

## Product Rules

- the primary model remains responsible for persona and orchestration
- specialist/light models are subordinate helpers
- routing failure must fall back predictably or return a clear service error
- experimental resource placement may not silently change provider/model policy
- new model families require route and fallback tests rather than dashboard conditionals
