# Assets

`assets/` is for small, versioned source/runtime assets that Gamma needs or that are intentionally curated for the repo.

Good fits:

- system fallback audio such as `assets/audio/system/filtered.wav`
- curated Shana voice reference clips and metadata
- reviewed, intentionally versioned voice dataset subsets
- small static source assets that should survive a clean checkout

Do not put runtime output, logs, temporary files, local model installs, sidecar projects, generated TTS artifacts, or large working datasets here. Those belong in `data/`, `imagegen/`, or a future external/vendor-local folder.
