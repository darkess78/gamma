# GammaTTSDataPrep

GammaTTSDataPrep is the helper project for preparing Shana TTS training data. It lives outside the core `gamma` runtime package so dataset staging, review, export, and packaging helpers do not look like assistant runtime code.

The source entry points are still importable through `gamma`:

- `python -m gamma.run_tts_dataset_gui`
- `python -m gamma.run_prepare_tts_dataset`
- `python -m gamma.run_stage_and_prepare_tts_dataset`

## Folders

- `packaging/` contains PyInstaller build files for the desktop GUI.
- `scripts/` contains project-specific dataset export, transcript, validation, and executable build helpers.
- `build/` is the local PyInstaller work folder for this helper project and is ignored by git.

## Build

From the repo root:

```powershell
py -3.12 helper_projects\GammaTTSDataPrep\scripts\build_exe.py
```

Or use the platform wrapper:

```bat
helper_projects\GammaTTSDataPrep\packaging\build.bat
```

```bash
./helper_projects/GammaTTSDataPrep/packaging/build.sh
```

Build work files are written to `helper_projects/GammaTTSDataPrep/build/`. Build output is still written to `dist/GammaTTSDataPrep/`.

## Data Boundary

Working media, extracted clips, labels, and review output belong under `data/tts_data_prep/` and are not committed. Small curated Shana reference clips and reviewed export manifests belong under `assets/voice_datasets/` or `assets/voice_references/` when they are intentionally versioned.
