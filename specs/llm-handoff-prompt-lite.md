# Gamma — LLM Handoff Prompt (Lite)

Quick orientation. Read this before working on anything. For full details see `specs/llm-handoff-prompt.md`.

---

## What This Is

**Gamma** = local Windows AI assistant with a Shana (Shakugan no Shana) persona.

**Active work** = `GammaTTSDataPrep` — a standalone Tkinter desktop app for building a Shana TTS training dataset from the source anime. Stages episodes locally, extracts speech clips via faster-whisper + ffmpeg, lets you manually review and label clips, exports curated subsets for GPT-SoVITS training.

The broader Gamma assistant (FastAPI backend, voice pipeline, dashboard, tray) is in `gamma/` but is **not the current focus**.

---

## Key Files

| File | Purpose |
|---|---|
| `gamma/run_tts_dataset_gui.py` | Main GUI app (~1700 lines) — all review, playback, export, pipeline controls |
| `gamma/run_prepare_tts_dataset.py` | CLI extraction: faster-whisper → ffmpeg → manifest.jsonl |
| `gamma/run_stage_and_prepare_tts_dataset.py` | Stage-from-share + optional prepare |
| `scripts/build_tts_dataset_gui_exe.py` | PyInstaller build script |
| `packaging/tts_dataset_gui.spec` | PyInstaller spec |
| `specs/voice_dataset.md` | Dataset tool docs (somewhat outdated) |
| `specs/llm-handoff-prompt.md` | Full context document |

---

## Data Paths (Windows)

| What | Path |
|---|---|
| App data root | `C:\Users\darke\AppData\Local\GammaTTSDataPrep\` |
| Staged episodes | `...\source_media\shakugan_no_shana\` |
| Dataset output | `...\shana_dataset\` |
| Clips | `...\shana_dataset\clips\<episode_id>\<clip_id>.wav` |
| Labels | `...\shana_dataset\labels.json` |
| Manifest | `...\shana_dataset\manifest.jsonl` |
| Exports | `...\shana_dataset\exports\shana_clean\`, `shana_light_noise\`, `shana_heavy_noise\` |
| Source anime (share) | `\\10.78.78.250\Media\Anime\Shakugan no Shana\Shakugan no Shana` |
| Exe | `dist\GammaTTSDataPrep\GammaTTSDataPrep.exe` |

---

## Labels

- `Shana` — clean core training audio
- `Shana-light-noise` — usable, minor contamination
- `Shana-heavy-noise` — contaminated, experimental only
- `Not Shana` — wrong speaker (used as anti-seeds in similarity ranking)
- `Reject` — unusable

Only `Shana` and `Shana-light-noise` should go into base GPT-SoVITS training.

---

## Current Dataset State

`clean=1, light=7, heavy=10` — **not training-ready**. Need ~100+ clean clips minimum for GPT-SoVITS.

---

## What Was Recently Added

1. Waveform canvas in review panel (real-time playback cursor)
2. Auto-Play on clip select toggle
3. Bulk label: "Label Selected" + "Label Visible" with multi-select tree
4. Quality row coloring by `no_speech_prob` / `avg_logprob`
5. "Prepare Staged" button — re-extract without re-staging
6. Anti-seed ranking: `score = cosine(clip, shana_centroid) - cosine(clip, not_shana_centroid)`

---

## Important Rules

- Always prefer Japanese audio stream (`jpn`) — never mix with English clips
- Recap episodes excluded by default (filename pattern `recap`)
- Do not export `Not Shana` or `Reject`
- Similarity ranking is triage only — not pre-filtering, not true speaker ID
- Close `GammaTTSDataPrep.exe` before rebuilding the exe

## Build

```bash
# Close the app first
py -3.12 scripts/build_tts_dataset_gui_exe.py
```

## Next Steps

1. Label more clips (need 20+ clean Shana seeds for ranking to be useful)
2. Add similarity threshold filter to hide low-scoring clips
3. Eventually: swap in resemblyzer/speechbrain embeddings for real speaker filtering
4. Reach ~100+ clean clips before attempting GPT-SoVITS training
