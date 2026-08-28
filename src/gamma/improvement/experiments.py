from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..config import PROJECT_ROOT
from .models import ChangeClass


_EXPERIMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,79}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_EXACT = {
    ".env",
    "config/app.local.toml",
    "config/improvement.toml",
    "config/persona.yaml",
    "config/safety_banned_words.txt",
    "config/users.toml",
    "config/voices.local.toml",
    "evaluations/improvement/conversation.toml",
    "specs/locked_gamma_network_deployment.md",
    "src/gamma/config.py",
    "src/gamma/dashboard/auth.py",
    "tests/test_improvement.py",
}
_FORBIDDEN_PARTS = {".git", ".venv", "node_modules", "__pycache__"}
_FORBIDDEN_PREFIXES = ("data/", "gamma/", "tests/")
_PROTECTED_PREFIXES = (
    "src/gamma/improvement/",
    "src/gamma/persona/",
    "src/gamma/safety/",
)
_ALLOWED_TRANSITIONS = {
    "planned": {"workspace_ready", "abandoned"},
    "workspace_ready": {"candidate_ready", "rejected", "abandoned"},
    "candidate_ready": {"evaluating", "rejected", "abandoned"},
    "evaluating": {"ready_for_review", "rejected", "abandoned"},
    "ready_for_review": {"promoted", "rejected", "abandoned"},
    "rejected": set(),
    "promoted": set(),
    "abandoned": set(),
}


