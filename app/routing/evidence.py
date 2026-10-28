from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.routing.taxonomy import ColdStartMode

EvidenceReason = Literal[
    "eligible",
    "inactive",
    "score_version_mismatch",
    "insufficient_samples",
    "not_yet_valid",
    "expired",
    "missing_capability",
]


@dataclass(frozen=True, slots=True)
class EvidenceEligibility:
    eligible: bool
    reason: EvidenceReason


@dataclass(frozen=True, slots=True)
class ColdStartDecision:
    eligible: bool
    use_static_profile: bool
    reason: str


def evaluate_score_evidence(
    score: ModelCapabilityScore,
    policy: RoutingPolicyVersion,
    *,
    required_capabilities: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> EvidenceEligibility:
    evaluated_at = now or datetime.now(UTC)
    if score.status != "active":
        return EvidenceEligibility(False, "inactive")
    if score.score_version != policy.score_version:
        return EvidenceEligibility(False, "score_version_mismatch")
    if score.sample_count < policy.minimum_sample_count:
        return EvidenceEligibility(False, "insufficient_samples")
    if score.valid_from is None or score.valid_from > evaluated_at:
        return EvidenceEligibility(False, "not_yet_valid")
    if score.valid_until is not None and score.valid_until <= evaluated_at:
        return EvidenceEligibility(False, "expired")
    if not required_capabilities.issubset(score.capabilities):
        return EvidenceEligibility(False, "missing_capability")
    return EvidenceEligibility(True, "eligible")


def evaluate_cold_start(
    policy: RoutingPolicyVersion,
    *,
    explicit_model: bool,
) -> ColdStartDecision:
    mode = ColdStartMode(policy.cold_start_mode)
    if mode is ColdStartMode.STATIC_PROFILE:
        return ColdStartDecision(True, True, "static_profile_fallback")
    if mode is ColdStartMode.EXPLICIT_ONLY and explicit_model:
        return ColdStartDecision(True, True, "explicit_model_fallback")
    if mode is ColdStartMode.EXPLICIT_ONLY:
        return ColdStartDecision(False, False, "cold_start_requires_explicit_model")
    return ColdStartDecision(False, False, "cold_start_excluded")
