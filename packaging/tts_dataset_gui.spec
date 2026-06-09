# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


repo_root = Path(SPECPATH).resolve().parent

hiddenimports = [
    "faster_whisper",
    "av",
    "ctranslate2",
    "tokenizers",
    "huggingface_hub",
    "yaml",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "demucs",
    "demucs.separate",
    "demucs.pretrained",
    "demucs.apply",
    "demucs.audio",
    *collect_submodules("demucs"),
    "numpy.core.multiarray",
    "numpy.core._multiarray_umath",
    "numpy.core._multiarray_tests",
    *collect_submodules("numpy"),
    *collect_submodules("torchaudio"),
    "soundfile",
    "soundfile._soundfile",
    "cffi",
    "_soundfile",
    *collect_submodules("soundfile"),
]

datas = (
    collect_data_files("faster_whisper")
    + collect_data_files("demucs")
    + collect_data_files("torchaudio")
    + collect_data_files("soundfile")
)


a = Analysis(
    [str(repo_root / "gamma" / "run_tts_dataset_gui.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GammaTTSDataPrep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GammaTTSDataPrep",
)
