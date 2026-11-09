from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderPricingProposal


class PricingProposalError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def create_pricing_proposal(
    database: AsyncSession,
    *,
    provider: str,
    model: str,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
    source_url: str,
    rationale: str,
    proposed_by_user_id: UUID,
) -> ProviderPricingProposal:
    proposal = ProviderPricingProposal(
        provider=provider.strip().casefold(),
        model=model.strip(),
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
        source_url=source_url.strip(),
        rationale=rationale.strip(),
        status="pending",
        proposed_by_user_id=proposed_by_user_id,
    )
    database.add(proposal)
    await database.flush()
    return proposal


async def list_pricing_proposals(
    database: AsyncSession, *, limit: int = 100
) -> list[ProviderPricingProposal]:
    if not 1 <= limit <= 200:
        raise ValueError("Pricing proposal limit must be between 1 and 200")
    statement = (
        select(ProviderPricingProposal)
        .order_by(ProviderPricingProposal.created_at.desc(), ProviderPricingProposal.id)
        .limit(limit)
    )
    return list((await database.scalars(statement)).all())


async def review_pricing_proposal(
    database: AsyncSession,
    *,
    proposal_id: UUID,
    reviewer_user_id: UUID,
    decision: Literal["approved", "rejected"],
    review_note: str,
    now: datetime | None = None,
) -> ProviderPricingProposal:
    statement = (
        select(ProviderPricingProposal)
        .where(ProviderPricingProposal.id == proposal_id)
        .with_for_update()
    )
    proposal = (await database.scalars(statement)).one_or_none()
    if proposal is None:
        raise PricingProposalError("pricing_proposal_not_found", "Pricing proposal not found")
    if proposal.status != "pending":
        raise PricingProposalError(
            "pricing_proposal_already_reviewed", "Pricing proposal has already been reviewed"
        )
    if proposal.proposed_by_user_id == reviewer_user_id:
        raise PricingProposalError(
            "independent_pricing_review_required",
            "A different platform operator must review this proposal",
        )

    proposal.status = decision
    proposal.reviewed_by_user_id = reviewer_user_id
    proposal.review_note = review_note.strip()
    proposal.reviewed_at = now or datetime.now(UTC)
    await database.flush()
    return proposal
