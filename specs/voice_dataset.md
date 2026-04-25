# Voice Dataset Prep

`gamma.run_prepare_tts_dataset` is an offline helper for turning long-form episode media into reviewable speech clips.

`gamma.run_tts_dataset_gui` is a dark-mode Tkinter desktop wrapper around staging, extraction, live logs, and manual review.

What it does:
- scans a file or directory of media
- runs `faster-whisper` segmentation with timestamps
- merges nearby transcript fragments into more natural dialogue clips
- can target a specific container audio stream for dual-audio releases
- excludes recap files by default using filename matching
- extracts mono 16 kHz wav clips with `ffmpeg`
- writes `manifest.jsonl`, `segments.csv`, and `REVIEW.md`

What it does not do:
- it does not identify a character by speaker name
- it does not perform diarization
- it does not remove music or effects beyond Whisper VAD filtering
- it should not be treated as training-ready output without manual review

GUI behavior:
- runs staging and extraction in a worker thread so the window stays responsive
- streams live logs from the pipeline, including each extracted candidate speech clip
- loads `manifest.jsonl` after a run and lets you tag clips as `Shana`, `Shana-light-noise`, `Shana-heavy-noise`, `Not Shana`, or `Reject`
- plays extracted `.wav` clips directly inside the review pane on Windows
- can rank the review queue by acoustic similarity to your confirmed `Shana` labels
- can scan for exact and near-duplicate clips in the review queue
- can export separate clean and noisy Shana subsets for training
- lets you choose the Whisper STT model explicitly in the GUI
- lets you tune the extraction `Max Seconds` threshold in the GUI
- lets you create trimmed derived clips from mixed segments during review
- saves manual labels to `labels.json` in the dataset output directory
- uses a dark layout intended for long review sessions

Important limitation:
- the GUI cannot truly tell you "this is Shana speaking"
- it can only show that a candidate speech segment was extracted
- `Shana / Shana-light-noise / Shana-heavy-noise / Not Shana / Reject` is a manual review decision in the current workflow
- the similarity ranking is heuristic triage from seeded Shana clips, not robust speaker identification

Recommended labeling:
- `Shana`: clean solo lines suitable for core training
- `Shana-light-noise`: mostly Shana with minor contamination that may still be usable for some training passes
- `Shana-heavy-noise`: mostly Shana, but with enough bleed or contamination that it should stay separated from cleaner data
- `Not Shana`: valid speech clip, wrong speaker
- `Reject`: unusable clip due to overlap, heavy SFX, music, or corruption

Export layout:

```text
data/shana_dataset/exports/
  shana_clean/
  shana_noisy/
  manifests/
    shana_clean.jsonl
    shana_noisy.jsonl
```

Packaged app storage:
- the Windows executable now defaults its working data to `%LOCALAPPDATA%\GammaTTSDataPrep`
- staging and dataset paths entered as relative paths are resolved under that persistent folder
- this avoids losing extracted clips and labels when `dist/GammaTTSDataPrep` is rebuilt

Ranking note:
- the seeded similarity ranking currently uses only clips labeled `Shana` as the clean reference set
- `Shana-light-noise` and `Shana-heavy-noise` clips are exported separately but are not used as seed references by default

Duplicate note:
- `Find Duplicates` marks byte-identical WAV clips as exact duplicates
- it also flags extremely similar clips with very close durations as near duplicates
- duplicate status is shown in the review queue and exported manifests
- this is meant to catch reused lines, recap reuse, and slightly shifted duplicate cuts before training

Whisper model note:
- Japanese extraction requires a multilingual Whisper model such as `base`, `small`, `medium`, or `large-v3`
- English-only models such as `base.en` or `small.en` are invalid for `language=ja` and now fail fast with a clear error

Extraction tuning note:
- if you see many `rejected_long` segments, increase `Max Seconds` in the GUI
- longer segments can recover more candidate clips, but they also increase the chance of mixed-speaker or noisy data

Trim note:
- for mixed clips where Shana only speaks in part of the segment, use `Trim Start` and `Trim End`
- `Create Trimmed Clip` writes a derived WAV into the dataset and appends it to the manifest
- this lets you salvage usable portions instead of forcing the whole original clip into `Not Shana` or `Reject`

Recommended use for Shana:
1. Run the tool across the episode folder.
2. Review clips and keep only clean solo lines spoken by Shana.
3. Remove overlap, music bleed, battle shouts, whispers, and heavily processed lines.
4. Use the filtered set as GPT-SoVITS training data or pick a few clean samples as reference audio.

Example:

```bash
python -m gamma.run_prepare_tts_dataset "D:\anime\Shakugan no Shana" --out-dir data\shana_dataset --language ja
```

For dual-audio episodes, prefer forcing the Japanese stream:

```bash
python -m gamma.run_prepare_tts_dataset "D:\anime\Shakugan no Shana" --out-dir data\shana_dataset --language ja --audio-stream-lang jpn
```

Stage from a share first, then prepare:

```bash
python -m gamma.run_stage_and_prepare_tts_dataset "\\10.78.78.250\Media\Anime\Shakugan no Shana" --staging-dir data\source_media\shakugan_no_shana --dataset-out-dir data\shana_dataset --prepare --prepare-args --language ja
```

Launch the desktop GUI:

```bash
python -m gamma.run_tts_dataset_gui
```

Build a Windows executable:

```bash
py -3.12 -m pip install pyinstaller
py -3.12 scripts/build_tts_dataset_gui_exe.py
```

Expected output:

```text
dist/GammaTTSDataPrep/GammaTTSDataPrep.exe
```

Packaging notes:
- the executable is built from `packaging/tts_dataset_gui.spec`
- `ffmpeg` and `ffprobe` are still external runtime requirements
- the build currently produces a folder-based app, not a single-file exe, to avoid extra startup and packaging friction
- only launch `dist/GammaTTSDataPrep/GammaTTSDataPrep.exe`
- do not launch `build/tts_dataset_gui/tts_dataset_gui/GammaTTSDataPrep.exe`; that is a PyInstaller intermediate artifact and will fail because it does not carry the final `_internal` runtime folder

Staging behavior:
- copies into a local directory before any processing
- preserves timestamps with `shutil.copy2`
- skips unchanged files by default using file size and modified time
- excludes recap files by default using the filename substring `recap`
- supports `--overwrite` when you want to force a fresh local copy

Filtering notes:
- both staging and prep exclude files whose names contain `recap` by default
- add more exclusions with `--exclude-pattern`, for example `--exclude-pattern ncop`
- narration-heavy scenes inside normal episodes still need manual clip review

Requirements:
- `ffmpeg` must be installed and available on `PATH`, or passed via `--ffmpeg-bin`
- `ffprobe` must be installed and available on `PATH`, or passed via `--ffprobe-bin`
- the repo Python environment must have `faster-whisper` available

Output layout:

```text
data/shana_dataset/
  REVIEW.md
  manifest.jsonl
  segments.csv
  clips/
    episode_01/
      episode_01_0001.wav
      ...
```
