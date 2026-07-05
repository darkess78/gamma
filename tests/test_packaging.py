from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optional_runtime_dependencies_are_not_in_the_core_install() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {dependency.split(">=", 1)[0].lower() for dependency in project["dependencies"]}
    extras = project["optional-dependencies"]

    assert "openai>=1.40.0" in extras["hosted"]
    assert "faster-whisper>=1.1.0" in extras["local-voice"]
    assert "sounddevice" in extras["local-voice"]


def test_readme_documents_the_minimal_and_optional_install_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert ".[dev]" in readme
    for extra in ("hosted", "local-voice", "discord", "audio-understanding"):
        assert f".[{extra}]" in readme
