from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPECS_ROOT = PROJECT_ROOT / "specs"
REMOVED_SPEC_NAMES = {
    "audio_understanding_deployment_proposal.md",
    "audio_understanding_handoff.md",
    "audio_understanding_plan.md",
    "integrations_observability_handoff.md",
    "live_voice_incremental_checklist.md",
    "live_voice_incremental_plan.md",
    "llm-handoff-prompt-lite.md",
    "llm-handoff-prompt.md",
    "models.md",
    "phase1.md",
    "resource_aware_model_routing_proposal.md",
    "streamer_gap_backlog.md",
    "streamer_roadmap.md",
    "streamer_roadmap_current.md",
    "voice_incremental_full.md",
    "voice_incremental_simple.md",
    "voice_interruptibility.md",
}


def _referenced_documents(text: str) -> set[str]:
    known_names = {path.name for path in SPECS_ROOT.rglob("*.md")} | {
        path.name for path in SPECS_ROOT.rglob("*.pdf")
    }
    backticked = [
        value
        for value in re.findall(r"`([^`\n]+\.(?:md|pdf))`", text, flags=re.IGNORECASE)
        if value.startswith(("specs/", "../")) or Path(value).name in known_names
    ]
    linked = re.findall(r"\[[^\]]+\]\(([^)]+\.(?:md|pdf))\)", text, flags=re.IGNORECASE)
    return {value.strip() for value in [*backticked, *linked] if "://" not in value}


def _resolve_reference(source: Path, value: str) -> Path:
    normalized = value.split("#", 1)[0]
    if normalized.startswith("specs/"):
        return PROJECT_ROOT / normalized
    local = source.parent / normalized
    if local.exists():
        return local
    return SPECS_ROOT / normalized


def test_spec_document_references_exist() -> None:
    missing: list[str] = []
    for source in SPECS_ROOT.rglob("*.md"):
        for reference in _referenced_documents(source.read_text(encoding="utf-8")):
            if not _resolve_reference(source, reference).exists():
                missing.append(f"{source.relative_to(PROJECT_ROOT)} -> {reference}")
    assert not missing, "Missing spec references:\n" + "\n".join(sorted(missing))


def test_removed_spec_names_are_not_referenced() -> None:
    references = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [PROJECT_ROOT / "README.md", *SPECS_ROOT.rglob("*.md")]
    )
    stale = sorted(name for name in REMOVED_SPEC_NAMES if name in references)
    assert not stale, f"Removed specs are still referenced: {stale}"
