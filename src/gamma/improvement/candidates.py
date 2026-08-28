from __future__ import annotations

import ast
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..llm.base import LLMAdapter, LLMCallContext
from .experiments import (
    ExperimentManifest,
    _run_git,
    normalize_experiment_path,
    validate_candidate_scope,
    verify_experiment_workspace,
)
from .grounded_plans import (
    GroundedPlan,
    _grounding_sha256,
    _parse_json_object,
    validate_grounded_plan,
)
from .grounding import SourceGroundingReport


_MAXIMUM_EDITS = 12
_MAXIMUM_CHANGED_LINES = 400
_MAXIMUM_TOTAL_REPLACEMENT_CHARS = 200_000
_MAXIMUM_SOURCE_CONTEXT_LINES = 600
_MAXIMUM_SOURCE_CONTEXT_CHARS = 240_000


class CandidateEdit(BaseModel):
    path: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    old_text: str = Field(min_length=1, max_length=100_000)
    new_text: str = Field(max_length=150_000)

    @model_validator(mode="after")
    def validate_edit(self) -> "CandidateEdit":
        self.path = normalize_experiment_path(self.path)
        if self.old_text == self.new_text:
            raise ValueError("candidate edit does not change source")
        if "\x00" in self.old_text or "\x00" in self.new_text:
            raise ValueError("candidate edit contains a null byte")
        return self


class CandidateDraft(BaseModel):
    version: int = 1
    status: Literal["candidate", "needs_more_source"]
    manifest_id: str
    baseline_commit: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    grounding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale: str = Field(default="", max_length=3000)
    edits: tuple[CandidateEdit, ...] = ()
    provider: str | None = None
    model: str | None = None
    authority: Literal["candidate_draft_only"] = "candidate_draft_only"

    @model_validator(mode="after")
    def validate_status(self) -> "CandidateDraft":
        if self.status == "candidate" and not self.edits:
            raise ValueError("candidate draft requires at least one edit")
        if self.status == "needs_more_source" and self.edits:
            raise ValueError("needs_more_source cannot include edits")
        return self


class CandidateDraftRejection(BaseModel):
    code: str
    received_fields: tuple[str, ...] = ()


class CandidateDraftBatch(BaseModel):
    draft: CandidateDraft | None = None
    rejections: tuple[CandidateDraftRejection, ...] = ()


class CandidateFileReceipt(BaseModel):
    path: str
    before_sha256: str
    after_sha256: str


class CandidateApplicationReceipt(BaseModel):
    manifest_id: str
    baseline_commit: str
    plan_sha256: str
    grounding_sha256: str
    changed_paths: tuple[str, ...]
    files: tuple[CandidateFileReceipt, ...]
    diff_sha256: str
    authority: Literal["isolated_candidate_only"] = "isolated_candidate_only"


