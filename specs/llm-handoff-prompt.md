# Gamma — LLM Handoff Prompt (Full)

Use this document to orient yourself to the Gamma repo before contributing. Read it top-to-bottom before touching anything.

---

## What This Repo Is

**Gamma** is a local Windows/Linux AI assistant project built around a Shana (Shakugan no Shana) persona. It has two largely independent concerns:

1. **Gamma AI assistant** — a FastAPI-based backend with LLM routing, memory, voice I/O (STT/TTS), a system tray app, and a dark-mode web dashboard. This is the "live" assistant system.

2. **TTS dataset prep tooling** — an offline, offline-first workflow for building a Shana-voice training dataset from the source anime. This is a standalone Tkinter desktop app (`GammaTTSDataPrep.exe`) that stages episodes, extracts speech clips via Whisper, and lets you manually review/label them. **This is the active area of development.**

The two concerns share the `gamma/` Python package but are otherwise independent. Do not conflate them.

---

## Repo Layout

```
gamma/                          # Python package root
  __init__.py
  config.py                     # App-wide settings (stt_model, stt_device, etc.)
  main.py                       # Gamma assistant entry point (FastAPI app)
  errors.py

  api/
    routes.py                   # FastAPI routes

  avatar_events/
    models.py                   # Avatar event data models

  conversation/
    service.py                  # Conversation state and LLM routing

  dashboard/
    main.py                     # Dashboard FastAPI app
    service.py
    auth.py
    static/
      index.html
      dashboard.css
      dashboard.js

  llm/
    base.py                     # LLM adapter base class
    factory.py                  # Adapter selection
    openai_adapter.py           # OpenAI-compatible adapter
    local_adapter.py            # Local model adapter (Ollama etc.)
    mock_adapter.py             # Mock for testing
    ollama_probe.py             # Ollama health/model probe

  memory/
    models.py
    service.py                  # Persistent memory for conversations

  persona/
    loader.py                   # Loads persona config
    core.md                     # Shana persona definition
    boundaries.md               # Persona behavioral constraints
    style.json                  # Response style config
    relationship_state.json     # Relationship tracking state

  schemas/
    conversation.py
    response.py
    voice.py

  supervisor/
    manager.py                  # Process supervisor
    cli.py

  system/
    status.py                   # System health/status

  tools/
    base.py
    builtin.py
    registry.py

  tray/
    app.py                      # System tray app (Windows/Linux)
    __main__.py

  vision/
    service.py                  # Vision/screenshot input

  voice/
    controller.py               # Voice pipeline controller
    live.py                     # Live voice loop
    live_jobs.py
    roundtrip.py                # Single-turn voice roundtrip
    stt.py                      # STT (faster-whisper)
    tts.py                      # TTS (GPT-SoVITS)

  # --- TTS DATASET PREP (active development) ---
  run_tts_dataset_gui.py        # Main GUI app (~1800 lines) — Tkinter desktop app
  run_prepare_tts_dataset.py    # CLI extraction pipeline (faster-whisper + ffmpeg)
  run_stage_and_prepare_tts_dataset.py  # Stage-from-share + optional prepare wrapper

  # --- Other runners ---
  run_live_voice_worker.py
  run_local_voice_loop.py
  run_mic_voice_loop.py
  run_voice_mode.py
  run_voice_roundtrip.py
  run_llm_test.py
  run_stt_test.py
  run_tts_test.py

scripts/
  build_tts_dataset_gui_exe.py  # PyInstaller build script for GammaTTSDataPrep.exe
  start_shana_windows.py        # Launches full Gamma assistant stack on Windows
  start_dashboard_windows.py
  start_gamma_tray.py / _windows / _linux variants
  start_gpt_sovits_*.py/.sh/.ps1
  stop_gpt_sovits_*.py/.sh/.ps1
  stop_services*.py/.sh
  open_gamma*.py/.cmd
  install_gamma_linux.sh

packaging/
  tts_dataset_gui.spec          # PyInstaller spec for the TTS dataset GUI exe

specs/
  README.md
  architecture.md               # Gamma assistant architecture
  integrations.md
  memory.md
  models.md
  persona.md
  phase1.md
  product.md
  voice.md
  voice_dataset.md              # TTS dataset prep docs (somewhat outdated, see this file)
  llm-handoff-prompt.md         # This file
  llm-handoff-prompt-lite.md    # Condensed version

config/
  app.example.toml              # Example app config

dist/
  GammaTTSDataPrep/
    GammaTTSDataPrep.exe        # Built executable (do not edit, rebuild from source)
```

