from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import settings
from ..memory.service import MemoryService

if TYPE_CHECKING:
    from ..identity.profile import SpeakerProfile

try:
    import yaml
except ImportError:  # optional dependency for YAML persona/scenario files
    yaml = None


PERSONA_DIR = Path(__file__).resolve().parent
CONFIG_DIR = settings.project_root / "config"
CORE_MEMORIES_PATH = settings.data_dir / "core_memories.md"

# Module-level cache: (mtime, content). Re-read only when the file changes on disk.
_file_cache: dict[Path, tuple[float, str]] = {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _cached_text(path: Path) -> str:
    """Return file contents, re-reading only when the file has been modified."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return ""
    cached = _file_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    content = path.read_text(encoding="utf-8").strip()
    _file_cache[path] = (mtime, content)
    return content


def build_system_prompt(
    memory_service: MemoryService | None = None,
    user_text: str | None = None,
    session_id: str | None = None,
    speaker: "SpeakerProfile | None" = None,
) -> str:
    core = _cached_text(PERSONA_DIR / "core.md")
    boundaries = _cached_text(PERSONA_DIR / "boundaries.md")
    style = json.loads(_cached_text(PERSONA_DIR / "style.json") or "{}")
    relationship = json.loads(_cached_text(PERSONA_DIR / "relationship_state.json") or "{}")
    persona_config = _read_toml(CONFIG_DIR / "persona.toml") if (CONFIG_DIR / "persona.toml").exists() else {}
    # Load additional YAML persona definitions if present
    yaml_path = CONFIG_DIR / "persona.yaml"
    if yaml_path.exists() and yaml is not None:
        try:
            persona_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception:
            persona_yaml = None
        if persona_yaml:
            persona_config["yaml"] = persona_yaml
    # Load scenario definitions
    scenario_path = CONFIG_DIR / "scenario.yaml"
    if scenario_path.exists() and yaml is not None:
        try:
            scenario_yaml = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        except Exception:
            scenario_yaml = None
        if scenario_yaml:
            persona_config["scenario"] = scenario_yaml
    memory_config = _read_toml(CONFIG_DIR / "memory.toml") if (CONFIG_DIR / "memory.toml").exists() else {}

    # Build the speaker block
    speaker_block = _build_speaker_block(speaker)

    # Memory is only read for owner/trusted speakers
    memory_read_ok = speaker is None or speaker.memory_read_allowed

    memory_lines: list[str] = []
    if memory_read_ok and memory_service and settings.memory_enabled and memory_config.get("enabled", True):
        if memory_config.get("profile_enabled", True):
            subject_type = speaker.subject_type if speaker else "primary_user"
            profile_facts = memory_service.get_profile_facts(limit=settings.memory_top_k, subject_type=subject_type)
            if profile_facts:
                memory_lines.append("## Stored Facts About The User")
                for fact in profile_facts:
                    memory_lines.append(f"- [{fact.category}] {fact.fact_text}")

            mentioned_people = _extract_named_people(user_text or "")
            if mentioned_people:
                for person_name in mentioned_people[:2]:
                    person_facts = memory_service.get_profile_facts(
                        limit=3,
                        subject_type="other_person",
                        subject_name=person_name,
                    )
                    if person_facts:
                        memory_lines.append(f"## Stored Facts About {person_name}")
                        for fact in person_facts:
                            relationship = f" relationship={fact.relationship_to_user}" if fact.relationship_to_user else ""
                            memory_lines.append(f"- [{fact.category}]{relationship} {fact.fact_text}")

        if user_text and memory_config.get("episodic_enabled", True):
            memories = memory_service.search_memories(
                user_text,
                session_id=session_id,
                limit=settings.memory_top_k,
            )
            if memories:
                memory_lines.append("## Relevant Episodic Memories")
                for memory in memories:
                    tags = f" tags={memory.tags}" if memory.tags else ""
                    subject = ""
                    if memory.subject_type == "other_person" and memory.subject_name:
                        subject = f" subject={memory.subject_name}"
                    memory_lines.append(f"- {memory.summary}{tags}{subject}")

    memory_block = "\n".join(memory_lines).strip() or "No stored memory injected for this turn."

    core_memories_block = _load_core_memories()

    return "\n\n".join([
        "# Core Persona\n" + core,
        "# Boundaries\n" + boundaries,
        "# Style\n" + json.dumps(style, indent=2, sort_keys=True),
        "# Relationship State\n" + json.dumps(relationship, indent=2, sort_keys=True),
        "# Persona Config\n" + json.dumps(persona_config, indent=2, sort_keys=True),
        "# Memory Config\n" + json.dumps(memory_config, indent=2, sort_keys=True),
        "# Core Memories\n" + core_memories_block,
        "# Current Speaker\n" + speaker_block,
        "# Runtime Memory\n" + memory_block,
        "# Response Rules\nUse stored memory when it is relevant and explicitly admit uncertainty when none exists. Do not say you lack memory if the Runtime Memory section contains relevant facts. Core Memories are permanent and always true — treat them as established facts.",
    ])


def _load_core_memories() -> str:
    raw = _cached_text(CORE_MEMORIES_PATH)
    if not raw:
        return "No core memories stored yet."
    # Extract bullet lines only — skip comments and headers
    lines = [line.strip() for line in raw.splitlines() if line.strip().startswith("- ")]
    if not lines:
        return "No core memories stored yet."
    return "\n".join(lines)


def _build_speaker_block(speaker: "SpeakerProfile | None") -> str:
    if speaker is None:
        return (
            "Name: Owner (assumed)\n"
            "Trust: owner\n"
            "Note: No speaker context provided — local solo use. Treat as the owner."
        )
    return speaker.describe()


def _extract_named_people(text: str) -> list[str]:
    if not text:
        return []
    matches = re.findall(r"\b(?:friend|brother|sister|partner|wife|husband|girlfriend|boyfriend|coworker|coworkers?|manager|mom|mother|dad|father)\s+([A-Z][a-z]+)\b", text)
    seen: set[str] = set()
    names: list[str] = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            names.append(match)
    return names