class CandidateDraftGenerator:
    """Ask a local model for bounded exact replacements; never write source itself."""

    def __init__(self, llm: LLMAdapter) -> None:
        self.llm = llm

    def generate(
        self,
        *,
        manifest: ExperimentManifest,
        plan: GroundedPlan,
        grounding: SourceGroundingReport,
        workspace: Path,
        model_override: str | None = None,
    ) -> CandidateDraftBatch:
        if manifest.status != "workspace_ready":
            raise ValueError("candidate drafting requires a workspace_ready experiment")
        verify_experiment_workspace(manifest, workspace)
        validate_grounded_plan(plan, grounding, project_root=workspace)
        if plan.status != "grounded_plan":
            raise ValueError("candidate drafting requires a grounded_plan")
        if plan.grounding_sha256 != _grounding_sha256(grounding):
            raise ValueError("grounded plan digest does not match source grounding")
        if not set(plan.allowed_paths).issubset(manifest.allowed_paths):
            raise ValueError("grounded plan paths exceed experiment manifest scope")

        context = {
            "experiment": {
                "id": manifest.id,
                "hypothesis": manifest.hypothesis,
                "domain": manifest.domain,
                "allowed_paths": plan.allowed_paths,
                "maximum_edits": min(_MAXIMUM_EDITS, manifest.maximum_changed_files),
                "maximum_changed_lines": _MAXIMUM_CHANGED_LINES,
            },
            "plan": {
                "mechanism_hypothesis": plan.mechanism_hypothesis,
                "validation_plan": plan.validation_plan,
                "risk_notes": plan.risk_notes,
            },
            "verified_source": _candidate_source_context(plan, workspace=workspace),
        }
        reply = self.llm.generate_reply(
            system_prompt=_candidate_system_prompt(),
            user_text=json.dumps(context, ensure_ascii=True, sort_keys=True),
            call_context=LLMCallContext(
                purpose="improvement_candidate_draft",
                reasoning_depth="heavy",
                persona_sensitive=False,
                interaction_mode="improvement",
                cost_sensitive=False,
                quality_tier="primary",
                minimum_context_tokens=8192,
            ),
            model_override=model_override,
        )
        route = (reply.metadata or {}).get("route") if isinstance(reply.metadata, dict) else {}
        route = route if isinstance(route, dict) else {}
        raw = _parse_json_object(reply.text)
        if raw is None:
            return CandidateDraftBatch(
                rejections=(CandidateDraftRejection(code="unparseable_response"),)
            )
        if isinstance(raw.get("candidate"), dict):
            raw = raw["candidate"]
        received_fields = tuple(
            sorted(key for key in raw if isinstance(key, str) and key.replace("_", "").isalnum())
        )
        status = str(raw.get("status") or "").strip().lower().replace("-", "_")
        if status in {"needs_source", "insufficient_source", "needs_more_context"}:
            status = "needs_more_source"
        try:
            draft = CandidateDraft.model_validate(
                {
                    "status": status,
                    "manifest_id": manifest.id,
                    "baseline_commit": manifest.baseline_commit,
                    "plan_sha256": candidate_plan_sha256(plan),
                    "grounding_sha256": _grounding_sha256(grounding),
                    "rationale": str(raw.get("rationale") or raw.get("reason") or "").strip(),
                    "edits": raw.get("edits") or (),
                    "provider": route.get("provider"),
                    "model": route.get("model"),
                }
            )
            validate_candidate_draft(
                draft,
                manifest=manifest,
                plan=plan,
                grounding=grounding,
                workspace=workspace,
            )
        except (SyntaxError, TypeError, ValueError) as exc:
            return CandidateDraftBatch(
                rejections=(
                    CandidateDraftRejection(
                        code=_candidate_rejection_code(exc),
                        received_fields=received_fields,
                    ),
                )
            )
        return CandidateDraftBatch(draft=draft)


