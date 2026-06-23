# Audio Understanding

Status: Current
Last verified: 2026-06-22

Audio understanding is optional context layered beside STT. It must never make
a normal voice turn depend on large model availability.

## Current Pipeline

- decode supported input into bounded mono 16 kHz PCM
- calculate lightweight signal/prosody features
- optionally call speaker-emotion and audio-event providers
- normalize labels, confidence, timestamps, duration, merging, and cooldown
- return typed audio context and timing metadata
- add prompt context only when explicitly enabled and confidence-gated

## Providers And Runtime

- disabled providers are the default
- optional Hugging Face SUPERB emotion and AST AudioSet adapters exist
- a loopback-only FastAPI sidecar supports persistent model placement
- emotion and audio-event models have independent device settings
- Shana may start the sidecar only when an explicit local endpoint is configured
- sidecar failure falls back to lightweight analysis without failing the turn

## Privacy And Operations

- operational logs exclude audio bytes, transcript text, and temporary paths
- uploaded audio is temporary
- sound-only live turns may emit normalized `audio_event` stream inputs without forcing speech
- model placement remains optional experimental work documented by `resource_routing.md`