---

## TTS Dataset Prep — What It Does

The goal is to build a Shana-voice TTS training dataset from the original Shakugan no Shana anime episodes, entirely offline and with full manual control over quality.

**Pipeline:**
1. **Stage** — copy episode files from a UNC share (`\\10.78.78.250\Media\Anime\Shakugan no Shana\Shakugan no Shana`) into a local staging dir. Originals are never modified.
2. **Prepare** — run `faster-whisper` on staged files to segment speech, merge nearby segments, extract mono 16 kHz WAV clips via ffmpeg. Write `manifest.jsonl`, `segments.csv`, `REVIEW.md`.
3. **Review** — load the manifest in the GUI, listen to clips, label them.
4. **Export** — copy labeled clips into training-ready subsets.

**Data root:** `C:\Users\darke\AppData\Local\GammaTTSDataPrep\`

```
GammaTTSDataPrep\
  source_media\shakugan_no_shana\    # Staged episode files
  shana_dataset\
    manifest.jsonl                   # All extracted clips (append-only, includes derived trims)
    labels.json                      # Manual review labels
    segments.csv
    REVIEW.md
    shana_seed_archive.json        # Persisted similarity seed vectors (survives manifest re-extraction)
    clips\
      <episode_id>\
        <clip_id>.wav                # Extracted mono 16kHz WAV clips
      derived\
        <clip_id>_trim_*.wav         # Trimmed derived clips
    exports\
      shana_clean\                   # WAVs labeled "Shana"
      shana_light_noise\             # WAVs labeled "Shana-light-noise"
      shana_heavy_noise\             # WAVs labeled "Shana-heavy-noise"
      manifests\
        shana_clean.jsonl
        shana_light_noise.jsonl
        shana_heavy_noise.jsonl
