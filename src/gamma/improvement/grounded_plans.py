from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..config import PROJECT_ROOT
from ..llm.base import LLMAdapter, LLMCallContext
from .grounding import SourceGroundingReport, resolve_grounding_path
from .models import ObservationReport
from .proposals import ImprovementProposal, _proposal_observation


class SourceCitation(BaseModel):
    path: str
    file_sha256: str
    symbol: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class GroundedPlan(BaseModel):
    status: Literal["grounded_plan", "needs_more_source", "refuted"]
    mechanism_hypothesis: str = Field(default="", max_length=3000)
    source_evidence: tuple[SourceCitation, ...] = ()
    target_metrics: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    validation_plan: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    proposal_sha256: str
    grounding_sha256: str
    observation_sha256: str
    provider: str | None = None
    model: str | None = None
    authority: Literal["grounding_only"] = "grounding_only"

    @model_validator(mode="after")
    def validate_status(self) -> "GroundedPlan":
        if self.status in {"grounded_plan", "refuted"}:
            if len(self.mechanism_hypothesis.strip()) < 12:
                raise ValueError(f"{self.status} requires a mechanism hypothesis")
            if not self.source_evidence:
                raise ValueError(f"{self.status} requires source evidence")
        if self.status == "grounded_plan":
            if not self.validation_plan:
                raise ValueError("grounded plan requires a validation plan")
            if not self.risk_notes:
                raise ValueError("grounded plan requires risk notes")
        return self


class GroundedPlanRejection(BaseModel):
    code: str
    received_fields: tuple[str, ...] = ()


class GroundedPlanBatch(BaseModel):
    proposal_sha256: str
    grounding_sha256: str
    plans: tuple[GroundedPlan, ...]
    rejections: tuple[GroundedPlanRejection, ...] = ()


