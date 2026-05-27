# Shana TTS Dataset

Reviewed Shana-only clips exported from `data/tts_data_prep/shana_dataset`.
This excludes clips labeled `Not Shana` or `Reject`.

Exported clips: 155

Manifest rows with transcript text: 155

Manifest rows needing transcript review: 75

Large-model transcript checks: 155

Training candidate rows: 67

Transcript agreement:
- `match`: 80
- `close`: 34
- `different`: 41
- `empty`: 0

Labels:
- `clean`: clips labeled `Shana`
- `light_noise`: clips labeled `Shana-light-noise`
- `heavy_noise`: clips labeled `Shana-heavy-noise`

The WAV files are reviewed by speaker label. Transcript text is machine-generated with faster-whisper and should be reviewed before supervised TTS training.

Use `helper_projects/GammaTTSDataPrep/scripts/export_shana_tts_assets.py --clean`, then `helper_projects/GammaTTSDataPrep/scripts/transcribe_shana_tts_assets.py --model small --language ja`, then `helper_projects/GammaTTSDataPrep/scripts/validate_shana_tts_transcripts.py --model large-v3 --language ja --device cuda --compute-type float16`, to refresh this folder after labeling more clips.
