from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from .models import ObservationReport
from .proposals import ImprovementProposal, ProposalBatch, _proposal_observation


_MEASUREMENT_TERMS = re.compile(
    r"\b(instrument|instrumentation|logging|log |profile|profiling|trace|tracing|measure|diagnos)",
    re.IGNORECASE,
)
_DIRECT_EFFECT_TERMS = re.compile(
    r"\b(reduce|lower|decrease|improve|speed up|accelerate|prevent|eliminate)\b",
    re.IGNORECASE,
)
_DETERMINISTIC_VALIDATION_TERMS = re.compile(
    r"\b(test|fixture|assert|verify|benchmark|compare|regression|replay)\b",
    re.IGNORECASE,
)
_UNBOUND_NUMBER = re.compile(r"(?<![A-Za-z_])(?:~\s*)?\d+(?:\.\d+)?%?")


class ProposalReview(BaseModel):
    proposal_index: int
    hypothesis_sha256: str
    model: str
    domain: str
    proposal_kind: Literal["measurement", "direct_change"]
    state: Literal["manifest_candidate", "needs_revision", "rejected"]
    score: int = Field(ge=0, le=100)
    target_metrics: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ConsensusCandidate(BaseModel):
    key: str
    domain: str
    proposal_kind: Literal["measurement", "direct_change"]
    target_metrics: tuple[str, ...]
    supporting_models: tuple[str, ...]
    support_count: int
    state: Literal["consensus", "single_model"]
    next_action: Literal["manifest_planning", "code_grounding"]
    proposal_hashes: tuple[str, ...]


class ProposalReviewReport(BaseModel):
    observation_sha256: str
    reviews: tuple[ProposalReview, ...]
    consensus: tuple[ConsensusCandidate, ...]
    manifest_candidate_count: int
    authority: Literal["review_only"] = "review_only"


def review_proposal_batches(
    batches: list[ProposalBatch],
    report: ObservationReport,
) -> ProposalReviewReport:
    expected_digest = hashlib.sha256(
        json.dumps(
            _proposal_observation(report),
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    reviews: list[ProposalReview] = []
    consensus_inputs: list[tuple[ProposalReview, ImprovementProposal]] = []
    global_index = 0
    for batch in batches:
        batch_matches = batch.observation_sha256 == expected_digest
        for proposal in batch.proposals:
            review = _review_one(
                proposal,
                report,
                proposal_index=global_index,
                observation_matches=batch_matches and proposal.observation_sha256 == expected_digest,
            )
            reviews.append(review)
            consensus_inputs.append((review, proposal))
            global_index += 1
    consensus = _consensus(consensus_inputs)
    return ProposalReviewReport(
        observation_sha256=expected_digest,
        reviews=tuple(reviews),
        consensus=tuple(consensus),
        manifest_candidate_count=sum(review.state == "manifest_candidate" for review in reviews),
    )


def _review_one(
    proposal: ImprovementProposal,
    report: ObservationReport,
    *,
    proposal_index: int,
    observation_matches: bool,
) -> ProposalReview:
    hypothesis_digest = hashlib.sha256(proposal.hypothesis.encode("utf-8")).hexdigest()
    model = proposal.model or proposal.provider or "unknown"
    measurement = bool(_MEASUREMENT_TERMS.search(proposal.hypothesis))
    direct_effect = bool(_DIRECT_EFFECT_TERMS.search(proposal.hypothesis))
    proposal_kind: Literal["measurement", "direct_change"] = (
        "measurement" if measurement else "direct_change"
    )
    reasons: list[str] = []
    warnings: list[str] = []
    score = 45
    if not observation_matches:
        reasons.append("observation_digest_mismatch")
    observed = {metric.metric_id: metric for metric in report.metrics}
    insufficient = [
        evidence.metric_id
        for evidence in proposal.evidence
        if not observed.get(evidence.metric_id) or not observed[evidence.metric_id].sufficient_data
    ]
    if insufficient:
        reasons.append("insufficient_observation_samples")
    else:
        score += 10
    validation_text = " ".join(proposal.validation_plan)
    if _DETERMINISTIC_VALIDATION_TERMS.search(validation_text):
        score += 15
    else:
        reasons.append("validation_plan_lacks_deterministic_check")
    if proposal.risk_notes:
        score += 10
    else:
        reasons.append("risk_analysis_missing")
    if len(proposal.allowed_paths) <= 2:
        score += 10
    else:
        warnings.append("broad_path_scope")
    if _UNBOUND_NUMBER.search(proposal.hypothesis) or _UNBOUND_NUMBER.search(proposal.rationale):
        warnings.append("model_restates_unbound_numeric_claim")
    else:
        score += 5
    if proposal.change_class.value == "restricted_operation":
        reasons.append("restricted_operation_is_manual_only")
        state: Literal["manifest_candidate", "needs_revision", "rejected"] = "rejected"
    elif measurement and direct_effect:
        reasons.append("instrumentation_does_not_directly_change_target_metric")
        score -= 35
        state = "needs_revision"
    elif proposal_kind == "direct_change":
        reasons.append("aggregate_only_proposal_requires_code_grounding")
        score -= 20
        state = "needs_revision"
    elif reasons:
        state = "needs_revision"
    else:
        score += 10
        state = "manifest_candidate"
    return ProposalReview(
        proposal_index=proposal_index,
        hypothesis_sha256=hypothesis_digest,
        model=model,
        domain=proposal.domain,
        proposal_kind=proposal_kind,
        state=state,
        score=max(0, min(100, score)),
        target_metrics=proposal.target_metrics,
        allowed_paths=proposal.allowed_paths,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _consensus(
    candidates: list[tuple[ProposalReview, ImprovementProposal]],
) -> list[ConsensusCandidate]:
    grouped: dict[
        tuple[str, str],
        list[tuple[ProposalReview, ImprovementProposal]],
    ] = defaultdict(list)
    for review, proposal in candidates:
        if review.state == "manifest_candidate":
            next_action = "manifest_planning"
        elif (
            review.state == "needs_revision"
            and set(review.reasons) == {"aggregate_only_proposal_requires_code_grounding"}
        ):
            next_action = "code_grounding"
        else:
            continue
        group_payload = {
            "domain": review.domain,
            "proposal_kind": review.proposal_kind,
            "target_metrics": sorted(review.target_metrics),
            "next_action": next_action,
        }
        key = hashlib.sha256(
            json.dumps(group_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        grouped[(key, next_action)].append((review, proposal))
    results: list[ConsensusCandidate] = []
    for (key, next_action), items in grouped.items():
        models = tuple(sorted({review.model for review, _ in items}))
        proposal_hashes = tuple(sorted(review.hypothesis_sha256 for review, _ in items))
        exemplar = items[0][0]
        results.append(
            ConsensusCandidate(
                key=key,
                domain=exemplar.domain,
                proposal_kind=exemplar.proposal_kind,
                target_metrics=tuple(sorted(exemplar.target_metrics)),
                supporting_models=models,
                support_count=len(models),
                state="consensus" if len(models) >= 2 else "single_model",
                next_action=next_action,  # type: ignore[arg-type]
                proposal_hashes=proposal_hashes,
            )
        )
    return sorted(
        results,
        key=lambda item: (-item.support_count, item.next_action, item.domain, item.target_metrics),
    )