```

---

## Review Labels

| Label | Meaning |
|---|---|
| `Shana` | Clean solo Shana line — core training audio |
| `Shana-light-noise` | Shana speaking, minor contamination (light ambience, trivial tail) |
| `Shana-heavy-noise` | Shana speaking but substantially contaminated (music, overlap, SFX) |
| `Not Shana` | Valid clean speech, confirmed different speaker |
| `Reject` | Unusable — bad cut, distortion, overlap, junk |

**Legacy:** old `Shana-noisy` labels are automatically migrated to `Shana-heavy-noise` on manifest load.

**Export:** Only `Shana`, `Shana-light-noise`, and `Shana-heavy-noise` are exported. `Not Shana` and `Reject` are never exported.

**Training guidance:** Only `Shana` and `Shana-light-noise` should be used for base GPT-SoVITS training. `Shana-heavy-noise` is a fallback/experimental pool only.

---

## Current Dataset State (as of last export)

- `clean=1`, `light=7`, `heavy=10`
- **Not training-ready.** Need significantly more clean and light-noise clips.
- Focus: review more episodes, label more Shana clips, use trimming to salvage mixed segments.

---

## GUI Features (run_tts_dataset_gui.py)

### Pipeline Tab
- **Source** — UNC share or local folder containing episode files
- **Staging** — local copy destination (defaults to `%LOCALAPPDATA%\GammaTTSDataPrep\source_media\shakugan_no_shana`)
- **Dataset** — output directory (defaults to `%LOCALAPPDATA%\GammaTTSDataPrep\shana_dataset`)
- **Language** — Whisper language hint (`ja` for Japanese source track)
- **STT Model** — faster-whisper model name (`small` default; `medium`/`large-v3` for better accuracy)
- **Audio Stream Lang** — preferred container audio stream (`jpn` for Japanese dual-audio)
- **Exclude Patterns** — comma-separated filename substrings to skip (recap excluded by default)
- **Limit Files** — cap how many files to process (0 = all)
- **Max Seconds** — maximum extracted clip length (default 12.0s)
- **Overwrite** — force re-copy even if staged file looks unchanged
- **Separate Vocals (Demucs)** — run Demucs vocal separation before transcription; strips music, SFX, and ambience so clips contain vocals only. Requires the `demucs` package. Significantly slower (one pass per episode). Uses the `htdemucs` model by default (CLI flag `--demucs-model` to override).

**Action buttons:**
- **Stage Only** — copy files without extracting
- **Stage + Prepare** — copy then extract in one pass
- **Prepare Staged** — re-run extraction on already-staged files without re-copying (useful for changing model/settings)
- **Load Manifest** — load `manifest.jsonl` into the review queue
- **Stop Pipeline** — cooperative cancellation at safe checkpoints

### Review Tab
- **Filter** — show All / Unlabeled / specific label
- **Rank Likely Shana** — score all clips using `cosine(clip, shana_centroid) - cosine(clip, not_shana_centroid)`. Uses all `Shana*`-labeled clips (Shana, Shana-light-noise, Shana-heavy-noise) as positive seeds and `Not Shana` clips as anti-seeds. Persists seed vectors to `shana_seed_archive.json` so rankings survive manifest re-extraction. Also pulls in "orphan" seeds — clips labeled in prior manifest runs that are still on disk. Sorts queue by score descending.
- **Find Duplicates** — SHA-256 exact match + near-duplicate cosine detection
- **Auto-Advance** — after labeling, automatically move to next clip (default on)
- **Auto-Play** — replay clip automatically when navigating to it (default off)

**Review queue tree:**
- Columns: Episode, Seconds, Score, Duplicate, Label
- Row colors: white = normal quality, amber = borderline (`no_speech_prob > 0.2` or `avg_logprob < -0.5`), dim red = low quality (`no_speech_prob > 0.4`)
- Multi-select: Shift+click or Ctrl+click

**Detail panel:**
- Clip metadata (episode, time range, language, label, score, duplicate status, Whisper confidence)
- Transcript text
- **Waveform canvas** — 56px visual waveform with real-time playback cursor
- **Playback transport** — draggable timeline seek, Prev / Replay / Play / Stop / Next
- **Label buttons** — Mark Shana, Shana-light-noise, Shana-heavy-noise, Not Shana, Reject, Clear Label
- **Bulk label row** — combobox + "Label Selected" (multi-select) + "Label Visible" (all in current filter)
- **Export Training Subsets** — exports clean/light/heavy subsets to `exports/`
- **Trim** — Trim Start + Trim End + "Create Trimmed Clip" — creates a derived WAV from a sub-range of the current clip and appends it to the manifest

### Hotkeys (ignored when a text entry has focus)
| Key | Action |
|---|---|
| `1` | Mark Shana |
| `2` | Mark Shana-light-noise |
| `3` | Mark Shana-heavy-noise |
| `4` | Mark Not Shana |
| `5` | Reject |
| `0` | Clear label |
| `Left` / `Right` | Previous / next clip |
| `P` | Play |
| `R` | Replay |
| `Space` | Play/stop toggle |
| `S` | Stop |
| `[` / `]` | Promote / demote between Shana → light → heavy |

---

## Similarity Scoring — How It Works

The similarity ranking uses a lightweight 12-dimensional spectral signature per clip:
- 8 frequency band energy values (FFT-based, normalized)
- Zero crossing rate
- RMS energy
- Duration feature (clamped to 8s)
- Spectral centroid (normalized)

Signatures are L2-normalized and compared with cosine similarity.

**Scoring formula (with anti-seeds):**
```
score = cosine(clip, shana_centroid) - cosine(clip, not_shana_centroid)
```

- `shana_centroid` = mean of signatures from all `Shana*`-labeled clips (Shana, Shana-light-noise, Shana-heavy-noise) in the current manifest, plus vectors from `shana_seed_archive.json` and any orphan clips on disk
- `not_shana_centroid` = mean from `Not Shana` clips plus archive/orphan not-shana vectors
- If no anti-seeds exist, falls back to plain `cosine(clip, shana_centroid)`
- After ranking, the full set of used seed vectors is saved back to `shana_seed_archive.json`

**Important limitations:**
- 12-dim spectral features are too crude for reliable speaker identification
- With few seeds the centroid is not representative
- This is triage ranking only — not a pre-filter, not true speaker ID
- Pre-filtering (hiding clips below a threshold) should not be enabled until ~20+ clean Shana seeds exist
- For robust speaker filtering in the future, integrate a proper speaker embedding model (e.g. resemblyzer or speechbrain ECAPA-TDNN)

---

## Technical Details

- **Python 3.12** — the project targets 3.12 explicitly
- **faster-whisper** — VAD-filtered transcription; multilingual models required for Japanese (`ja`). `.en` models will fail fast with a clear error.
- **ffmpeg / ffprobe / ffplay** — must be on PATH; used for clip extraction (hidden-window subprocess on Windows), audio stream selection, and playback
- **numpy** — used for signature computation and waveform drawing
- **tkinter** — dark-mode Tkinter GUI with ttk.clam theme
- **demucs** *(optional)* — vocal separation via `--vocals-only` flag. Requires `pip install demucs` (pulls in `torchaudio`, `soundfile`). Not bundled by default but the PyInstaller spec includes it when present.
- **PyInstaller 6.x** — builds `dist/GammaTTSDataPrep/GammaTTSDataPrep.exe` (folder-based, not single-file)
- All ffmpeg/ffprobe subprocesses use `CREATE_NO_WINDOW` on Windows
- App data lives in `%LOCALAPPDATA%\GammaTTSDataPrep` — rebuilding the exe does not wipe data
- `manifest.jsonl` is append-only; trim-derived clips are appended as new entries
- `labels.json` is overwritten on every label save

---

## Build Instructions

```bash
# Close GammaTTSDataPrep.exe first (PyInstaller cannot replace a running exe)
py -3.12 scripts/build_tts_dataset_gui_exe.py
# Output: dist/GammaTTSDataPrep/GammaTTSDataPrep.exe
```

Run from source (no build needed):
```bash
py -3.12 -m gamma.run_tts_dataset_gui
```

Run extraction CLI directly:
```bash
py -3.12 -m gamma.run_prepare_tts_dataset "C:\path\to\staged\episodes" --out-dir "C:\path\to\dataset" --language ja --audio-stream-lang jpn

