from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedClient, authenticate_api_key
from app.db.session import get_database_session
from app.schemas.usage import UsageSummaryResponse
from app.usage.service import DatabaseUsageService

router = APIRouter(prefix="/v1/usage", tags=["Usage"])


@router.get("", response_model=UsageSummaryResponse, operation_id="getUsageSummary")
async def get_usage_summary(
    period: Annotated[str, Query(pattern=r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")],
    authenticated_client: Annotated[AuthenticatedClient, Depends(authenticate_api_key)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> UsageSummaryResponse:
    summary = await DatabaseUsageService(session).summary(authenticated_client.client_id, period)
    return UsageSummaryResponse(
        period=summary.period,
        request_count=summary.request_count,
        prompt_tokens=summary.prompt_tokens,
        completion_tokens=summary.completion_tokens,
        total_tokens=summary.total_tokens,
        cost_usd=float(summary.cost_usd),
        budget_usd=float(summary.budget_usd) if summary.budget_usd is not None else None,
        remaining_budget_usd=(
            float(summary.remaining_budget_usd)
            if summary.remaining_budget_usd is not None
            else None
        ),
    )
