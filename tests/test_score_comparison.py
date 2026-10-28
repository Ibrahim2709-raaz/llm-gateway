from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore
from app.evaluations.score_activation import compare_score_version


def score(
    version: str,
    status: str,
    quality: str,
    *,
    category: str = "coding",
    difficulty: str = "medium",
) -> ModelCapabilityScore:
    return ModelCapabilityScore(
        id=uuid4(),
        evaluation_run_id=uuid4(),
        provider="openai",
        model="model-1",
        score_version=version,
        task_category=category,
        difficulty=difficulty,
        quality_score=Decimal(quality),
        sample_count=20,
        score_variance=Decimal("0.01"),
        capabilities=["text_generation"],
        status=status,
    )


@pytest.mark.anyio
async def test_compare_candidate_with_active_scores_by_task() -> None:
    active = score("scores-v1", "active", "0.70")
    candidate = score("scores-v2", "draft", "0.82")
    result = Mock()
    result.scalars.return_value.all.return_value = [active, candidate]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    comparison = await compare_score_version(
        session,
        provider="openai",
        model="model-1",
        candidate_version="scores-v2",
    )

    assert comparison.active_version == "scores-v1"
    assert comparison.candidate_status == "draft"
    assert comparison.rows[0].candidate_quality == Decimal("0.82")
    assert comparison.rows[0].active_quality == Decimal("0.70")
    assert comparison.rows[0].quality_delta == Decimal("0.12")
    assert comparison.rows[0].candidate_samples == 20
    assert comparison.rows[0].capabilities == ("text_generation",)


@pytest.mark.anyio
async def test_compare_candidate_without_active_baseline_uses_null_delta() -> None:
    candidate = score("scores-v1", "draft", "0.75")
    result = Mock()
    result.scalars.return_value.all.return_value = [candidate]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    comparison = await compare_score_version(
        session,
        provider="openai",
        model="model-1",
        candidate_version="scores-v1",
    )

    assert comparison.active_version is None
    assert comparison.rows[0].active_quality is None
    assert comparison.rows[0].quality_delta is None
