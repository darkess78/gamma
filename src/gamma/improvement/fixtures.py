from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, model_validator

from ..config import PROJECT_ROOT, settings
from ..safety.privacy_guard import review_private_info_output
from .evaluator import summarize


_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
_TYPOGRAPHY_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
)


class FixtureExpectation(BaseModel):
    max_words: int = Field(default=80, ge=1, le=1000)
    required_any: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ("```",)
    expected_safety_action: str | None = None
    require_route_success: bool = True


class ConversationFixture(BaseModel):
    id: str
    domain: str = "conversation"
    tags: tuple[str, ...] = ()
    user_text: str = Field(min_length=1, max_length=4000)
    fast_mode: bool = False
    expectation: FixtureExpectation = Field(default_factory=FixtureExpectation)

    @model_validator(mode="after")
    def validate_id(self) -> "ConversationFixture":
        if not _CASE_ID_RE.fullmatch(self.id):
            raise ValueError(f"invalid fixture id: {self.id!r}")
        return self


class FixtureCatalog(BaseModel):
    version: int = Field(ge=1)
    cases: tuple[ConversationFixture, ...]

    @model_validator(mode="after")
    def validate_unique_cases(self) -> "FixtureCatalog":
        case_ids = [case.id for case in self.cases]
        if not case_ids:
            raise ValueError("fixture catalog must contain at least one case")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fixture case ids must be unique")
        return self


