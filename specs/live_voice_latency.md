# Live voice latency, measured 2026-11-14

Timings are from real turns on Gamma PC, using a throwaway copy of the
deployment with an empty `data/`. The input was three spoken questions made with
her TTS. Speech-to-text was `small.en` on the 3060 Ti, the main model was
`gpt-oss:20b` on the 3090, and TTS was the Qwen sidecar with her unchanged
profile.

Each figure is the time from the request to her first audio.

| Setup | First audio | Main model draft |
|---|---|---|
| As deployed: one model per Ollama instance, reviewer unloads after each check | 10.7–15.3 s, plus about 1.4 s worker start-up | 5.8–8.8 s |
| Both models resident on the 3090 | 4.6–5.6 s, plus start-up | 2.4–6.1 s |
| Plus low reasoning effort for real-time turns | 4.1–5.5 s, plus start-up | 1.8–2.1 s |
| Plus a standby worker | **4.2–4.6 s** from the request | 1.5–1.7 s |

Where the remaining time goes, for a typical short reply:

| Stage | Time |
|---|---|
| Speech-to-text | 0.3 s |
| Audio understanding | 0.1 s |
| Main model draft | 1.5–2 s |
| TTS, first chunk | 1.8–2.5 s (occasional outliers up to about 6 s, when the model ends late) |

Changes:
- **Safety reviewer keep-alive.** It follows `local_llm_keep_alive` instead of `0`. See `safety_review_benchmark.md`.
- **`local_llm_fast_reasoning = "low"`.** gpt-oss gets `think: low` on fast-mode turns (voice, stream, Discord) and on light helper calls. Text chat and heavy turns keep the default. The replies stayed the same length and quality.
- **Standby worker** (`live_voice_standby_worker`, `live_voice_standby_max_age_seconds`):
  - One worker loads STT and the services, then waits on stdin for the next turn.
  - Each turn is still its own process, so cancelling a turn still kills its process.
  - A new standby starts only after a turn has finished, so two STT models never sit on the 3060 Ti at once.
  - A standby is replaced if the TTS or STT selection or a config file changes, or after 300 s.
- **Fixed:** GPU STT made the job manager call a missing `settings._as_int`, so every dashboard live voice turn failed to start.

Needs the owner's OK (deployment):
- `OLLAMA_MAX_LOADED_MODELS=2` on `ollama-3090`, so the reviewer and helper models stop evicting `gpt-oss`. Together they use about 17 of the 3090's 24 GB.

Not done:
- **Streaming the LLM sentence by sentence.** In fast mode, voice replies are usually a single sentence. TTS already runs per chunk and the first chunk plays while later ones synthesize, so streaming would only help long replies. The draft is also a structured JSON turn, which is hard to stream safely.
- **Caching the voice-clone prompt in the TTS sidecar.** In x-vector mode it is only a short speaker-embedding pass, so the gain is small, and the change needs a sidecar restart and a voice re-check.