def validate_candidate_draft(
    draft: CandidateDraft,
    *,
    manifest: ExperimentManifest,
    plan: GroundedPlan,
    grounding: SourceGroundingReport,
    workspace: Path,
) -> None:
    if draft.manifest_id != manifest.id or draft.baseline_commit != manifest.baseline_commit:
        raise ValueError("candidate manifest binding mismatch")
    if draft.plan_sha256 != candidate_plan_sha256(plan):
        raise ValueError("candidate plan binding mismatch")
    if draft.grounding_sha256 != _grounding_sha256(grounding):
        raise ValueError("candidate grounding binding mismatch")
    if draft.status == "needs_more_source":
        return
    if len(draft.edits) > min(_MAXIMUM_EDITS, manifest.maximum_changed_files):
        raise ValueError("candidate edit limit exceeded")
    if sum(max(edit.old_text.count("\n") + 1, edit.new_text.count("\n") + 1) for edit in draft.edits) > _MAXIMUM_CHANGED_LINES:
        raise ValueError("candidate changed-line limit exceeded")
    if sum(len(edit.old_text) + len(edit.new_text) for edit in draft.edits) > _MAXIMUM_TOTAL_REPLACEMENT_CHARS:
        raise ValueError("candidate replacement-size limit exceeded")

    fact_by_path = {fact.path: fact for fact in grounding.files}
    citations_by_path: dict[str, list[tuple[int, int]]] = {}
    for citation in plan.source_evidence:
        citations_by_path.setdefault(citation.path, []).append((citation.line_start, citation.line_end))
    changed_paths = [edit.path for edit in draft.edits]
    scope = validate_candidate_scope(manifest, changed_paths)
    if not scope.passed:
        raise ValueError("candidate scope violation:" + ",".join(scope.violations))

    edits_by_path: dict[str, list[tuple[int, int, CandidateEdit, str, str]]] = {}
    root = workspace.resolve()
    for edit in draft.edits:
        fact = fact_by_path.get(edit.path)
        if fact is None or edit.path not in plan.allowed_paths:
            raise ValueError(f"candidate path is not grounded:{edit.path}")
        path = root / edit.path
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != fact.sha256 or edit.file_sha256 != fact.sha256:
            raise ValueError(f"candidate source hash mismatch:{edit.path}")
        source = raw.decode("utf-8", errors="strict")
        old_text = _match_workspace_newlines(edit.old_text, source)
        new_text = _match_workspace_newlines(edit.new_text, source)
        if source.count(old_text) != 1:
            raise ValueError(f"candidate old_text must match exactly once:{edit.path}")
        start = source.index(old_text)
        end = start + len(old_text)
        start_line = source.count("\n", 0, start) + 1
        end_line = start_line + old_text.count("\n")
        if old_text.endswith(("\n", "\r\n")):
            end_line = max(start_line, end_line - 1)
        if not any(low <= start_line <= end_line <= high for low, high in citations_by_path.get(edit.path, [])):
            raise ValueError(f"candidate edit is outside cited source span:{edit.path}")
        edits_by_path.setdefault(edit.path, []).append((start, end, edit, old_text, new_text))

    for relative, edits in edits_by_path.items():
        spans = sorted((start, end) for start, end, _, _, _ in edits)
        if any(right_start < left_end for (_, left_end), (right_start, _) in zip(spans, spans[1:])):
            raise ValueError(f"candidate edits overlap:{relative}")
        source = (root / relative).read_bytes().decode("utf-8", errors="strict")
        candidate = source
        for start, end, _, _, new_text in sorted(edits, key=lambda item: item[0], reverse=True):
            candidate = candidate[:start] + new_text + candidate[end:]
        ast.parse(candidate, filename=relative)


def apply_candidate_draft(
    draft: CandidateDraft,
    *,
    manifest: ExperimentManifest,
    plan: GroundedPlan,
    grounding: SourceGroundingReport,
    workspace: Path,
) -> CandidateApplicationReceipt:
    if manifest.status != "workspace_ready":
        raise ValueError("candidate application requires a workspace_ready experiment")
    if draft.status != "candidate":
        raise ValueError("needs_more_source is not an applicable candidate")
    verify_experiment_workspace(manifest, workspace)
    validate_grounded_plan(plan, grounding, project_root=workspace)
    validate_candidate_draft(
        draft,
        manifest=manifest,
        plan=plan,
        grounding=grounding,
        workspace=workspace,
    )
    root = workspace.resolve()
    originals: dict[str, bytes] = {}
    replacements: dict[str, bytes] = {}
    for relative in sorted({edit.path for edit in draft.edits}):
        path = root / relative
        raw = path.read_bytes()
        source = raw.decode("utf-8", errors="strict")
        matching_edits: list[tuple[int, int, str]] = []
        for edit in (item for item in draft.edits if item.path == relative):
            old_text = _match_workspace_newlines(edit.old_text, source)
            new_text = _match_workspace_newlines(edit.new_text, source)
            start = source.index(old_text)
            matching_edits.append((start, start + len(old_text), new_text))
        candidate = source
        for start, end, new_text in sorted(matching_edits, reverse=True):
            candidate = candidate[:start] + new_text + candidate[end:]
        ast.parse(candidate, filename=relative)
        originals[relative] = raw
        replacements[relative] = candidate.encode("utf-8")

    try:
        for relative, content in replacements.items():
            _replace_file_atomically(root / relative, content)
        changed = tuple(
            line.strip().replace("\\", "/")
            for line in _run_git(root, ["diff", "--name-only", "--"]).splitlines()
            if line.strip()
        )
        scope = validate_candidate_scope(manifest, list(changed))
        if not scope.passed or set(changed) != set(replacements):
            raise ValueError("applied candidate diff escaped the validated edit set")
        _run_git(root, ["diff", "--check", "--"])
        diff = _run_git(root, ["diff", "--no-ext-diff", "--binary", "--"])
    except Exception:
        for relative, content in originals.items():
            _replace_file_atomically(root / relative, content)
        raise

    receipts = tuple(
        CandidateFileReceipt(
            path=relative,
            before_sha256=hashlib.sha256(originals[relative]).hexdigest(),
            after_sha256=hashlib.sha256(replacements[relative]).hexdigest(),
        )
        for relative in sorted(replacements)
    )
    return CandidateApplicationReceipt(
        manifest_id=manifest.id,
        baseline_commit=manifest.baseline_commit,
        plan_sha256=draft.plan_sha256,
        grounding_sha256=draft.grounding_sha256,
        changed_paths=tuple(sorted(changed)),
        files=receipts,
        diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    )


