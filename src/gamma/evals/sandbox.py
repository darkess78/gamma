"""A throwaway Gamma state for evals: temp database, data dir and persona files.

Everything the conversation path reads or writes (memory, journal, stream
chat memory, feelings, core memories, users.toml) points into a temporary
directory, and every patched setting is put back on exit.

- ``full=True`` (privacy suite, tests): also moves project_root, the persona
  directory and the config directory, so nothing from the checkout's own data
  can reach a prompt. The persona text is copied from the real files unless
  ``persona_files`` overrides a file.
- ``full=False`` (identity run against a real model): only the database,
  feelings, core memories and owner name are isolated, so the real model
  routing config still loads.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PERSONA_FILES = ("core.md", "boundaries.md", "style.json", "relationship_state.json")


@dataclass
class Sandbox:
    root: Path
    data: Path
    persona: Path
    config: Path

    @property
    def emotion_path(self) -> Path:
        return self.data / "assistant_emotion_memory.json"

    @property
    def core_memories_path(self) -> Path:
        return self.data / "core_memories.md"


@contextmanager
def sandbox(*, full: bool = True, owner_name: str = "EvalOwner",
            persona_files: dict[str, str] | None = None, core_memories: str = "") -> Iterator[Sandbox]:
    from ..config import settings
    from ..identity import resolver
    from ..persona import assistant_state, emotion_service, loader

    saved: list[tuple[object, str, object]] = []

    def patch(obj, name: str, value) -> None:
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    tmp = Path(tempfile.mkdtemp(prefix="gamma-eval-"))
    box = Sandbox(root=tmp, data=tmp / "data", persona=tmp / "persona", config=tmp / "config")
    (box.data / "memory").mkdir(parents=True)
    box.persona.mkdir()
    box.config.mkdir()
    try:
        box.core_memories_path.write_text(core_memories, encoding="utf-8")
        (box.config / "users.toml").write_text(f'[owner]\nname = "{owner_name}"\ntrust = "owner"\n', encoding="utf-8")
        patch(settings, "database_url", "sqlite:///" + str(box.data / "memory" / "eval.db"))
        patch(settings, "memory_enabled", True)
        patch(resolver, "_CONFIG_PATH", box.config / "users.toml")
        patch(loader, "CORE_MEMORIES_PATH", box.core_memories_path)
        patch(emotion_service, "_DEFAULT_PATH", box.emotion_path)
        patch(assistant_state, "_DEFAULT_STATE_PATH", box.data / "assistant_state.json")
        if full:
            for name in PERSONA_FILES:
                text = (persona_files or {}).get(name)
                if text is None:
                    source = loader.PERSONA_DIR / name
                    text = source.read_text(encoding="utf-8") if source.exists() else ("{}" if name.endswith(".json") else "")
                (box.persona / name).write_text(text, encoding="utf-8")
            for name in ("persona.yaml", "memory.toml"):
                source = loader.CONFIG_DIR / name
                if source.exists():
                    shutil.copyfile(source, box.config / name)
            patch(settings, "project_root", box.root)
            patch(settings, "data_dir", box.data)
            patch(settings, "speech_filter_llm_enabled", False)
            patch(settings, "memory_embeddings_dir", "")
            patch(loader, "PERSONA_DIR", box.persona)
            patch(loader, "CONFIG_DIR", box.config)
        yield box
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
        shutil.rmtree(tmp, ignore_errors=True)
