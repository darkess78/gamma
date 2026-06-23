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
- configured model context/persona capability metadata

`presence_wake` is a dedicated persona-sensitive, primary-quality route. Its
short event instruction never selects the light path by itself. Each fallback
receives a prompt rebuilt for that candidate's usable context budget, and
models below the required context/persona capability are skipped.

## Context Budgeting

Model capability records distinguish advertised context, configured usable
input, reserved output, safety margin, persona/vision/tool capability, and
provenance. Prompt assembly budgets the complete request for each candidate.
Persona, boundaries, privacy/safety rules, current input, and current speaker
are mandatory; working state, summary, memories, recent turns, and background
are compacted in priority order.

Provider context-overflow responses are typed separately from availability
failures. They do not create provider backoff. The router rebuilds once under
a smaller candidate budget and then selects only a compatible fallback.

Every routed call records provider, model, purpose, fallback attempts, timing,
prompt estimate, context limit/reserves, compaction, failure/backoff state, and
optional placement metadata.

## Product Rules

- the primary model remains responsible for persona and orchestration
- specialist/light models are subordinate helpers
- routing failure must fall back predictably or return a clear service error
- experimental resource placement may not silently change provider/model policy
- new model families require route and fallback tests rather than dashboard conditionals
