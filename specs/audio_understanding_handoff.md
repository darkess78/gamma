# Audio Understanding Implementation Handoff

## Status

As of June 15, 2026, Gamma can attach speaker-affect observations and selected
non-speech events to voice input. The implementation, sidecar, isolated CUDA
runtime, model downloads, and automated tests are complete. Prompt influence
remains disabled pending evaluation on representative microphone recordings.

## Implemented Runtime

- `VoiceInputContext` carries prosody, model emotion, audio events, timing, and
  analyzer diagnostics.
- Audio is normalized to bounded mono 16 kHz PCM before analysis.
- The speaker-emotion backend uses `superb/wav2vec2-base-superb-er`.
- The event backend uses `MIT/ast-finetuned-audioset-10-10-0.4593` and maps
  AudioSet labels to Gamma's laughter, cough, and clapping event vocabulary.
- Live voice, transcription, and roundtrip results expose audio context.
- Event-only live turns can be recorded without forcing an assistant reply.
- Model failures are fail-open and preserve the existing STT path.
- A loopback-only FastAPI sidecar keeps both Hugging Face models resident.
- The supervisor recognizes the `audio-understanding` service and prefers
  `.venv-audio/bin/python` for it.
- Structured sidecar logs retain startup/shutdown, preload, request timing, and
  provider/decode/remote failure evidence with tracebacks. They exclude raw
  audio, transcript text, and temporary request paths.

The design source is `audio_understanding_plan.md`. Sidecar topology and
placement decisions are in `audio_understanding_deployment_proposal.md`.

## Current Machine State

The isolated `.venv-audio` runtime uses Python 3.12 and CUDA PyTorch
`2.11.0+cu128`. Both model repositories are already cached locally.

The repeatable probe was run at 07:34 MDT on June 15, 2026 with both models on
GPU 1, an RTX 3060 Ti:

- 1,020 MiB allocated to the sidecar process
- 1,041 MiB total device usage and 6,800 MiB free after inference
- 5.0 seconds to start the process and preload both cached models
- 429 ms client elapsed for the first inference request
- 117-124 ms client elapsed and 112-118 ms sidecar time when warm
- warm model stages of approximately 9 ms for emotion and 68 ms for events

GPU 0 is an RTX 3090 intended to retain headroom for Ollama. The latest
observation before this handoff found no Ollama model loaded and neither GPU
under material load. Shana and dashboard were running; the audio sidecar was
stopped after validation. The full machine-readable result is in the ignored
`data/runtime/audio-understanding-gpu-probe.json` runtime report.

## Repeatable Commands

Create or repair the isolated runtime:

```bash
.venv/bin/python scripts/setup_audio_understanding_env.py
```

Run a self-contained GPU 1 placement probe. It starts the sidecar with
environment overrides, performs three requests, writes a JSON snapshot under
ignored runtime data, and stops only the sidecar that it started:

```bash
.venv/bin/python scripts/probe_audio_gpu_placement.py --start-sidecar
```

The default report is:

```text
data/runtime/audio-understanding-gpu-probe.json
```

Use `--emotion-device cpu`, `--event-device cpu`, or another explicit CUDA
index to compare placements. Use `--audio-file PATH` for a representative
recording. The generated sine wave is suitable for runtime and memory checks,
not model-quality evaluation.

For manual operation:

```bash
.venv/bin/python scripts/start_audio_understanding_server.py
.venv/bin/python scripts/stop_audio_understanding_server.py
```

## Recommended Local Configuration

Do not put machine-specific placement in tracked shared configuration. The
current candidate for ignored `config/app.local.toml` is:

```toml
audio_understanding_endpoint = "http://127.0.0.1:9883/analyze"
speaker_emotion_provider = "huggingface"
audio_event_provider = "huggingface"
speaker_emotion_device = "cuda:1"
audio_event_device = "cuda:1"
audio_model_local_files_only = true
audio_understanding_prompt_enabled = false
```

Apply that configuration only after the combined-load measurement below.

## Next Work

1. Load the intended Ollama model on GPU 0 and the production STT/TTS models
   in their intended locations.
2. Run the placement probe with those workloads resident and capture peak VRAM
   during simultaneous STT, Qwen TTS, and audio analysis.
3. If GPU 1 is constrained, compare AST on CPU with emotion on GPU 1 before
   moving either model to GPU 0.
4. Build a small labeled evaluation set from consented Gamma microphone audio
   containing neutral speech, emotional speech, laughter, coughs, clapping,
   room noise, and common false-positive sources.
5. Tune thresholds from measured precision and recall. Keep
   `audio_understanding_prompt_enabled = false` until this evaluation passes.
6. Add dashboard diagnostics only after the runtime placement is stable.

## Validation Baseline

The latest complete validation before this handoff reported:

```text
276 passed, 2 skipped, 63 subtests passed
```

`git diff --check` also passed. Re-run the focused audio suites after any
placement or inference changes, followed by the full suite before release.