class GroundedPlanGenerator:
    """Ask a local model for source-cited plans; never grant tools or edit files."""

    def __init__(self, llm: LLMAdapter) -> None:
        self.llm = llm

    def generate(
        self,
        *,
        proposal: ImprovementProposal,
        grounding: SourceGroundingReport,
        observation: ObservationReport,
        model_override: str | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> GroundedPlanBatch:
        proposal_sha256 = hashlib.sha256(proposal.hypothesis.encode("utf-8")).hexdigest()
        grounding_sha256 = _grounding_sha256(grounding)
        observation_sha256 = hashlib.sha256(
            json.dumps(
                _proposal_observation(observation),
                ensure_ascii=True,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if proposal.observation_sha256 != observation_sha256:
            raise ValueError("proposal observation digest does not match current observation")
        if tuple(sorted(proposal.target_metrics)) != tuple(sorted(grounding.target_metrics)):
            raise ValueError("grounding target metrics do not match proposal")
        validate_grounding_current(grounding, project_root=project_root)
        context = {
            "proposal": {
                "hypothesis": proposal.hypothesis,
                "domain": proposal.domain,
                "target_metrics": proposal.target_metrics,
                "allowed_paths": proposal.allowed_paths,
                "evidence": [item.model_dump(mode="json") for item in proposal.evidence],
            },
            "source_facts": _relevant_source_facts(
                grounding,
                project_root=project_root,
            ),
        }
        reply = self.llm.generate_reply(
            system_prompt=_system_prompt(),
            user_text=json.dumps(context, ensure_ascii=True, sort_keys=True),
            call_context=LLMCallContext(
                purpose="improvement_source_grounding",
                reasoning_depth="heavy",
                persona_sensitive=False,
                interaction_mode="improvement",
                cost_sensitive=False,
                quality_tier="primary",
                minimum_context_tokens=4096,
            ),
            model_override=model_override,
        )
        raw = _parse_plan_json_object(reply.text)
        if raw is None:
            reply = self.llm.generate_reply(
                system_prompt=_repair_system_prompt(),
                user_text=json.dumps(context, ensure_ascii=True, sort_keys=True),
                call_context=LLMCallContext(
                    purpose="improvement_source_grounding_repair",
                    reasoning_depth="normal",
                    persona_sensitive=False,
                    interaction_mode="improvement",
                    cost_sensitive=False,
                    quality_tier="primary",
                    minimum_context_tokens=4096,
                ),
                model_override=model_override,
            )
            raw = _parse_plan_json_object(reply.text)
            if raw is None:
                return GroundedPlanBatch(
                    proposal_sha256=proposal_sha256,
                    grounding_sha256=grounding_sha256,
                    plans=(),
                    rejections=(GroundedPlanRejection(code="unparseable_response_after_repair"),),
                )
        route = (reply.metadata or {}).get("route") if isinstance(reply.metadata, dict) else {}
        route = route if isinstance(route, dict) else {}
        if isinstance(raw.get("plan"), dict):
            raw = raw["plan"]
        received_fields = tuple(
            sorted(
                key
                for key in raw
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", key)
            )
        )
        try:
            plan = GroundedPlan.model_validate(
                {
                    **_normalize_plan(raw),
                    "target_metrics": proposal.target_metrics,
                    "allowed_paths": proposal.allowed_paths,
                    "proposal_sha256": proposal_sha256,
                    "grounding_sha256": grounding_sha256,
                    "observation_sha256": observation_sha256,
                    "provider": route.get("provider"),
                    "model": route.get("model"),
                }
            )
            validate_grounded_plan(plan, grounding, project_root=project_root)
        except (ValueError, TypeError) as exc:
            return GroundedPlanBatch(
                proposal_sha256=proposal_sha256,
                grounding_sha256=grounding_sha256,
                plans=(),
                rejections=(
                    GroundedPlanRejection(
                        code=_rejection_code(exc),
                        received_fields=received_fields,
                    ),
                ),
            )
        return GroundedPlanBatch(
            proposal_sha256=proposal_sha256,
            grounding_sha256=grounding_sha256,
            plans=(plan,),
        )


def validate_grounding_current(
    grounding: SourceGroundingReport,
    *,
    project_root: Path = PROJECT_ROOT,
) -> None:
    for fact in grounding.files:
        relative, path = resolve_grounding_path(fact.path, project_root=project_root)
        if not path.exists() or not path.is_file():
            raise ValueError(f"grounding_source_missing:{relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != fact.sha256:
            raise ValueError(f"grounding_source_stale:{relative}")


def validate_grounded_plan(
    plan: GroundedPlan,
    grounding: SourceGroundingReport,
    *,
    project_root: Path = PROJECT_ROOT,
) -> None:
    validate_grounding_current(grounding, project_root=project_root)
    if plan.status == "needs_more_source":
        return
    if plan.status == "grounded_plan":
        mechanism_text = " ".join(
            [plan.mechanism_hypothesis, *plan.validation_plan]
        ).lower()
        if (
            "cache" in mechanism_text
            and any(
                marker in mechanism_text
                for marker in ("draft_reply", "draft reply", "response text", "stored reply")
            )
        ):
            raise ValueError("unsafe_grounded_mechanism:generated_response_cache")
    file_by_path = {fact.path: fact for fact in grounding.files}
    if not set(plan.allowed_paths).issubset(file_by_path):
        raise ValueError("grounded_plan_path_not_in_grounding")
    cited_paths: set[str] = set()
    for citation in plan.source_evidence:
        fact = file_by_path.get(citation.path)
        if fact is None:
            raise ValueError(f"grounded_citation_unknown_path:{citation.path}")
        if citation.file_sha256 != fact.sha256:
            raise ValueError(f"grounded_citation_hash_mismatch:{citation.path}")
        symbols = {symbol.qualified_name: symbol for symbol in fact.symbols}
        symbol = symbols.get(citation.symbol)
        if symbol is None:
            raise ValueError(f"grounded_citation_unknown_symbol:{citation.symbol}")
        if not (
            symbol.line_start <= citation.line_start <= citation.line_end <= symbol.line_end
        ):
            raise ValueError(f"grounded_citation_line_range_mismatch:{citation.symbol}")
        cited_paths.add(citation.path)
    if plan.status == "grounded_plan" and not set(plan.allowed_paths).issubset(cited_paths):
        raise ValueError("grounded_plan_missing_path_citation")


def _relevant_source_facts(
    grounding: SourceGroundingReport,
    *,
    project_root: Path,
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for fact in grounding.files:
        relative, source_path = resolve_grounding_path(
            fact.path,
            project_root=project_root,
        )
        lines = {
            line
            for values in fact.metric_reference_lines.values()
            for line in values
        }
        symbols = [
            symbol
            for symbol in fact.symbols
            if any(symbol.line_start <= line <= symbol.line_end for line in lines)
        ]
        symbols.sort(key=lambda item: (item.line_end - item.line_start, item.qualified_name))
        if not symbols:
            symbols = [
                symbol
                for symbol in fact.symbols
                if symbol.kind in {"function", "method", "async_function", "async_method"}
            ][:8]
        direct_callees = _direct_callee_symbols(
            relevant_symbols=symbols,
            all_symbols=fact.symbols,
            metric_lines=lines,
        )
        relevant.append(
            {
                "path": relative,
                "sha256": fact.sha256,
                "metric_reference_lines": fact.metric_reference_lines,
                "symbols": [symbol.model_dump(mode="json") for symbol in symbols[:12]],
                "directly_called_symbols": [
                    symbol.model_dump(mode="json") for symbol in direct_callees
                ],
                "verified_source_excerpts": _verified_source_excerpts(
                    source_path,
                    fact.metric_reference_lines,
                ),
                "verified_callee_excerpts": _verified_symbol_excerpts(
                    source_path,
                    direct_callees,
                ),
            }
        )
    return relevant


def _direct_callee_symbols(
    *,
    relevant_symbols: list[Any],
    all_symbols: tuple[Any, ...],
    metric_lines: set[int],
    context_lines: int = 35,
    maximum_symbols: int = 6,
) -> list[Any]:
    """Select same-scope callees invoked near a metric reference."""
    candidates: dict[str, tuple[int, Any]] = {}
    all_by_name = {symbol.qualified_name: symbol for symbol in all_symbols}
    for caller in relevant_symbols:
        scope = caller.qualified_name.rsplit(".", 1)[0] if "." in caller.qualified_name else ""
        for call_name, line_numbers in caller.call_lines.items():
            distances = [
                abs(line_number - metric_line)
                for line_number in line_numbers
                for metric_line in metric_lines
            ]
            if not distances or min(distances) > context_lines:
                continue
            leaf = call_name.rsplit(".", 1)[-1]
            qualified_name = f"{scope}.{leaf}" if scope else leaf
            callee = all_by_name.get(qualified_name)
            if callee is None or callee.qualified_name == caller.qualified_name:
                continue
            previous = candidates.get(callee.qualified_name)
            candidate = (min(distances), callee)
            if previous is None or candidate[0] < previous[0]:
                candidates[callee.qualified_name] = candidate
    ranked = sorted(
        candidates.values(),
        key=lambda item: (item[0], item[1].line_start, item[1].qualified_name),
    )
    return [symbol for _, symbol in ranked[:maximum_symbols]]


def _verified_source_excerpts(
    path: Path,
    metric_reference_lines: dict[str, tuple[int, ...]],
    *,
    context_lines: int = 35,
    maximum_windows: int = 4,
    maximum_total_lines: int = 240,
) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    centers: set[int] = set()
    for metric_lines in metric_reference_lines.values():
        for line in metric_lines:
            centers.add(line)
    windows: list[tuple[int, int]] = []
    for center in sorted(centers):
        start = max(1, center - context_lines)
        end = min(len(lines), center + context_lines)
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    excerpts: list[dict[str, Any]] = []
    remaining = maximum_total_lines
    for start, end in windows[:maximum_windows]:
        if remaining <= 0:
            break
        bounded_end = min(end, start + remaining - 1)
        text = "\n".join(
            f"{line_number}: {lines[line_number - 1]}"
            for line_number in range(start, bounded_end + 1)
        )
        excerpts.append(
            {
                "line_start": start,
                "line_end": bounded_end,
                "text": text,
            }
        )
        remaining -= bounded_end - start + 1
    return excerpts


def _verified_symbol_excerpts(
    path: Path,
    symbols: list[Any],
    *,
    maximum_symbols: int = 6,
    maximum_lines_per_symbol: int = 120,
    maximum_total_lines: int = 300,
) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    excerpts: list[dict[str, Any]] = []
    remaining = maximum_total_lines
    for symbol in symbols[:maximum_symbols]:
        if remaining <= 0:
            break
        line_start = symbol.line_start
        line_end = min(
            symbol.line_end,
            line_start + maximum_lines_per_symbol - 1,
            line_start + remaining - 1,
        )
        excerpts.append(
            {
                "symbol": symbol.qualified_name,
                "line_start": line_start,
                "line_end": line_end,
                "truncated": line_end < symbol.line_end,
                "text": "\n".join(
                    f"{line_number}: {lines[line_number - 1]}"
                    for line_number in range(line_start, line_end + 1)
                ),
            }
        )
        remaining -= line_end - line_start + 1
    return excerpts


def _grounding_sha256(grounding: SourceGroundingReport) -> str:
    payload = grounding.model_dump(mode="json", exclude={"generated_at"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _system_prompt() -> str:
    return (
        "You are Gamma's source-grounding analyst. You have no tools and may not edit anything. "
        "Use only the supplied source hashes, symbols, call names, timing keys, line ranges, and "
        "verified bounded source excerpts, including bounded same-file callee bodies selected near "
        "metric timers. The excerpts came from the pinned file hash and are not "
        "permission to request other files. "
        "Never propose caching generated draft or response text; conversational output depends on "
        "persona, memory, tools, state, and model nondeterminism. "
        "If verified facts contradict a required causal premise, return status refuted with "
        "mechanism_hypothesis explaining the contradiction and source_evidence proving it. "
        "If facts are insufficient, return status needs_more_source. Only return status "
        "grounded_plan for an actionable mechanism supported by the verified facts. Return "
        "one JSON object with status, "
        "mechanism_hypothesis, source_evidence, validation_plan, risk_notes, and confidence. "
        "Every source_evidence item must copy path, file_sha256, symbol, line_start, and line_end "
        "from the supplied facts. Do not claim a function body behavior that is not established by "
        "the facts. Do not include target_metrics or allowed_paths; Gamma binds those deterministically."
    )


def _repair_system_prompt() -> str:
    return (
        "Return exactly one valid JSON object and no other text. This is a formatting repair for "
        "Gamma's source-grounding analysis; you still have no tools and may use only the supplied "
        "context. Use status grounded_plan, refuted, or needs_more_source. Include "
        "mechanism_hypothesis, source_evidence, validation_plan, risk_notes, and confidence. Every "
        "source_evidence item must copy path, file_sha256, symbol, line_start, and line_end from the "
        "supplied source facts. If the facts do not establish an actionable and safely testable "
        "mechanism, return needs_more_source. Do not include markdown, analysis, or the input wrapper."
    )


def _normalize_plan(raw: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw)
    status = str(raw.get("status") or "").strip().lower().replace("-", "_")
    if status in {"needs_more_context", "insufficient_source", "needs_source"}:
        status = "needs_more_source"
    if status in {"hypothesis_refuted", "source_refuted", "disproved"}:
        status = "refuted"
    normalized["status"] = status
    normalized["mechanism_hypothesis"] = str(
        raw.get("mechanism_hypothesis") or raw.get("mechanism") or raw.get("hypothesis") or ""
    ).strip()
    normalized["source_evidence"] = raw.get("source_evidence") or raw.get("citations") or ()
    normalized["validation_plan"] = _string_tuple(
        raw.get("validation_plan") or raw.get("validation")
    )
    normalized["risk_notes"] = _string_tuple(raw.get("risk_notes") or raw.get("risks"))
    confidence = raw.get("confidence", 0.0)
    if isinstance(confidence, str):
        labels = {"low": 0.3, "medium": 0.5, "moderate": 0.5, "high": 0.8}
        confidence = labels.get(confidence.strip().lower(), confidence)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
    normalized["confidence"] = confidence
    return normalized


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple)) else [value]
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            item = next(
                (item.get(key) for key in ("step", "risk", "description", "text") if item.get(key)),
                None,
            )
        if isinstance(item, str):
            result.extend(line.strip().lstrip("-0123456789. ") for line in item.splitlines() if line.strip())
    return tuple(item for item in result if item)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        text.strip()[:1_000_000],
        flags=re.IGNORECASE,
    )
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for count, start in enumerate(index for index, char in enumerate(cleaned) if char == "{"):
            if count >= 200:
                break
            try:
                payload, _ = decoder.raw_decode(cleaned, start)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def _parse_plan_json_object(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$",
        "",
        text.strip()[:1_000_000],
        flags=re.IGNORECASE,
    )
    try:
        return _plan_shaped_object(json.loads(cleaned))
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for count, start in enumerate(index for index, char in enumerate(cleaned) if char == "{"):
            if count >= 200:
                break
            try:
                payload, _ = decoder.raw_decode(cleaned, start)
            except json.JSONDecodeError:
                continue
            selected = _plan_shaped_object(payload)
            if selected is not None:
                return selected
    return None


def _plan_shaped_object(value: Any) -> dict[str, Any] | None:
    """Select a plan object without accepting echoed source context as output."""
    if not isinstance(value, dict):
        return None
    candidates = [value]
    for key in ("plan", "result", "grounded_plan"):
        nested = value.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        if "status" in candidate:
            return candidate
    return None


def _rejection_code(exc: Exception) -> str:
    detail = str(exc).lower()
    if "unsafe_grounded_mechanism" in detail:
        return "unsafe_mechanism"
    if "grounding_source_stale" in detail:
        return "stale_source"
    if "citation" in detail:
        return "invalid_source_citation"
    if "path_not_in_grounding" in detail or "missing_path_citation" in detail:
        return "grounding_scope_mismatch"
    return "schema_validation_failed"
