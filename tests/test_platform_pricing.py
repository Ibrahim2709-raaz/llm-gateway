from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models import ProviderPricingProposal
from app.platform.pricing import (
    PricingProposalError,
    create_pricing_proposal,
    list_pricing_proposals,
    review_pricing_proposal,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def database_with_scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = value
    database = MagicMock()
    database.scalars = AsyncMock(return_value=result)
    database.flush = AsyncMock()
    return database


def proposal(*, status: str = "pending") -> ProviderPricingProposal:
    return ProviderPricingProposal(
        id=uuid4(),
        provider="openai",
        model="model-a",
        input_cost_per_million=Decimal("1.25"),
        output_cost_per_million=Decimal("5.5"),
        source_url="https://provider.example/pricing",
        rationale="Official provider price changed.",
        status=status,
        proposed_by_user_id=uuid4(),
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_create_pricing_proposal_normalizes_values() -> None:
    database = MagicMock()
    database.flush = AsyncMock()
    proposer = uuid4()

    created = await create_pricing_proposal(
        database,
        provider=" OpenAI ",
        model=" model-a ",
        input_cost_per_million=Decimal("1.25"),
        output_cost_per_million=Decimal("5.5"),
        source_url=" https://provider.example/pricing ",
        rationale=" Official provider price changed. ",
        proposed_by_user_id=proposer,
    )

    assert created.provider == "openai"
    assert created.model == "model-a"
    assert created.status == "pending"
    assert created.proposed_by_user_id == proposer
    database.add.assert_called_once_with(created)
    database.flush.assert_awaited_once()


@pytest.mark.anyio
async def test_list_pricing_proposals_orders_and_limits() -> None:
    item = proposal()
    result = MagicMock()
    result.all.return_value = [item]
    database = MagicMock()
    database.scalars = AsyncMock(return_value=result)

    assert await list_pricing_proposals(database, limit=25) == [item]
    database.scalars.assert_awaited_once()

    with pytest.raises(ValueError, match="between 1 and 200"):
        await list_pricing_proposals(database, limit=0)


@pytest.mark.anyio
async def test_review_pricing_proposal_requires_an_independent_reviewer() -> None:
    item = proposal()
    database = database_with_scalar_result(item)

    with pytest.raises(PricingProposalError) as error:
        await review_pricing_proposal(
            database,
            proposal_id=item.id,
            reviewer_user_id=item.proposed_by_user_id,
            decision="approved",
            review_note="Checked source.",
        )

    assert error.value.code == "independent_pricing_review_required"
    database.flush.assert_not_awaited()


@pytest.mark.anyio
async def test_review_pricing_proposal_records_decision() -> None:
    item = proposal()
    reviewer = uuid4()
    database = database_with_scalar_result(item)

    reviewed = await review_pricing_proposal(
        database,
        proposal_id=item.id,
        reviewer_user_id=reviewer,
        decision="approved",
        review_note=" Checked official price page. ",
        now=NOW,
    )

    assert reviewed.status == "approved"
    assert reviewed.reviewed_by_user_id == reviewer
    assert reviewed.review_note == "Checked official price page."
    assert reviewed.reviewed_at == NOW
    database.flush.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stored", "expected_code"),
    [
        (None, "pricing_proposal_not_found"),
        (proposal(status="approved"), "pricing_proposal_already_reviewed"),
    ],
)
async def test_review_pricing_proposal_rejects_unavailable_state(
    stored: ProviderPricingProposal | None, expected_code: str
) -> None:
    database = database_with_scalar_result(stored)

    with pytest.raises(PricingProposalError) as error:
        await review_pricing_proposal(
            database,
            proposal_id=uuid4(),
            reviewer_user_id=uuid4(),
            decision="rejected",
            review_note="Not current.",
        )

    assert error.value.code == expected_code