# With Demucs vocal separation (strips music/SFX before transcription — much slower):
py -3.12 -m gamma.run_prepare_tts_dataset "C:\path\to\staged\episodes" --out-dir "C:\path\to\dataset" --language ja --audio-stream-lang jpn --vocals-only
# Use a different Demucs model:
py -3.12 -m gamma.run_prepare_tts_dataset ... --vocals-only --demucs-model htdemucs_ft
```

---

## Known Constraints and Rules

- Japanese audio track (`jpn`) is always preferred — never mix English and Japanese clips in the same dataset
- Recap episodes are excluded by default via the `recap` filename pattern
- `Not Shana` and `Reject` clips are never exported
- `Shana-heavy-noise` should not be in the base training set — export only, treat as experimental
- The system does not know who is Shana — all speaker identification is manual with similarity ranking as a triage aid
- Trim-derived clips keep the source episode path and Whisper metadata from the parent clip
- Similarity scores are invalidated (cleared) when any `Shana` label is added or cleared, forcing a re-rank

---

## Recently Implemented (Current Session)

All of the following were added in the most recent development session (not yet built into exe):

1. **Demucs vocal separation** — new `--vocals-only` / `--demucs-model` CLI flags and matching "Separate Vocals (Demucs)" checkbox in the Pipeline tab. When enabled: extracts HQ stereo audio, runs Demucs to isolate the vocals track, then transcribes and extracts clips from the clean vocals WAV. Significantly slower but produces cleaner clips with less music/SFX contamination. Requires `pip install demucs`.
2. **Seed archive persistence** — similarity ranking now saves used seed vectors to `shana_seed_archive.json` in the dataset dir. On subsequent runs, archived seeds are merged with current manifest seeds, so a re-extraction doesn't lose accumulated seed knowledge. Also detects "orphan" seeds — clips labeled in prior manifest runs that still exist on disk even if not in the current manifest.
3. **All Shana* labels as positive seeds** — ranking now treats `Shana`, `Shana-light-noise`, and `Shana-heavy-noise` clips as positive seeds (previously only `Shana`).
4. **Treeview and log scrollbars** — review treeview and log text widget now have explicit vertical scrollbars.

---

## Next Development Priorities

In rough order of impact:

1. **Label more clips** — need 20+ clean Shana clips before similarity scoring is meaningful
2. **Add similarity threshold filter** — once seeds are sufficient, add a slider/cutoff to hide low-scoring clips from the review queue (pre-filter, not pre-label)
3. **Speaker embedding upgrade** — replace the 12-dim spectral signature with resemblyzer or speechbrain ECAPA-TDNN embeddings for robust speaker discrimination
4. **Process more episodes** — use "Prepare Staged" to run extraction on more staged episodes with the current settings
5. **Training readiness** — GPT-SoVITS training requires roughly 100+ clean clips with accurate transcripts; current dataset (clean=1, light=7) is not ready