class ExperimentManifest(BaseModel):
    version: int = 1
    id: str
    hypothesis: str = Field(min_length=12, max_length=2000)
    domain: str = Field(min_length=2, max_length=80)
    change_class: ChangeClass
    baseline_commit: str
    contract_sha256: str
    fixture_catalog_sha256: str
    allowed_paths: tuple[str, ...]
    maximum_changed_files: int = Field(default=12, ge=1, le=100)
    maximum_attempts: int = Field(default=3, ge=1, le=10)
    maximum_wall_clock_minutes: int = Field(default=60, ge=1, le=240)
    status: Literal[
        "planned",
        "workspace_ready",
        "candidate_ready",
        "evaluating",
        "ready_for_review",
        "rejected",
        "promoted",
        "abandoned",
    ] = "planned"
    created_at: str = Field(default_factory=lambda: _utc_now())
    authorization: Literal["proposal_only"] = "proposal_only"

    @model_validator(mode="after")
    def validate_manifest(self) -> "ExperimentManifest":
        if not _EXPERIMENT_ID_RE.fullmatch(self.id):
            raise ValueError("invalid experiment id")
        if not _COMMIT_RE.fullmatch(self.baseline_commit):
            raise ValueError("baseline_commit must be a full lowercase Git commit id")
        for name, value in (
            ("contract_sha256", self.contract_sha256),
            ("fixture_catalog_sha256", self.fixture_catalog_sha256),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        normalized = tuple(normalize_experiment_path(path) for path in self.allowed_paths)
        if not normalized:
            raise ValueError("an experiment requires at least one allowed path")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed experiment paths must be unique")
        self.allowed_paths = normalized
        return self


class ScopeValidation(BaseModel):
    passed: bool
    changed_paths: list[str]
    violations: list[str]


class ExperimentStore:
    """Durable manifest/audit storage; it never edits source or invokes a model."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create(self, manifest: ExperimentManifest) -> Path:
        experiment_dir = self.root / manifest.id
        experiment_dir.mkdir(parents=True, exist_ok=False)
        manifest_path = experiment_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._audit(experiment_dir, event="created", previous=None, current=manifest.status)
        return manifest_path

    def read(self, experiment_id: str) -> ExperimentManifest:
        if not _EXPERIMENT_ID_RE.fullmatch(experiment_id):
            raise ValueError("invalid experiment id")
        return ExperimentManifest.model_validate_json(
            (self.root / experiment_id / "manifest.json").read_text(encoding="utf-8")
        )

    def transition(self, experiment_id: str, status: str) -> ExperimentManifest:
        manifest = self.read(experiment_id)
        if status == "promoted":
            raise PermissionError("promotion is not implemented; evaluator eligibility is not promotion authority")
        allowed = _ALLOWED_TRANSITIONS[manifest.status]
        if status not in allowed:
            raise ValueError(f"invalid experiment transition: {manifest.status} -> {status}")
        previous = manifest.status
        manifest.status = status  # type: ignore[assignment]
        experiment_dir = self.root / experiment_id
        manifest_path = experiment_dir / "manifest.json"
        replacement = manifest_path.with_suffix(".json.next")
        replacement.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        replacement.replace(manifest_path)
        self._audit(experiment_dir, event="transition", previous=previous, current=status)
        return manifest

    @staticmethod
    def _audit(experiment_dir: Path, *, event: str, previous: str | None, current: str) -> None:
        with (experiment_dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": _utc_now(),
                        "event": event,
                        "previous_status": previous,
                        "current_status": current,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


class ExperimentWorkspaceManager:
    """Create a detached Git worktree only when the contract explicitly enables experiments."""

    def __init__(self, *, project_root: Path = PROJECT_ROOT, worktree_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.worktree_root = worktree_root.resolve()

    def create(self, manifest: ExperimentManifest, *, enabled: bool) -> Path:
        if not enabled:
            raise PermissionError("isolated experiment worktrees are disabled by the improvement contract")
        if manifest.status != "planned":
            raise ValueError("worktrees may only be created for planned experiments")
        target = (self.worktree_root / manifest.id).resolve()
        if target.parent != self.worktree_root:
            raise ValueError("experiment worktree escaped its configured root")
        if target.exists():
            raise FileExistsError(f"experiment worktree already exists: {target}")
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        _run_git(self.project_root, ["cat-file", "-e", f"{manifest.baseline_commit}^{{commit}}"])
        _run_git(
            self.project_root,
            ["worktree", "add", "--detach", str(target), manifest.baseline_commit],
        )
        return target


def verify_experiment_workspace(
    manifest: ExperimentManifest,
    workspace: Path,
    *,
    require_clean: bool = True,
) -> None:
    """Verify the candidate workspace still matches the manifest's immutable baseline inputs."""

    root = workspace.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("experiment workspace does not exist")
    head = _run_git(root, ["rev-parse", "HEAD"])
    if head != manifest.baseline_commit:
        raise ValueError("experiment workspace HEAD does not match baseline_commit")
    if require_clean and _run_git(root, ["status", "--porcelain", "--untracked-files=no"]):
        raise ValueError("experiment workspace contains tracked changes")
    contract_path = root / "config" / "improvement.toml"
    fixture_path = root / "evaluations" / "improvement" / "conversation.toml"
    if not contract_path.is_file() or _file_sha256(contract_path) != manifest.contract_sha256:
        raise ValueError("experiment contract hash does not match manifest")
    if not fixture_path.is_file() or _file_sha256(fixture_path) != manifest.fixture_catalog_sha256:
        raise ValueError("experiment fixture catalog hash does not match manifest")


def build_experiment_manifest(
    *,
    experiment_id: str,
    hypothesis: str,
    domain: str,
    change_class: ChangeClass,
    baseline_commit: str,
    allowed_paths: tuple[str, ...],
    contract_path: Path | None = None,
    fixture_catalog_path: Path | None = None,
) -> ExperimentManifest:
    contract_path = contract_path or PROJECT_ROOT / "config" / "improvement.toml"
    fixture_catalog_path = fixture_catalog_path or PROJECT_ROOT / "evaluations" / "improvement" / "conversation.toml"
    return ExperimentManifest(
        id=experiment_id,
        hypothesis=hypothesis,
        domain=domain,
        change_class=change_class,
        baseline_commit=baseline_commit,
        contract_sha256=_file_sha256(contract_path),
        fixture_catalog_sha256=_file_sha256(fixture_catalog_path),
        allowed_paths=allowed_paths,
    )


def validate_candidate_scope(manifest: ExperimentManifest, changed_paths: list[str]) -> ScopeValidation:
    normalized: list[str] = []
    violations: list[str] = []
    for path in changed_paths:
        try:
            candidate = normalize_experiment_path(path)
        except ValueError as exc:
            violations.append(str(exc))
            continue
        normalized.append(candidate)
        if not any(candidate == allowed or candidate.startswith(allowed.rstrip("/") + "/") for allowed in manifest.allowed_paths):
            violations.append(f"path_outside_manifest_scope:{candidate}")
    if len(normalized) > manifest.maximum_changed_files:
        violations.append(
            f"changed_file_limit_exceeded:{len(normalized)}>{manifest.maximum_changed_files}"
        )
    return ScopeValidation(
        passed=not violations,
        changed_paths=sorted(set(normalized)),
        violations=sorted(set(violations)),
    )


def normalize_experiment_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or raw in {".", "./"}:
        raise ValueError(f"unsafe_experiment_path:{raw or '<empty>'}")
    normalized = path.as_posix().removeprefix("./")
    lowered = normalized.lower()
    if lowered in _FORBIDDEN_EXACT:
        raise ValueError(f"protected_experiment_path:{normalized}")
    if lowered.startswith(_PROTECTED_PREFIXES):
        raise ValueError(f"protected_experiment_path:{normalized}")
    if any(part.lower() in _FORBIDDEN_PARTS for part in path.parts):
        raise ValueError(f"forbidden_experiment_path:{normalized}")
    if lowered.startswith(_FORBIDDEN_PREFIXES):
        raise ValueError(f"forbidden_experiment_path:{normalized}")
    if lowered.endswith((".pyc", ".pfx", ".p12", ".pem", ".key")):
        raise ValueError(f"credential_or_generated_path:{normalized}")
    return normalized


def _run_git(project_root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail}")
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
