from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelCapabilityScore, RoutingDecisionSnapshot, RoutingPolicyVersion
from app.providers.mock import MockProvider
from app.routing.repository import (
    DatabaseRoutingEvidenceRepository,
    RoutingEvidenceError,
)
from app.routing.service import ProviderRouter
from app.schemas.chat import ChatCompletionRequest, ChatMessage


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


@pytest.mark.anyio
async def test_repository_records_privacy_safe_decision_snapshot() -> None:
    session = AsyncMock(spec=AsyncSession)
    request = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Explain this clearly")]
    )
    plan = ProviderRouter((MockProvider(),)).plan(request)
    request_id = uuid4()
    client_id = uuid4()
    created_at = datetime(2026, 10, 29, 12, tzinfo=UTC)

    snapshot = await DatabaseRoutingEvidenceRepository(session).record_decision(
        request_id=request_id,
        client_id=client_id,
        plan=plan,
        created_at=created_at,
    )

    assert isinstance(snapshot, RoutingDecisionSnapshot)
    assert snapshot.request_id == request_id
    assert snapshot.client_id == client_id
    assert snapshot.selected_provider == "mock"
    assert snapshot.selected_model == "mock-1"
    assert snapshot.policy_version == "baseline-v1"
    assert snapshot.candidate_count == 1
    assert not hasattr(snapshot, "prompt")
    session.add.assert_called_once_with(snapshot)
    session.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_repository_lists_only_requested_client_decisions() -> None:
    session = AsyncMock(spec=AsyncSession)
    rows = Mock()
    rows.all.return_value = []
    session.scalars.return_value = rows
    repository = DatabaseRoutingEvidenceRepository(session)
    assert await repository.list_decisions(client_id=uuid4(), limit=25) == []
    session.scalars.assert_awaited_once()
    with pytest.raises(ValueError, match="between 1 and 200"):
        await repository.list_decisions(client_id=uuid4(), limit=201)