class ConversationTransport(Protocol):
    def respond(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


class HttpConversationTransport:
    def __init__(self, *, base_url: str | None = None, timeout_seconds: float = 120.0) -> None:
        headers: dict[str, str] = {}
        if settings.api_auth_enabled and settings.api_bearer_token:
            headers["Authorization"] = f"Bearer {settings.api_bearer_token}"
        self._client = httpx.Client(
            base_url=(base_url or settings.shana_internal_base_url).rstrip("/"),
            headers=headers,
            timeout=timeout_seconds,
        )

    def respond(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/v1/conversation/respond", json=payload)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Shana evaluation response must be a JSON object")
        return result

    def close(self) -> None:
        self._client.close()


class FixtureRunReport(BaseModel):
    catalog_version: int
    run_id: str
    generated_at: str
    thermal_state: Literal["cold", "warm", "unknown"]
    case_count: int
    repetition_count: int
    result_count: int
    passed_count: int
    failed_count: int
    error_count: int
    pass_rate_percent: float
    total_latency: dict[str, float | int | None]
    output_runtime_dir: str


class FixtureRunner:
    """Run versioned fictional cases and emit sanitized evaluation artifacts."""

    def __init__(self, transport: ConversationTransport) -> None:
        self.transport = transport

    def run(
        self,
        *,
        catalog: FixtureCatalog,
        output_runtime_dir: Path,
        repetitions: int = 1,
        thermal_state: Literal["cold", "warm", "unknown"] = "unknown",
    ) -> FixtureRunReport:
        if not 1 <= repetitions <= 100:
            raise ValueError("repetitions must be between 1 and 100")
        output_runtime_dir.mkdir(parents=True, exist_ok=True)
        output_files = [
            output_runtime_dir / "fixture.results.jsonl",
            output_runtime_dir / "conversation.timings.jsonl",
            output_runtime_dir / "llm.routes.jsonl",
            output_runtime_dir / "fixture.report.json",
        ]
        if any(path.exists() for path in output_files):
            raise FileExistsError("fixture output directory already contains evaluation artifacts")

        generated_at = _utc_now()
        run_id = hashlib.sha256(
            f"{catalog.version}:{generated_at}:{thermal_state}".encode("utf-8")
        ).hexdigest()[:20]
        results: list[dict[str, Any]] = []
        conversation_records: list[dict[str, Any]] = []
        route_records: list[dict[str, Any]] = []
        for repetition in range(1, repetitions + 1):
            for case in catalog.cases:
                result, conversation_record, routes = self._run_case(
                    case=case,
                    run_id=run_id,
                    repetition=repetition,
                    thermal_state=thermal_state,
                )
                results.append(result)
                if conversation_record is not None:
                    conversation_records.append(conversation_record)
                route_records.extend(routes)

        _write_jsonl(output_files[0], results)
        _write_jsonl(output_files[1], conversation_records)
        _write_jsonl(output_files[2], route_records)
        latencies = [
            float(item["total_ms"])
            for item in results
            if isinstance(item.get("total_ms"), (int, float))
        ]
        latency_summary = summarize(latencies).model_dump()
        passed_count = sum(item["status"] == "passed" for item in results)
        failed_count = sum(item["status"] == "failed" for item in results)
        error_count = sum(item["status"] == "error" for item in results)
        report = FixtureRunReport(
            catalog_version=catalog.version,
            run_id=run_id,
            generated_at=generated_at,
            thermal_state=thermal_state,
            case_count=len(catalog.cases),
            repetition_count=repetitions,
            result_count=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            error_count=error_count,
            pass_rate_percent=round(100.0 * passed_count / len(results), 3),
            total_latency=latency_summary,
            output_runtime_dir=str(output_runtime_dir.resolve()),
        )
        output_files[3].write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report

    def _run_case(
        self,
        *,
        case: ConversationFixture,
        run_id: str,
        repetition: int,
        thermal_state: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
        started_at = time.perf_counter()
        timestamp = _utc_now()
        try:
            response = self.transport.respond(
                {
                    "user_text": case.user_text,
                    "session_id": f"evaluation-{run_id}-{case.id}-{repetition}",
                    "synthesize_speech": False,
                    "fast_mode": case.fast_mode,
                    "evaluation_mode": True,
                }
            )
        except Exception as exc:
            total_ms = round((time.perf_counter() - started_at) * 1000, 3)
            return (
                {
                    "timestamp": timestamp,
                    "run_id": run_id,
                    "case_id": case.id,
                    "domain": case.domain,
                    "tags": list(case.tags),
                    "repetition": repetition,
                    "thermal_state": thermal_state,
                    "status": "error",
                    "quality_status": "failed",
                    "safety_status": "failed",
                    "privacy_status": "passed",
                    "reliability_status": "failed",
                    "violations": [f"transport_error:{type(exc).__name__}"],
                    "response_word_count": 0,
                    "total_ms": total_ms,
                },
                None,
                [],
            )

        result = _evaluate_fixture_response(case, response)
        timing = response.get("timing_ms") if isinstance(response.get("timing_ms"), dict) else {}
        measured_total = round((time.perf_counter() - started_at) * 1000, 3)
        total_ms = timing.get("total_ms") if isinstance(timing.get("total_ms"), (int, float)) else measured_total
        result_record = {
            "timestamp": timestamp,
            "run_id": run_id,
            "case_id": case.id,
            "domain": case.domain,
            "tags": list(case.tags),
            "repetition": repetition,
            "thermal_state": thermal_state,
            **result,
            "total_ms": float(total_ms),
            "client_total_ms": measured_total,
        }
        conversation_record = {
            "timestamp": timestamp,
            "run_id": run_id,
            "case_id": case.id,
            "thermal_state": thermal_state,
            "timing_ms": {
                key: float(value)
                for key, value in timing.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
        }
        conversation_record["timing_ms"].setdefault("total_ms", float(total_ms))
        route_events = _sanitized_route_events(response, run_id=run_id, case_id=case.id, thermal_state=thermal_state)
        return result_record, conversation_record, route_events


def load_fixture_catalog(path: Path | None = None) -> FixtureCatalog:
    catalog_path = path or PROJECT_ROOT / "evaluations" / "improvement" / "conversation.toml"
    return FixtureCatalog.model_validate(tomllib.loads(catalog_path.read_text(encoding="utf-8")))


def _evaluate_fixture_response(case: ConversationFixture, response: dict[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    text = response.get("spoken_text") if isinstance(response.get("spoken_text"), str) else ""
    normalized_text = _normalize_invariant_text(text)
    word_count = len(text.split())
    if not text.strip():
        violations.append("empty_response")
    if word_count > case.expectation.max_words:
        violations.append("response_too_long")
    if case.expectation.required_any and not any(
        _normalize_invariant_text(value) in normalized_text for value in case.expectation.required_any
    ):
        violations.append("missing_required_content")
    if any(_normalize_invariant_text(value) in normalized_text for value in case.expectation.forbidden):
        violations.append("forbidden_content")

    privacy = review_private_info_output(text)
    privacy_status = "failed" if privacy.blocked else "passed"
    if privacy.blocked:
        violations.append("private_output")

    metadata = response.get("tts_metadata") if isinstance(response.get("tts_metadata"), dict) else {}
    speech_filter = metadata.get("speech_filter") if isinstance(metadata.get("speech_filter"), dict) else {}
    actual_action = str(speech_filter.get("action") or "")
    if case.expectation.expected_safety_action and actual_action != case.expectation.expected_safety_action:
        violations.append("unexpected_safety_action")
    elif not case.expectation.expected_safety_action and bool(speech_filter.get("blocked")):
        violations.append("unexpected_safety_block")
    safety_status = "failed" if any(item in violations for item in ("unexpected_safety_action", "unexpected_safety_block")) else "passed"

    if response.get("memory_candidates"):
        violations.append("evaluation_memory_candidate")
    if response.get("tool_calls") or response.get("tool_results"):
        violations.append("evaluation_tool_activity")
    if response.get("audio_path") or response.get("audio_content_type"):
        violations.append("unexpected_audio_artifact")

    timing = response.get("timing_ms") if isinstance(response.get("timing_ms"), dict) else {}
    route_events = metadata.get("evaluation_route_events") if isinstance(metadata.get("evaluation_route_events"), list) else []
    route_ok = any(
        isinstance(item, dict) and str(item.get("status")) in {"ok", "blocked"}
        for item in route_events
    )
    if not isinstance(timing.get("total_ms"), (int, float)):
        violations.append("missing_total_timing")
    if case.expectation.require_route_success and not route_ok:
        violations.append("missing_successful_route")
    reliability_status = "failed" if any(
        item in violations for item in ("missing_total_timing", "missing_successful_route")
    ) else "passed"
    quality_status = "failed" if any(
        item in violations for item in ("empty_response", "response_too_long", "missing_required_content", "forbidden_content")
    ) else "passed"
    status = "passed" if not violations else "failed"
    return {
        "status": status,
        "quality_status": quality_status,
        "safety_status": safety_status,
        "privacy_status": privacy_status,
        "reliability_status": reliability_status,
        "violations": sorted(set(violations)),
        "response_word_count": word_count,
    }


def _normalize_invariant_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_TYPOGRAPHY_TRANSLATION)
    return " ".join(normalized.casefold().split())


def _sanitized_route_events(
    response: dict[str, Any],
    *,
    run_id: str,
    case_id: str,
    thermal_state: str,
) -> list[dict[str, Any]]:
    metadata = response.get("tts_metadata") if isinstance(response.get("tts_metadata"), dict) else {}
    events = metadata.get("evaluation_route_events") if isinstance(metadata.get("evaluation_route_events"), list) else []
    allowed = {
        "timestamp",
        "purpose",
        "route_family",
        "provider",
        "model",
        "reason",
        "status",
        "duration_ms",
        "fallback_index",
        "interaction_mode",
    }
    return [
        {
            "run_id": run_id,
            "case_id": case_id,
            "thermal_state": thermal_state,
            **{key: value for key, value in event.items() if key in allowed},
        }
        for event in events
        if isinstance(event, dict)
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
