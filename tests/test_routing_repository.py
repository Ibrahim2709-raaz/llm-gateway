from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore, RoutingPolicyVersion
from app.routing.repository import (
    DatabaseRoutingEvidenceRepository,
    RoutingEvidenceError,
)


def query_result(values: list[object]) -> Mock:
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def policy() -> RoutingPolicyVersion:
    return RoutingPolicyVersion(
        id=uuid4(),
        version="policy-v2",
        status="active",
        classifier_version="task-rules-v1",
        score_version="scores-v2",
        strategy_weights={},
        minimum_quality={},
        minimum_sample_count=5,
        cold_start_mode="exclude",
    )


def score() -> ModelCapabilityScore:
    return ModelCapabilityScore(
        id=uuid4(),
        evaluation_run_id=uuid4(),
        provider="openai",
        model="model-1",
        score_version="scores-v2",
        task_category="coding",
        difficulty="medium",
        quality_score=Decimal("0.8"),
        sample_count=20,
        capabilities=["text_generation"],
        status="active",
    )


@pytest.mark.anyio
async def test_repository_loads_one_version_consistent_snapshot() -> None:
    active_policy = policy()
    active_score = score()
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        query_result([active_policy]),
        query_result([active_score]),
    ]

    snapshot = await DatabaseRoutingEvidenceRepository(session).load_active()

    assert snapshot.policy is active_policy
    assert snapshot.scores == (active_score,)
    assert session.execute.await_count == 2


@pytest.mark.anyio
async def test_repository_rejects_missing_or_multiple_active_policies() -> None:
    missing_session = AsyncMock(spec=AsyncSession)
    missing_session.execute.return_value = query_result([])
    with pytest.raises(RoutingEvidenceError) as missing:
        await DatabaseRoutingEvidenceRepository(missing_session).load_active()
    assert missing.value.code == "active_policy_missing"

    duplicate_session = AsyncMock(spec=AsyncSession)
    duplicate_session.execute.return_value = query_result([policy(), policy()])
    with pytest.raises(RoutingEvidenceError) as duplicate:
        await DatabaseRoutingEvidenceRepository(duplicate_session).load_active()
    assert duplicate.value.code == "multiple_active_policies"


@pytest.mark.anyio
async def test_repository_rejects_duplicate_score_coordinates() -> None:
    active_score = score()
    duplicate_score = score()
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        query_result([policy()]),
        query_result([active_score, duplicate_score]),
    ]

    with pytest.raises(RoutingEvidenceError) as error:
        await DatabaseRoutingEvidenceRepository(session).load_active()

    assert error.value.code == "duplicate_active_scores"
