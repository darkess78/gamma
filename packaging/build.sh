#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

.venv/bin/pyinstaller packaging/tts_dataset_gui.spec --noconfirm

echo
echo "Build complete: dist/GammaTTSDataPrep/GammaTTSDataPrep"
