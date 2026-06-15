# Audio Understanding Sidecar Deployment Proposal

## Status

Proposed internal deployment extension approved for implementation work on
June 15, 2026. This document does not modify or replace
`LOCKED_GAMMA_NETWORK_DEPLOYMENT.md`.

## Purpose

Keep speaker-emotion and audio-event models resident across live voice turns
without forcing them into the Shana process or reloading them in every
per-turn worker.

## Internal Topology

```text
Shana/live worker
  -> HTTP loopback request
  -> audio-understanding sidecar on 127.0.0.1:9883
  -> shared audio normalization
  -> resident speaker-emotion model
  -> resident audio-event model
```

The sidecar is internal only:

- no public Nginx route
- no dashboard-owned route
- no change to ports 8000, 8001, 8080, or 443
- bind host defaults to `127.0.0.1`

## GPU Placement

Emotion and event models have independent device settings:

```toml
speaker_emotion_device = "cpu"
audio_event_device = "cpu"
```

Supported placement examples:

- both CPU when VRAM is constrained
- emotion CPU and AST `cuda:1`
- both `cuda:1` when the secondary GPU has enough free VRAM
- either model on another explicit CUDA index

The main LLM/Ollama placement remains independent. The sidecar must not assume
GPU 0 is available.

## Operational Policy

- Providers remain disabled by default.
- A configured local endpoint allows the supervisor to start and stop the
  sidecar with Shana.
- The sidecar preloads enabled models once at startup.
- Shana fails open to local prosody when the sidecar is unavailable.
- Model caches and downloaded weights remain outside version control.
- `audio_model_local_files_only = true` is recommended after prefetching.

## Proposed Configuration

```toml
audio_understanding_endpoint = "http://127.0.0.1:9883/analyze"
audio_understanding_bind_host = "127.0.0.1"
audio_understanding_port = 9883

speaker_emotion_provider = "huggingface"
speaker_emotion_model = "superb/wav2vec2-base-superb-er"
speaker_emotion_device = "cpu"

audio_event_provider = "huggingface"
audio_event_model = "MIT/ast-finetuned-audioset-10-10-0.4593"
audio_event_device = "cpu"

audio_model_local_files_only = true
```

Machine-specific placement belongs in ignored `config/app.local.toml`.

## Remaining Validation

- test mixed placement against STT and Qwen TTS under load
- evaluate real microphone precision before prompt influence is enabled
- consider splitting emotion and event inference into separate sidecars if one
  combined process cannot satisfy placement or memory requirements

The exact current state, probe command, and resumable work queue are maintained
in `audio_understanding_handoff.md`.

## Current Machine Observation

Observed on June 15, 2026:

- GPU 0: NVIDIA GeForce RTX 3090, 24,576 MiB total, approximately 23,958 MiB free
- GPU 1: NVIDIA GeForce RTX 3060 Ti, 8,192 MiB total, approximately 7,828 MiB free
- Gamma `.venv` Torch build: `2.12.0+cpu`

The current Hugging Face sidecar therefore runs on CPU even though placement
settings accept CUDA devices.

An isolated `.venv-audio` runtime was subsequently created with:

- Python 3.12
- PyTorch `2.11.0+cu128`
- CUDA available with two visible devices
- Transformers and the minimal FastAPI sidecar dependencies

This environment is approximately 6.9 GB on disk and remains ignored by Git.
The main Gamma `.venv` remains unchanged.

## Measured GPU Placement

Both selected models were loaded on GPU 1, the RTX 3060 Ti:

- repeatable-probe sidecar allocation: 1,020 MiB
- total GPU 1 usage after inference: 1,041 MiB
- remaining GPU 1 VRAM after inference: 6,800 MiB
- sidecar startup and cached-model preload: approximately 5.0 seconds
- first inference request: approximately 429 ms client elapsed
- warm requests: approximately 117-124 ms client elapsed
- warm sidecar processing: approximately 112-118 ms

This placement leaves GPU 0 available for Ollama, but GPU 1 must still share
capacity with STT and Qwen TTS. Before enabling all three simultaneously,
measure their combined peak VRAM. If GPU 1 becomes constrained, practical
fallbacks are:

- keep emotion on GPU 1 and run AST on CPU
- move both audio models to CPU
- move one audio model to GPU 0 only when Ollama has sufficient headroom
- split emotion and events into separate sidecars if independent lifecycle and
  placement become necessary
