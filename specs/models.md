# Models Spec

## Primary model
- The primary conversation/character brain can be hosted (`openai`) or local (`local` / `ollama`).

## Responsibilities of the primary model
- conversation
- personality consistency
- planning
- memory-aware replies
- deciding when helper tools/models should be used

## Specialist helper model
- **Codex** may be added later as a coding/helper specialist.

## Responsibilities of Codex
- code generation
- debugging
- refactoring
- implementation suggestions
- technical subproblem solving

## Local/background models
Local models may be added later for:
- summarization
- memory cleanup
- tagging
- retrieval preparation
- offline/background batch work

## Adapter policy
Phase 1 should support at least:
- a real GPT/OpenAI adapter
- a selectable local-model adapter path, with Ollama compatibility
- a mock adapter for development fallback

## Rule
The primary conversation brain remains the orchestrator.
Helpers do not become the center of the architecture.
