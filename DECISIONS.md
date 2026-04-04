# Decisions Log

## 2026-03-11
- Project is being organized in a spec-first style.
- `specs/` is the source of truth for project intent.
- `config/` holds swappable runtime/provider choices.
- Phase 1 focuses only on the voice conversation loop.
- Manga-finder remains the main active project outside this repo.
- GPT is the default primary conversation brain.
- Codex is reserved as an optional coding/helper specialist, not the main personality model.
- Local models are intended for later background/support roles like summarization and memory prep.
- The system/project name is **Gamma**.
- The assistant persona name is **Shana**.
- Phase 1 now includes a local-first TTS integration point: `stub` TTS writes testable WAV files even when no external TTS provider is configured.
- Memory remains selective, but it is now concretely backed by SQLite so persona/runtime memory plumbing can be exercised before more advanced retrieval is built.
- The TTS layer should stay provider-backed behind a single service boundary so OpenAI, stub output, and future custom-voice backends (starting with GPT-SoVITS HTTP) can swap without disturbing the conversation layer.
- Near-term live voice should prefer a low-risk push-to-talk loop over premature streaming complexity, as long as the validated file-based path keeps working.
