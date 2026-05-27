#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../.."

.venv/bin/pyinstaller helper_projects/GammaTTSDataPrep/packaging/tts_dataset_gui.spec --noconfirm --workpath helper_projects/GammaTTSDataPrep/build

echo
echo "Build complete: dist/GammaTTSDataPrep/GammaTTSDataPrep"
