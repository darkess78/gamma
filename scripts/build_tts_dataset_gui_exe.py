from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "packaging" / "tts_dataset_gui.spec"
    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build" / "tts_dataset_gui"

    if not spec_path.exists():
        raise SystemExit(f"PyInstaller spec not found: {spec_path}")
    try:
        import PyInstaller  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "PyInstaller is not installed in the current environment.\n"
            "Install it first with: py -3.12 -m pip install pyinstaller"
        ) from exc

    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        str(spec_path),
    ]
    completed = subprocess.run(command, cwd=repo_root, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    exe_path = dist_dir / "GammaTTSDataPrep" / "GammaTTSDataPrep.exe"
    if not exe_path.exists():
        raise SystemExit(f"Build completed but expected executable was not found: {exe_path}")

    print(f"Built executable: {exe_path}")
    print("ffmpeg and ffprobe must still be installed separately and available on PATH.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