def candidate_plan_sha256(plan: GroundedPlan) -> str:
    return hashlib.sha256(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _candidate_source_context(plan: GroundedPlan, *, workspace: Path) -> list[dict[str, Any]]:
    root = workspace.resolve()
    result: list[dict[str, Any]] = []
    total_lines = 0
    total_chars = 0
    seen: set[tuple[str, int, int]] = set()
    for citation in plan.source_evidence:
        key = (citation.path, citation.line_start, citation.line_end)
        if key in seen:
            continue
        seen.add(key)
        lines = (root / citation.path).read_text(encoding="utf-8", errors="strict").splitlines()
        excerpt = "\n".join(lines[citation.line_start - 1 : citation.line_end])
        line_count = citation.line_end - citation.line_start + 1
        if total_lines + line_count > _MAXIMUM_SOURCE_CONTEXT_LINES:
            raise ValueError("candidate source context line limit exceeded")
        if total_chars + len(excerpt) > _MAXIMUM_SOURCE_CONTEXT_CHARS:
            raise ValueError("candidate source context size limit exceeded")
        result.append(
            {
                "path": citation.path,
                "file_sha256": citation.file_sha256,
                "symbol": citation.symbol,
                "line_start": citation.line_start,
                "line_end": citation.line_end,
                "exact_source_excerpt": excerpt,
            }
        )
        total_lines += line_count
        total_chars += len(excerpt)
    return result


def _match_workspace_newlines(value: str, source: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\r\n" in source and source.count("\n") == source.count("\r\n"):
        return normalized.replace("\n", "\r\n")
    return normalized


def _replace_file_atomically(path: Path, content: bytes) -> None:
    replacement = path.with_name(f".{path.name}.gamma-candidate-{uuid.uuid4().hex}.next")
    replacement.write_bytes(content)
    replacement.chmod(path.stat().st_mode)
    replacement.replace(path)


def _candidate_system_prompt() -> str:
    return (
        "You are Gamma's isolated candidate author. You have no tools and cannot edit files. "
        "Use only the supplied grounded plan and exact verified source excerpts. Return one JSON "
        "object with status candidate, rationale, and edits, or status needs_more_source with no "
        "edits. Every edit must contain path, file_sha256, old_text copied exactly from one supplied "
        "excerpt, and new_text. Keep the change minimal. Do not alter tests, scoring, safety, privacy, "
        "authentication, persona, deployment, local configuration, or runtime data. Do not add "
        "dependencies or cache generated replies. Do not use markdown."
    )


def _candidate_rejection_code(exc: Exception) -> str:
    if isinstance(exc, SyntaxError):
        return "candidate_syntax_error"
    detail = str(exc).lower()
    if "hash" in detail or "binding" in detail:
        return "stale_or_mismatched_source"
    if "scope" in detail or "not grounded" in detail or "outside cited" in detail:
        return "candidate_scope_violation"
    if "match exactly once" in detail or "overlap" in detail:
        return "ambiguous_candidate_edit"
    if "limit exceeded" in detail or "size limit" in detail:
        return "candidate_limit_exceeded"
    return "candidate_schema_validation_failed"
