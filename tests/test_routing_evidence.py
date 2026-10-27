from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.routing.evidence import evaluate_cold_start, evaluate_score_evidence

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def make_policy(
    *,
    score_version: str = "scores-v1",
    minimum_sample_count: int = 5,
    cold_start_mode: str = "static_profile",
) -> RoutingPolicyVersion:
    return RoutingPolicyVersion(
        version="policy-v1",
        status="active",
        classifier_version="classifier-v1",
        score_version=score_version,
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=minimum_sample_count,
        cold_start_mode=cold_start_mode,
    )


def make_score(**overrides: object) -> ModelCapabilityScore:
    values: dict[str, object] = {
        "evaluation_run_id": uuid4(),
        "provider": "example",
        "model": "example-1",
        "score_version": "scores-v1",
        "task_category": "coding",
        "difficulty": "medium",
        "quality_score": Decimal("0.8"),
        "sample_count": 20,
        "score_variance": Decimal("0.01"),
        "capabilities": ["text_generation", "structured_output"],
        "status": "active",
        "valid_from": NOW - timedelta(days=1),
        "valid_until": NOW + timedelta(days=30),
    }
    values.update(overrides)
    return ModelCapabilityScore(**values)


def test_active_current_score_with_enough_evidence_is_eligible() -> None:
    result = evaluate_score_evidence(
        make_score(),
        make_policy(),
        required_capabilities=frozenset({"structured_output"}),
        now=NOW,
    )

    assert result.eligible is True
    assert result.reason == "eligible"


@pytest.mark.parametrize(
    ("score_overrides", "policy_overrides", "required", "reason"),
    [
        ({"status": "draft"}, {}, frozenset(), "inactive"),
        ({"score_version": "old"}, {}, frozenset(), "score_version_mismatch"),
        ({"sample_count": 4}, {}, frozenset(), "insufficient_samples"),
        ({"valid_from": NOW + timedelta(seconds=1)}, {}, frozenset(), "not_yet_valid"),
        ({"valid_until": NOW}, {}, frozenset(), "expired"),
        ({}, {}, frozenset({"tool_use"}), "missing_capability"),
    ],
)
def test_ineligible_evidence_has_an_explicit_reason(
    score_overrides: dict[str, object],
    policy_overrides: dict[str, object],
    required: frozenset[str],
    reason: str,
) -> None:
    result = evaluate_score_evidence(
        make_score(**score_overrides),
        make_policy(**policy_overrides),
        required_capabilities=required,
        now=NOW,
    )

    assert result.eligible is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("mode", "explicit_model", "eligible", "use_static_profile", "reason"),
    [
        ("static_profile", False, True, True, "static_profile_fallback"),
        ("exclude", False, False, False, "cold_start_excluded"),
        ("explicit_only", False, False, False, "cold_start_requires_explicit_model"),
        ("explicit_only", True, True, True, "explicit_model_fallback"),
    ],
)
def test_cold_start_behavior_is_policy_controlled(
    mode: str,
    explicit_model: bool,
    eligible: bool,
    use_static_profile: bool,
    reason: str,
) -> None:
    result = evaluate_cold_start(make_policy(cold_start_mode=mode), explicit_model=explicit_model)

    assert result.eligible is eligible
    assert result.use_static_profile is use_static_profile
    assert result.reason == reason
