# Data

`data/` is for local runtime state, generated output, models, sidecar installs, logs, and working datasets. The folder is intentionally ignored by git except for this README.

Good fits:

- generated audio under `data/audio/`
- uploaded or generated images under `data/images/`
- runtime state and logs under `data/runtime/` and `data/logs/`
- local model files such as `data/piper/`
- local sidecar installs such as `data/GPT-SoVITS/`
- GammaTTSDataPrep working media and clips under `data/tts_data_prep/`
- local memory files and databases

If a file is small, curated, safe to share, and meant to be a source asset, put it under `assets/` instead.
