from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..config import PROJECT_ROOT


_GROUNDING_ROOT = "src/gamma/"
_FORBIDDEN_GROUNDING_PARTS = {".git", ".venv", "node_modules", "__pycache__"}


def normalize_grounding_path(value: str) -> str:
    """Normalize an immutable source-evidence path, not a candidate edit path."""
    raw = str(value or "").replace("\\", "/").strip()
    path = Path(raw)
    if (
        not raw
        or path.is_absolute()
        or ".." in path.parts
        or raw in {".", "./"}
    ):
        raise ValueError(f"unsafe_grounding_path:{raw or '<empty>'}")
    normalized = path.as_posix().removeprefix("./")
    lowered = normalized.lower()
    if not lowered.startswith(_GROUNDING_ROOT):
        raise ValueError(f"non_source_grounding_path:{normalized}")
    if any(part.lower() in _FORBIDDEN_GROUNDING_PARTS for part in path.parts):
        raise ValueError(f"forbidden_grounding_path:{normalized}")
    if lowered.endswith((".pyc", ".pfx", ".p12", ".pem", ".key")):
        raise ValueError(f"credential_or_generated_grounding_path:{normalized}")
    return normalized


def resolve_grounding_path(value: str, *, project_root: Path = PROJECT_ROOT) -> tuple[str, Path]:
    relative = normalize_grounding_path(value)
    root = project_root.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"grounding_path_outside_project:{relative}")
    return relative, path


class SymbolFact(BaseModel):
    qualified_name: str
    kind: Literal["class", "function", "method", "async_function", "async_method"]
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    calls: tuple[str, ...] = ()
    call_lines: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    timing_keys: tuple[str, ...] = ()


class SourceFileFact(BaseModel):
    path: str
    sha256: str
    byte_count: int
    line_count: int
    symbols: tuple[SymbolFact, ...]
    metric_reference_lines: dict[str, tuple[int, ...]]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = normalize_grounding_path(value)
        if not normalized.lower().endswith(".py"):
            raise ValueError(f"unsupported_source_type:{normalized}")
        return normalized


class SourceGroundingReport(BaseModel):
    version: int = 1
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    target_metrics: tuple[str, ...]
    files: tuple[SourceFileFact, ...]
    unavailable_paths: tuple[str, ...] = ()
    authority: Literal["read_only_source_grounding"] = "read_only_source_grounding"


def build_source_grounding(
    *,
    paths: tuple[str, ...],
    target_metrics: tuple[str, ...],
    project_root: Path = PROJECT_ROOT,
    maximum_files: int = 8,
    maximum_file_bytes: int = 750_000,
) -> SourceGroundingReport:
    if not paths:
        raise ValueError("source grounding requires at least one path")
    if not target_metrics:
        raise ValueError("source grounding requires at least one target metric")
    if len(paths) > maximum_files:
        raise ValueError(f"source grounding file limit exceeded:{len(paths)}>{maximum_files}")
    files: list[SourceFileFact] = []
    unavailable: list[str] = []
    for value in paths:
        relative, path = resolve_grounding_path(value, project_root=project_root)
        if not path.exists() or not path.is_file():
            unavailable.append(f"missing:{relative}")
            continue
        if path.suffix.lower() != ".py":
            unavailable.append(f"unsupported_source_type:{relative}")
            continue
        size = path.stat().st_size
        if size > maximum_file_bytes:
            unavailable.append(f"source_file_too_large:{relative}:{size}")
            continue
        raw = path.read_bytes()
        try:
            source = raw.decode("utf-8", errors="strict")
            tree = ast.parse(source, filename=relative)
        except (UnicodeDecodeError, SyntaxError) as exc:
            unavailable.append(f"unparseable_source:{relative}:{type(exc).__name__}")
            continue
        lines = source.splitlines()
        visitor = _SymbolVisitor()
        visitor.visit(tree)
        files.append(
            SourceFileFact(
                path=relative,
                sha256=hashlib.sha256(raw).hexdigest(),
                byte_count=size,
                line_count=len(lines),
                symbols=tuple(visitor.symbols[:300]),
                metric_reference_lines=_metric_reference_lines(lines, target_metrics),
            )
        )
    if not files:
        raise ValueError("source grounding found no readable Python source files")
    return SourceGroundingReport(
        target_metrics=tuple(dict.fromkeys(target_metrics)),
        files=tuple(files),
        unavailable_paths=tuple(unavailable),
    )


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[tuple[str, str]] = []
        self.symbols: list[SymbolFact] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(
            SymbolFact(
                qualified_name=self._qualified(node.name),
                kind="class",
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )
        self.stack.append((node.name, "class"))
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, asynchronous=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, asynchronous=True)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        asynchronous: bool,
    ) -> None:
        method = any(kind == "class" for _, kind in self.stack)
        kind: Literal["function", "method", "async_function", "async_method"]
        if method:
            kind = "async_method" if asynchronous else "method"
        else:
            kind = "async_function" if asynchronous else "function"
        call_lines: dict[str, list[int]] = {}
        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue
            call_name = _call_name(item.func)
            if call_name is None:
                continue
            call_lines.setdefault(call_name, []).append(item.lineno)
        timing_keys = sorted(
            {
                item.value
                for item in ast.walk(node)
                if isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and item.value.endswith("_ms")
                and len(item.value) <= 100
            }
        )
        self.symbols.append(
            SymbolFact(
                qualified_name=self._qualified(node.name),
                kind=kind,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                calls=tuple(sorted(call_lines)[:100]),
                call_lines={
                    call_name: tuple(sorted(set(line_numbers))[:20])
                    for call_name, line_numbers in sorted(call_lines.items())[:100]
                },
                timing_keys=tuple(timing_keys[:100]),
            )
        )
        self.stack.append((node.name, "function"))
        self.generic_visit(node)
        self.stack.pop()

    def _qualified(self, name: str) -> str:
        return ".".join([*(item[0] for item in self.stack), name])


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id if node.id.isidentifier() else None
    if isinstance(node, ast.Attribute):
        parts: list[str] = [node.attr]
        current = node.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        parts.reverse()
        if all(part.isidentifier() for part in parts):
            return ".".join(parts[-5:])
    return None


def _metric_reference_lines(
    lines: list[str],
    target_metrics: tuple[str, ...],
) -> dict[str, tuple[int, ...]]:
    matches: dict[str, tuple[int, ...]] = {}
    for metric_id in dict.fromkeys(target_metrics):
        leaf = metric_id.rsplit(".", 1)[-1]
        line_numbers = tuple(
            index
            for index, line in enumerate(lines, start=1)
            if metric_id in line or leaf in line
        )
        matches[metric_id] = line_numbers[:100]
    return matches
