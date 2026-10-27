from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore, ScoreSetActivation
from app.evaluations.score_activation import ScoreActivationError, change_active_score_set

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def score(version: str, status: str, *, samples: int = 10) -> ModelCapabilityScore:
    return ModelCapabilityScore(
        id=uuid4(),
        evaluation_run_id=uuid4(),
        provider="mock",
        model="mock-1",
        score_version=version,
        task_category="reasoning",
        difficulty="simple",
        quality_score=Decimal("0.8"),
        sample_count=samples,
        score_variance=Decimal("0.01"),
        capabilities=["text_generation"],
        status=status,
    )


def session_with(*scores: ModelCapabilityScore) -> AsyncMock:
    lock_result = Mock()
    score_result = Mock()
    score_result.scalars.return_value.all.return_value = list(scores)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [lock_result, score_result]
    return session


@pytest.mark.anyio
async def test_activate_score_set_retires_previous_and_writes_audit() -> None:
    previous = score("scores-v1", "active")
    target = score("scores-v2", "draft")
    target.evaluation_run_id = previous.evaluation_run_id
    session = session_with(previous, target)

    result = await change_active_score_set(
        session,
        provider="mock",
        model="mock-1",
        score_version="scores-v2",
        action="activate",
        actor="release-bot",
        reason="evaluation approved",
        valid_days=30,
        minimum_samples=5,
        now=NOW,
    )

    assert previous.status == "retired"
    assert target.status == "active"
    assert target.valid_from == NOW
    assert target.valid_until == NOW + timedelta(days=30)
    assert result.previous_score_version == "scores-v1"
    event = session.add.call_args.args[0]
    assert isinstance(event, ScoreSetActivation)
    assert event.action == "activate"
    assert event.actor == "release-bot"
    assert session.flush.await_count == 2


@pytest.mark.anyio
async def test_rollback_reactivates_retired_score_set() -> None:
    current = score("scores-v2", "active")
    target = score("scores-v1", "retired")
    session = session_with(current, target)

    result = await change_active_score_set(
        session,
        provider="mock",
        model="mock-1",
        score_version="scores-v1",
        action="rollback",
        actor="on-call",
        minimum_samples=5,
        now=NOW,
    )

    assert current.status == "retired"
    assert target.status == "active"
    assert result.action == "rollback"
    assert result.previous_score_version == "scores-v2"


@pytest.mark.anyio
async def test_activation_rejects_insufficient_samples_without_mutation() -> None:
    target = score("scores-v2", "draft", samples=4)
    session = session_with(target)

    with pytest.raises(ScoreActivationError) as error:
        await change_active_score_set(
            session,
            provider="mock",
            model="mock-1",
            score_version="scores-v2",
            action="activate",
            actor="release-bot",
            minimum_samples=5,
            now=NOW,
        )

    assert error.value.code == "insufficient_samples"
    assert target.status == "draft"
    session.add.assert_not_called()
